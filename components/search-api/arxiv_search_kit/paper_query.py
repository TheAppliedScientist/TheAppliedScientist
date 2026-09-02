"""Query ArXiv papers using Google Gemini from their LaTeX source.

Supports both open-ended questions and summarization through a unified
:func:`query_paper` interface — summarization intent is detected automatically
from the query string.
"""

from __future__ import annotations

import gzip
import io
import logging
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx

from arxiv_search_kit.exceptions import DownloadError, SummarizationError
from arxiv_search_kit.models import Paper

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# LaTeX → Markdown conversion (via pandoc)
# ---------------------------------------------------------------------------

_PRESERVE_ENVS = [
    "equation", "equation*",
    "align", "align*",
    "alignat", "alignat*",
    "gather", "gather*",
    "multline", "multline*",
    "eqnarray", "eqnarray*",
    "flalign", "flalign*",
    "split",
    "tabular", "tabularx", "tabular*", "longtable", "tabulary",
    "table", "table*",
    "algorithm", "algorithm2e", "algorithmic", "algorithmic*",
]

_PLACEHOLDER_FMT = "XPLACEHOLDERX{idx}XPLACEHOLDERX"
_PLACEHOLDER_PAT = re.compile(r"XPLACEHOLDERX(\d+)XPLACEHOLDERX")


def _find_env_span(text: str, env: str, start: int = 0):
    begin_re = re.compile(r'\\begin\{' + re.escape(env) + r'\}')
    end_re = re.compile(r'\\end\{' + re.escape(env) + r'\}')
    m = begin_re.search(text, start)
    if not m:
        return -1, -1, ''
    depth = 1
    pos = m.end()
    while pos < len(text) and depth > 0:
        mb = begin_re.search(text, pos)
        me = end_re.search(text, pos)
        if me is None:
            break
        if mb and mb.start() < me.start():
            depth += 1
            pos = mb.end()
        else:
            depth -= 1
            if depth == 0:
                return m.start(), me.end(), text[m.start():me.end()]
            pos = me.end()
    return -1, -1, ''


def _extract_and_stash(tex: str) -> tuple[str, dict]:
    stash: dict[int, str] = {}
    idx = 0
    spans = []

    for m in re.finditer(r'\\\[.*?\\\]', tex, flags=re.DOTALL):
        spans.append((m.start(), m.end(), m.group(0), 'displaymath'))

    for env in _PRESERVE_ENVS:
        pos = 0
        while True:
            b, e, block = _find_env_span(tex, env, pos)
            if b == -1:
                break
            spans.append((b, e, block, env))
            pos = e

    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))

    accepted: list[tuple] = []
    for span in spans:
        b, e = span[0], span[1]
        if any(b >= ab and e <= ae for ab, ae, *_ in accepted):
            continue
        accepted.append(span)

    accepted.sort(key=lambda s: s[0], reverse=True)
    for b, e, block, env in accepted:
        stash[idx] = block
        placeholder = _PLACEHOLDER_FMT.format(idx=idx)
        tex = tex[:b] + f"\n\n{placeholder}\n\n" + tex[e:]
        idx += 1

    return tex, stash


def _restore_stash(md: str, stash: dict) -> str:
    def replacer(m: re.Match) -> str:
        i = int(m.group(1))
        block = stash.get(i, m.group(0))
        return f"\n\n```latex\n{block.strip()}\n```\n\n"
    return _PLACEHOLDER_PAT.sub(replacer, md)


def _run_pandoc(tex_source: str, timeout: float = 30) -> str:
    with tempfile.NamedTemporaryFile(suffix=".tex", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(tex_source)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["pandoc", tmp_path, "-f", "latex", "-t", "markdown",
             "--wrap=none", "--markdown-headings=atx"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0 and not result.stdout:
            raise RuntimeError(f"pandoc error: {result.stderr}")
        return result.stdout
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _postprocess_md(md: str) -> str:
    md = re.sub(r'^:::\s*\{[^}]*\}\s*$', '', md, flags=re.MULTILINE)
    md = re.sub(r'^:::\s*\w*\s*$', '', md, flags=re.MULTILINE)
    md = re.sub(r'```\{=latex\}.*?```', '', md, flags=re.DOTALL)

    parts = re.split(r'(```.*?```)', md, flags=re.DOTALL)
    clean_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            clean_parts.append(part)
        else:
            part = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})*', '', part)
            part = re.sub(r'(?<!\$)\{(?!\{)([^{}]*)\}(?!\})', r'\1', part)
            clean_parts.append(part)
    md = ''.join(clean_parts)

    def clean_cite(m: re.Match) -> str:
        keys = [k.strip().lstrip('@') for k in m.group(1).split(';')]
        return '[' + ', '.join(keys) + ']'
    md = re.sub(r'\[@([^\]]+)\]', clean_cite, md)
    md = re.sub(r'(?<!\[)@([a-zA-Z][a-zA-Z0-9_:]*)', r'[\1]', md)

    md = re.sub(r'(#{1,6} .+?)\s*\{#[^}]+\}', r'\1', md)
    md = re.sub(r'(#{1,6} [^#\n]+?)\s+#\w[\w:]*\s*$', r'\1', md, flags=re.MULTILINE)
    md = re.sub(r'^\s*\d+(?:\.\d+)?(?:pt|em|ex|cm|mm|in|bp|pc)\s*$', '', md, flags=re.MULTILINE)
    md = re.sub(r'\\([`*_{}[\]()#+\-.!|])', r'\1', md)

    lines = md.splitlines()
    md = '\n'.join(
        line for line in lines
        if not re.match(r'^\s*\\[a-zA-Z]+\*?\s*$', line.strip())
    )
    md = re.sub(r'\n{4,}', '\n\n\n', md)
    return md.strip()


def _pandoc_available() -> bool:
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _tex_to_markdown(tex: str) -> str:
    """Convert LaTeX source to clean Markdown via pandoc.

    Stashes math/table environments as placeholders before conversion so they
    are preserved verbatim, then restores them as fenced ```latex blocks.
    Falls back to the raw (cleaned) tex string if pandoc is not available
    or if pandoc fails for any reason (malformed LaTeX, OOM, etc.).
    """
    if not _pandoc_available():
        logger.warning("pandoc not found — passing raw LaTeX to LLM. "
                       "Install pandoc for cleaner results: apt install pandoc")
        return tex

    try:
        modified_tex, stash = _extract_and_stash(tex)
        raw_md = _run_pandoc(modified_tex)
        md = _postprocess_md(raw_md)
        md = _restore_stash(md, stash)
        md = re.sub(r'\n{4,}', '\n\n\n', md)
        return md.strip()
    except Exception as e:
        logger.warning("pandoc conversion failed, falling back to raw LaTeX: %s", e)
        return tex


ARXIV_SOURCE_URL = "https://arxiv.org/e-print/{arxiv_id}"


def _fetch_source(arxiv_id: str, timeout: float = 60.0) -> bytes:
    """Download the e-print source archive as raw bytes."""
    url = ARXIV_SOURCE_URL.format(arxiv_id=arxiv_id)
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPStatusError as e:
        raise DownloadError(f"ArXiv returned {e.response.status_code} for {url}") from e
    except Exception as e:
        raise DownloadError(f"Source download failed for {url}: {e}") from e


def _find_primary_tex(archive_bytes: bytes) -> str:
    """Extract the primary .tex file content from an e-print archive.

    Tries tar.gz first, then plain gzip (single-file submissions).
    The primary file is identified by looking for ``\\documentclass``.
    """
    # Try as tar.gz
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            tex_files: dict[str, str] = {}
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith(".tex"):
                    f = tar.extractfile(member)
                    if f is not None:
                        content = f.read().decode("utf-8", errors="replace")
                        # store by basename and full name for lookup
                        tex_files[member.name] = content
                        tex_files[Path(member.name).name] = content
                        tex_files[Path(member.name).stem] = content

            if not tex_files:
                raise SummarizationError("No .tex files found in source archive")

            # Find the primary file (the one with \documentclass)
            primary = None
            for name, content in tex_files.items():
                if r"\documentclass" in content:
                    primary = content
                    logger.info("Primary tex file: %s", name)
                    break

            if primary is None:
                # Fallback: largest .tex file
                by_size = sorted(tex_files.items(), key=lambda x: len(x[1]), reverse=True)
                primary = by_size[0][1]
                logger.info("No \\documentclass found, using largest tex file")

            # Inline \input{} and \include{} from the archive
            def inline_inputs(tex: str, depth: int = 0) -> str:
                if depth > 5:
                    return tex
                def replacer(m: re.Match) -> str:
                    fname = m.group(1).strip()
                    for key in (fname, fname + ".tex", Path(fname).name, Path(fname).stem):
                        if key in tex_files:
                            return inline_inputs(tex_files[key], depth + 1)
                    return m.group(0)  # leave as-is if not found
                return re.sub(r'\\(?:input|include)\{([^}]+)\}', replacer, tex)

            return inline_inputs(primary)
    except tarfile.TarError:
        pass

    # Try as plain gzip (single-file submissions)
    try:
        content = gzip.decompress(archive_bytes).decode("utf-8", errors="replace")
        if r"\documentclass" in content or r"\begin{document}" in content:
            logger.info("Single-file gzip submission")
            return content
    except Exception:
        pass

    raise SummarizationError("Could not extract .tex content from source archive")


def _trim_after_conclusion(tex: str) -> str:
    """Trim the tex content to keep everything up to and including the conclusion section.

    Removes appendices, acknowledgements after conclusion, bibliography, etc.
    """
    # Common patterns for conclusion sections (case insensitive)
    conclusion_patterns = [
        r"\\section\*?\{[^}]*[Cc]onclusion[^}]*\}",
        r"\\section\*?\{[^}]*[Ss]ummary[^}]*\}",
        r"\\section\*?\{[^}]*[Cc]oncluding [Rr]emarks[^}]*\}",
    ]

    conclusion_start = -1
    for pattern in conclusion_patterns:
        match = re.search(pattern, tex)
        if match:
            conclusion_start = match.start()
            break

    if conclusion_start == -1:
        # No conclusion found — return full content (minus bibliography)
        bib_match = re.search(r"\\bibliography\{|\\begin\{thebibliography\}", tex)
        if bib_match:
            return tex[:bib_match.start()].rstrip()
        return tex

    # Find the next \section or \appendix or \bibliography after conclusion
    after_conclusion = tex[conclusion_start:]
    next_section = re.search(
        r"\n\\(?:section|appendix|bibliography|begin\{thebibliography\}|end\{document\})",
        after_conclusion[1:],  # skip the conclusion \section itself
    )

    if next_section:
        end_pos = conclusion_start + 1 + next_section.start()
        return tex[:end_pos].rstrip()

    return tex


def _clean_tex(tex: str) -> str:
    """Light cleaning of LaTeX source to reduce token usage while preserving content."""
    # Strip preamble — keep only \begin{document}...\end{document} body
    begin_match = re.search(r"\\begin\{document\}", tex)
    if begin_match:
        tex = tex[begin_match.end():]
    end_match = re.search(r"\\end\{document\}", tex)
    if end_match:
        tex = tex[:end_match.start()]
    # Remove comments (lines starting with %)
    tex = re.sub(r"(?m)^%.*$", "", tex)
    # Remove inline comments (but not escaped \%)
    tex = re.sub(r"(?<!\\)%.*$", "", tex, flags=re.MULTILINE)
    # Strip preamble commands that pandoc can't handle (parametrized macros with #1)
    tex = re.sub(r"\\newcolumntype\{[^}]+\}.*", "", tex)
    tex = re.sub(r"\\(?:newcommand|renewcommand|def)\\[a-zA-Z]+.*?#\d.*", "", tex)
    # Collapse multiple blank lines
    tex = re.sub(r"\n{3,}", "\n\n", tex)
    return tex.strip()


QUERY_SYSTEM_PROMPT = """\
You are an expert academic researcher. You will be given the content of a research paper \
in Markdown format and a request from the user. Respond to the request accurately and in detail, \
grounding every claim in the paper's content. If the request asks for a summary, produce a \
structured summary. If it asks a specific question, answer it thoroughly citing sections, \
equations, tables, or numbers where relevant. Do not speculate beyond what the paper says."""

QUERY_USER_PROMPT = """\
{system_prompt}

---

{query}

Paper content (Markdown):

{md_content}
"""


def _generate_query_answer(user_prompt: str, api_key: str, model: str, timeout: float) -> str:
    """Call the configured LLM protocol without changing the reference Gemini path."""
    import os

    api_format = os.environ.get("SEARCH_QUERY_API_FORMAT", "gemini").lower()
    base_url = os.environ.get("SEARCH_QUERY_BASE_URL", "").rstrip("/")

    if api_format == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(text=QUERY_SYSTEM_PROMPT),
                        types.Part(text=user_prompt),
                    ],
                ),
            ],
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="medium"),
            ),
        )
        return response.text or ""

    import httpx

    if api_format == "openai":
        if not base_url:
            base_url = "https://api.openai.com/v1"
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": QUERY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"] or ""

    if api_format == "anthropic":
        if not base_url:
            base_url = "https://api.anthropic.com"
        response = httpx.post(
            f"{base_url}/v1/messages",
            headers={
                "x-api-key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "system": QUERY_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")

    raise SummarizationError(
        f"Unsupported SEARCH_QUERY_API_FORMAT={api_format!r}; use gemini, anthropic, or openai"
    )


def _query_one(arxiv_id: str, query: str, api_key: str, model: str, timeout: float) -> str:
    """Run a single paper query. Assumes the provider configuration is valid."""
    logger.info("Downloading source for %s ...", arxiv_id)
    archive_bytes = _fetch_source(arxiv_id, timeout=timeout)
    tex_content = _find_primary_tex(archive_bytes)
    tex_content = _trim_after_conclusion(tex_content)
    tex_content = _clean_tex(tex_content)
    md_content = _tex_to_markdown(tex_content)

    logger.info("Querying %s (%d chars md): %s", arxiv_id, len(md_content), query[:80])
    user_prompt = QUERY_USER_PROMPT.format(
        system_prompt=QUERY_SYSTEM_PROMPT, query=query, md_content=md_content,
    )

    try:
        result = _generate_query_answer(user_prompt, api_key, model, timeout)
    except Exception as e:
        raise SummarizationError(f"Paper-query LLM call failed: {e}") from e

    if not result:
        raise SummarizationError("Paper-query LLM returned an empty response")

    logger.info("Response generated for %s (%d chars)", arxiv_id, len(result))
    return result


def query_paper(
    paper: Paper | str | list[Paper] | list[str],
    query: str,
    api_key: str | None = None,
    model: str = "gemini-3-flash-preview",
    max_concurrent: int = 5,
    timeout: float = 120.0,
) -> str | dict[str, str]:
    """Query one or more papers with any natural language input.

    Accepts a single paper or a list. For lists, papers are queried in parallel.
    Downloads each paper's LaTeX source, converts it to clean Markdown, and passes
    the query + content to the configured LLM. The LLM handles any intent naturally —
    summarization, specific questions, comparisons, etc.

    Args:
        paper: Paper object, ArXiv ID string, or a list of either.
        query: Any natural language query — e.g. "summarize this paper",
               "what datasets were used?", "explain the loss function".
        api_key: Provider key. Falls back to SEARCH_QUERY_API_KEY or a standard
            provider key selected by SEARCH_QUERY_API_FORMAT.
        model: Model name; SEARCH_QUERY_MODEL overrides it when set.
        max_concurrent: Max parallel requests when given a list of papers.
        timeout: HTTP timeout for source download per paper.

    Returns:
        Response string for a single paper, or a dict mapping ArXiv ID to
        response string for multiple papers. Failed papers in a batch are
        logged and omitted from the dict.

    Raises:
        SummarizationError: If the API call fails (single paper).
        DownloadError: If source download fails (single paper).
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    api_format = os.environ.get("SEARCH_QUERY_API_FORMAT", "gemini").lower()
    if api_format == "gemini":
        try:
            from google import genai  # noqa: F401
        except ImportError:
            raise SummarizationError(
                "google-genai is required for SEARCH_QUERY_API_FORMAT=gemini."
            )
    api_key = (
        api_key
        or os.environ.get("SEARCH_QUERY_API_KEY")
        or (os.environ.get("GEMINI_API_KEY") if api_format == "gemini" else "")
        or (os.environ.get("ANTHROPIC_AUTH_TOKEN") if api_format == "anthropic" else "")
        or (os.environ.get("OPENAI_API_KEY") if api_format == "openai" else "")
    )
    if not api_key:
        raise SummarizationError(
            f"API key required for SEARCH_QUERY_API_FORMAT={api_format}. "
            "Set SEARCH_QUERY_API_KEY or the provider's standard key variable."
        )

    model = os.environ.get("SEARCH_QUERY_MODEL", model)

    if not isinstance(paper, list):
        arxiv_id = paper.arxiv_id if isinstance(paper, Paper) else paper
        return _query_one(arxiv_id, query, api_key, model, timeout)

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(
                _query_one,
                p.arxiv_id if isinstance(p, Paper) else p,
                query, api_key, model, timeout,
            ): (p.arxiv_id if isinstance(p, Paper) else p)
            for p in paper
        }
        for future in as_completed(futures):
            aid = futures[future]
            try:
                results[aid] = future.result()
            except Exception as e:
                logger.warning("Failed to query %s: %s", aid, e)

    return results


async def async_query_paper(
    paper: Paper | str | list[Paper] | list[str],
    query: str,
    api_key: str | None = None,
    model: str = "gemini-3-flash-preview",
    max_concurrent: int = 5,
    timeout: float = 120.0,
) -> str | dict[str, str]:
    """Async variant of :func:`query_paper`."""
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: query_paper(
            paper, query, api_key=api_key, model=model,
            max_concurrent=max_concurrent, timeout=timeout,
        )
    )
