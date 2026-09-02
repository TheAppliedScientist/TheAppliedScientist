"""
latex_to_markdown.py — Convert LaTeX paper to clean Markdown via Pandoc

Strategy:
  1. Inline all \\input{}/\\include{} so the full paper is in one string
  2. Trim to Conclusion on the raw LaTeX (drop References/Appendix before pandoc)
  3. Inject dummy \\newcommand definitions for any undefined single-letter math macros
     that would cause pandoc parse errors (e.g. \\I, \\R, \\N, \\E, \\P, \\Z)
  4. Stash math/table environments as placeholders so pandoc only sees prose
  5. Run pandoc; on failure fall back to a regex-based LaTeX stripper
  6. Post-process pandoc output; restore stashed math as ```latex blocks

Requires: pandoc >= 2.0  (apt install pandoc)

Usage:
    python latex_to_markdown.py paper.tex
    python latex_to_markdown.py paper.tex --output paper.md
    python latex_to_markdown.py paper.tex --stdout
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def inline_inputs(tex: str, base_dir: Path, depth: int = 0) -> str:
    """Recursively inline \\input{file} and \\include{file} directives."""
    if depth > 10:
        return tex

    def replace_input(m):
        fname = m.group(1).strip()
        if not fname.endswith('.tex'):
            fname += '.tex'
        fpath = base_dir / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8", errors="replace")
            return inline_inputs(content, base_dir, depth + 1)
        return ''

    tex = re.sub(r'\\input\{([^}]+)\}', replace_input, tex)
    tex = re.sub(r'\\include\{([^}]+)\}', replace_input, tex)
    return tex


def trim_to_conclusion(tex: str) -> str:
    """Cut the LaTeX source after the Conclusion section.

    Works on the raw (inlined) LaTeX body. Finds \\section{Conclusion},
    then drops everything from the next \\section / \\begin{thebibliography}
    / \\appendix onward.
    """
    # Pattern for any section-level command that starts References/Appendix/Bibliography
    STOP_PAT = re.compile(
        r'\\(?:section|chapter)\*?\{(?:References|Bibliography|Appendix|Acknowledgements?)\b'
        r'|\\begin\{thebibliography\}'
        r'|\\appendix\b',
        re.IGNORECASE,
    )

    conclusion_m = re.search(
        r'\\(?:section|chapter)\*?\{Conclusions?\}',
        tex, re.IGNORECASE,
    )

    if conclusion_m:
        # Keep through end of Conclusion section = next \section or stop pattern
        after_conclusion = tex[conclusion_m.start():]
        next_section = re.search(
            r'\n\\(?:section|chapter)\*?\{(?!Conclusions?)',
            after_conclusion, re.IGNORECASE,
        )
        stop = re.search(STOP_PAT, after_conclusion)

        cuts = [x.start() for x in [next_section, stop] if x]
        end = conclusion_m.start() + min(cuts) if cuts else len(tex)
        return tex[:end]
    else:
        # No conclusion found — just drop from References/Bibliography/Appendix
        stop = re.search(STOP_PAT, tex)
        return tex[:stop.start()] if stop else tex


# Undefined single-letter/common math macros that pandoc can't resolve
_UNDEF_MATH_MACROS = [
    'I', 'R', 'N', 'Z', 'E', 'P', 'Q', 'C', 'F', 'G', 'H', 'K', 'L', 'M',
    'T', 'V', 'W', 'X', 'Y', 'D', 'J', 'U', 'S',
]

def strip_problematic_envs(tex: str) -> str:
    """Remove figure/wrapfigure environments and bare \\caption{} commands.

    These contain complex inline math and undefined macros that cause pandoc
    parse errors, but carry no prose useful for a paper review.
    """
    # Remove figure / wrapfigure / subfigure environments entirely
    for env in ('figure*', 'figure', 'wrapfigure', 'subfigure', 'SCfigure'):
        tex = re.sub(
            r'\\begin\{' + re.escape(env) + r'\}.*?\\end\{' + re.escape(env) + r'\}',
            '', tex, flags=re.DOTALL,
        )
    # Remove any remaining \caption{...} (may span lines — match balanced braces)
    tex = re.sub(r'\\caption\*?\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', tex)
    return tex


def inject_dummy_macros(tex: str) -> str:
    """Add \\newcommand definitions for common undefined math macros.

    Only injects a definition if the macro isn't already defined in the source.
    Inserts them just before \\begin{document}.
    """
    doc_start = tex.find(r'\begin{document}')
    if doc_start == -1:
        return tex

    preamble = tex[:doc_start]
    injections = []
    for name in _UNDEF_MATH_MACROS:
        already_defined = re.search(
            r'\\(?:newcommand|renewcommand|def|DeclareMathOperator)\s*\{?\\' + name + r'\b',
            preamble,
        )
        if not already_defined:
            injections.append(rf'\providecommand{{\{name}}}{{\mathrm{{{name}}}}}')

    if injections:
        injection_block = '\n'.join(injections) + '\n'
        tex = tex[:doc_start] + injection_block + tex[doc_start:]

    return tex


# ---------------------------------------------------------------------------
# Math/table stashing (so pandoc only sees prose)
# ---------------------------------------------------------------------------

PRESERVE_ENVS = [
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

PLACEHOLDER_FMT = "XPLACEHOLDERX{idx}XPLACEHOLDERX"
PLACEHOLDER_PAT = re.compile(r"XPLACEHOLDERX(\d+)XPLACEHOLDERX")


def find_env_span(text: str, env: str, start: int = 0):
    begin_re = re.compile(r'\\begin\{' + re.escape(env) + r'\}')
    end_re   = re.compile(r'\\end\{'   + re.escape(env) + r'\}')
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


def extract_and_stash(tex: str) -> tuple[str, dict]:
    stash: dict = {}
    idx = 0
    spans = []

    for m in re.finditer(r'\\\[.*?\\\]', tex, flags=re.DOTALL):
        spans.append((m.start(), m.end(), m.group(0), 'displaymath'))

    for env in PRESERVE_ENVS:
        pos = 0
        while True:
            b, e, block = find_env_span(tex, env, pos)
            if b == -1:
                break
            spans.append((b, e, block, env))
            pos = e

    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    accepted = []
    for span in spans:
        b, e = span[0], span[1]
        if any(b >= ab and e <= ae for ab, ae, *_ in accepted):
            continue
        accepted.append(span)

    accepted.sort(key=lambda s: s[0], reverse=True)
    for b, e, block, env in accepted:
        stash[idx] = block
        placeholder = PLACEHOLDER_FMT.format(idx=idx)
        tex = tex[:b] + f"\n\n{placeholder}\n\n" + tex[e:]
        idx += 1

    return tex, stash


def restore_stash(md: str, stash: dict) -> str:
    def replacer(m):
        i = int(m.group(1))
        block = stash.get(i, m.group(0))
        return f"\n\n```latex\n{block.strip()}\n```\n\n"
    return PLACEHOLDER_PAT.sub(replacer, md)


# ---------------------------------------------------------------------------
# Pandoc runner
# ---------------------------------------------------------------------------

def run_pandoc(tex_source: str, resource_path: Path | None = None) -> str:
    tmp_dir = resource_path if (resource_path and resource_path.is_dir()) else Path(tempfile.gettempdir())
    tmp_path = tmp_dir / f"_pandoc_tmp_{id(tex_source)}.tex"
    tmp_path.write_text(tex_source, encoding="utf-8")

    try:
        try:
            result = subprocess.run(
                [
                    "pandoc", str(tmp_path),
                    "-f", "latex",
                    "-t", "markdown",
                    "--wrap=none",
                    "--markdown-headings=atx",
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_dir),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            print("pandoc timed out after 120s, using fallback", file=sys.stderr)
            return ""
        if result.returncode != 0 and not result.stdout:
            print(f"pandoc error: {result.stderr[:300]}", file=sys.stderr)
            return ""
        return result.stdout
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Pure-regex fallback when pandoc fails
# ---------------------------------------------------------------------------

def latex_to_text_fallback(tex: str) -> str:
    """Strip LaTeX markup from the document body and produce readable plain text.

    Used only when pandoc fails. Preserves section headings as ## headings,
    strips commands, and produces paragraph-separated prose.
    """
    # Extract body
    body_m = re.search(r'\\begin\{document\}(.*?)(?:\\end\{document\}|$)', tex, re.DOTALL)
    body = body_m.group(1) if body_m else tex

    # Convert section commands to markdown headings
    body = re.sub(r'\\section\*?\{([^}]+)\}', r'\n\n## \1\n\n', body)
    body = re.sub(r'\\subsection\*?\{([^}]+)\}', r'\n\n### \1\n\n', body)
    body = re.sub(r'\\subsubsection\*?\{([^}]+)\}', r'\n\n#### \1\n\n', body)
    body = re.sub(r'\\paragraph\*?\{([^}]+)\}', r'\n\n**\1** ', body)

    # Text formatting
    body = re.sub(r'\\(?:textbf|mathbf)\{([^}]+)\}', r'**\1**', body)
    body = re.sub(r'\\(?:textit|emph|textsl)\{([^}]+)\}', r'*\1*', body)
    body = re.sub(r'\\(?:texttt|code)\{([^}]+)\}', r'`\1`', body)
    body = re.sub(r'\\underline\{([^}]+)\}', r'\1', body)

    # Citations and references
    body = re.sub(r'\\cite(?:t|p|alt|alp)?\{([^}]+)\}', r'[\1]', body)
    body = re.sub(r'\\(?:ref|eqref|autoref)\{[^}]+\}', '', body)
    body = re.sub(r'\\label\{[^}]+\}', '', body)

    # Environments: itemize/enumerate -> simple lists
    body = re.sub(r'\\begin\{(?:itemize|enumerate|description)\}', '', body)
    body = re.sub(r'\\end\{(?:itemize|enumerate|description)\}', '', body)
    body = re.sub(r'\\item\s*', '\n- ', body)

    # Abstract environment
    body = re.sub(r'\\begin\{abstract\}(.*?)\\end\{abstract\}',
                  r'\n\n**Abstract:** \1\n\n', body, flags=re.DOTALL)

    # Drop figure/table/algorithm environments entirely
    body = re.sub(
        r'\\begin\{(?:figure|figure\*|table|table\*|algorithm|algorithm2e|algorithmic|wrapfigure)[^}]*\}.*?'
        r'\\end\{(?:figure|figure\*|table|table\*|algorithm|algorithm2e|algorithmic|wrapfigure)\}',
        '', body, flags=re.DOTALL,
    )

    # Footnotes
    body = re.sub(r'\\footnote\{([^}]*)\}', r' (\1)', body)

    # URLs
    body = re.sub(r'\\(?:url|href)\{([^}]+)\}(?:\{[^}]*\})?', r'\1', body)

    # Strip all remaining LaTeX commands (with optional args)
    body = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])*(?:\{[^{}]*\})*', ' ', body)

    # Strip leftover braces
    body = re.sub(r'[{}]', '', body)

    # Strip math delimiters (keep the content readable)
    body = re.sub(r'\$\$.*?\$\$', '', body, flags=re.DOTALL)
    body = re.sub(r'\$[^$\n]{0,80}\$', '', body)

    # Clean up whitespace
    body = re.sub(r'[ \t]+', ' ', body)
    body = re.sub(r'\n{3,}', '\n\n', body)

    return body.strip()


# ---------------------------------------------------------------------------
# Post-process pandoc output
# ---------------------------------------------------------------------------

def _clean_bib_str(s: str) -> str:
    """Strip LaTeX braces and commands from a bib field value."""
    s = re.sub(r'\{([^{}]*)\}', r'\1', s)  # unwrap single-level braces
    s = re.sub(r'\\[a-zA-Z]+\s*', '', s)   # strip \commands
    return s.strip()


def _make_label(title: str, author_raw: str, year: str) -> str:
    title = _clean_bib_str(title)[:100]
    first_author = _clean_bib_str(author_raw.split(" and ")[0].split(",")[0]).strip()
    if not title:
        return ""
    suffix = ""
    if first_author:
        multi = " and " in author_raw or "et~al" in author_raw
        suffix = f' ({first_author} et al.' if multi else f' ({first_author}'
        if year:
            suffix += f', {year}'
        suffix += ')'
    return f'"{title}"{suffix}'


def _parse_bib_file(text: str) -> dict:
    """Parse a .bib file text and return key→label map."""
    bib_map: dict[str, str] = {}
    for entry in re.split(r'(?=@\w+\s*\{)', text):
        entry = entry.strip()
        if not entry.startswith('@'):
            continue
        key_m = re.match(r'@\w+\s*\{([^,]+),', entry)
        if not key_m:
            continue
        key = key_m.group(1).strip()

        title_m = re.search(r'\btitle\s*=\s*[\{"]\s*\{?([^}"]{0,150})', entry, re.IGNORECASE)
        title = title_m.group(1).strip() if title_m else ""

        author_m = re.search(r'\bauthor\s*=\s*[\{"]\s*\{?([^}"]{0,120})', entry, re.IGNORECASE)
        author_raw = author_m.group(1).strip() if author_m else ""

        year_m = re.search(r'\byear\s*=\s*[\{"]?(\d{4})', entry, re.IGNORECASE)
        year = year_m.group(1) if year_m else ""

        label = _make_label(title, author_raw, year)
        if not label:
            continue

        bib_map[key] = label

        # Also index by arxiv ID from DOI-style key or url field
        for src in [key, entry]:
            arxiv_m = re.search(r'arxiv[./](\d{4}\.\d{4,5})', src, re.IGNORECASE)
            if arxiv_m:
                bib_map[arxiv_m.group(1)] = label
        url_m = re.search(r'\burl\s*=\s*[\{"]\s*https?://arxiv\.org/abs/(\d{4}\.\d{4,5})', entry, re.IGNORECASE)
        if url_m:
            bib_map[url_m.group(1)] = label

    return bib_map


def _parse_bbl_file(text: str) -> dict:
    """Parse a compiled .bbl file and return key→label map.

    .bbl format: \\bibitem[Author(Year)]{key} followed by author line,
    \\newblock title line, \\newblock venue line.
    """
    bib_map: dict[str, str] = {}
    # Split on \bibitem entries
    entries = re.split(r'(?=\\bibitem)', text)
    for entry in entries:
        key_m = re.match(r'\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}', entry)
        if not key_m:
            continue
        key = key_m.group(1).strip()

        # Year from optional arg [Author(Year)Author2 and ...]
        year_m = re.search(r'\((\d{4}[a-z]?)\)', entry)
        year = year_m.group(1)[:4] if year_m else ""

        # Author: first line after \bibitem{key}\n
        rest = entry[key_m.end():].strip()
        lines = [l.strip() for l in rest.splitlines() if l.strip()]

        author_raw = ""
        title = ""
        for i, line in enumerate(lines):
            line_clean = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{[^{}]*\}|\\[a-zA-Z]+\s*|\{|\}|~', ' ', line).strip()
            line_clean = re.sub(r'\s+', ' ', line_clean).strip()
            if not author_raw and line_clean and not line_clean.startswith('\\newblock'):
                author_raw = line_clean
            elif line_clean.startswith('\\newblock') or (i > 0 and not title):
                candidate = re.sub(r'^\\newblock\s*', '', line_clean).strip()
                candidate = re.sub(r'\.$', '', candidate).strip()
                if len(candidate) > 5 and not re.match(r'^\\|^\d', candidate):
                    title = candidate[:100]
                    break

        label = _make_label(title, author_raw, year)
        if label:
            bib_map[key] = label

    return bib_map


def parse_bib(tex_path: Path) -> dict:
    """Parse bibliography files (.bib or .bbl) and return key→label map.

    Tries .bib first (more structured), falls back to .bbl (compiled).
    Label format: "Title (First Author et al., Year)"
    """
    bib_map: dict[str, str] = {}
    latex_dir = tex_path.parent

    # Try .bib files first
    for bib_file in latex_dir.glob("*.bib"):
        try:
            text = bib_file.read_text(encoding="utf-8", errors="replace")
            bib_map.update(_parse_bib_file(text))
        except Exception:
            continue

    # Fall back to .bbl if no .bib entries found
    if not bib_map:
        for bbl_file in latex_dir.glob("*.bbl"):
            try:
                text = bbl_file.read_text(encoding="utf-8", errors="replace")
                # Detect biblatex format
                if r'\refsection' in text or r'\entry{' in text:
                    bib_map.update(_parse_biblatex_bbl(text))
                else:
                    bib_map.update(_parse_bbl_file(text))
            except Exception:
                continue

    return bib_map


def _parse_biblatex_bbl(text: str) -> dict:
    """Parse biblatex .bbl format (\\entry{key}{type}{} with \\field/\\name)."""
    bib_map: dict[str, str] = {}
    for entry in re.split(r'(?=\\entry\{)', text):
        key_m = re.match(r'\\entry\{([^}]+)\}', entry)
        if not key_m:
            continue
        key = key_m.group(1).strip()

        title_m = re.search(r'\\field\{title\}\{([^}]+(?:\{[^}]*\}[^}]*)*)\}', entry)
        title = _clean_bib_str(title_m.group(1)) if title_m else ""

        year_m = re.search(r'\\field\{(?:year|date)\}\{(\d{4})', entry)
        year = year_m.group(1) if year_m else ""

        # Extract first author's family name
        family_m = re.search(r'family=\{([^}]+)\}', entry)
        first_author = _clean_bib_str(family_m.group(1)) if family_m else ""
        multi = entry.count('family=') > 1

        if not title:
            continue

        suffix = ""
        if first_author:
            suffix = f' ({first_author} et al.' if multi else f' ({first_author}'
            if year:
                suffix += f', {year}'
            suffix += ')'
        bib_map[key] = f'"{title[:100]}"{suffix}'

    return bib_map


def resolve_citations(md: str, bib_map: dict) -> str:
    """Replace naked citation keys and DOI URLs with human-readable labels."""
    if not bib_map:
        return md

    # Replace [https://doi.org/10.48550/arxiv.XXXX.XXXXX] with titled label
    def replace_doi_url(m):
        url = m.group(1)
        arxiv_m = re.search(r'arxiv[./](\d{4}\.\d{4,5})', url, re.IGNORECASE)
        if arxiv_m and arxiv_m.group(1) in bib_map:
            return f"[{bib_map[arxiv_m.group(1)]}]"
        return m.group(0)

    md = re.sub(r'\[(https://doi\.org/[^\]]+)\]', replace_doi_url, md)

    # Replace [cite_key, cite_key2, ...] citation lists
    def replace_cite_list(m):
        keys = [k.strip() for k in m.group(1).split(',')]
        labels = []
        for k in keys:
            if k in bib_map:
                labels.append(bib_map[k])
            else:
                # Try arxiv ID match inside key
                arxiv_m = re.search(r'(\d{4}\.\d{4,5})', k)
                if arxiv_m and arxiv_m.group(1) in bib_map:
                    labels.append(bib_map[arxiv_m.group(1)])
                else:
                    labels.append(k)
        return '[' + '; '.join(labels) + ']'

    md = re.sub(r'\[([a-zA-Z][a-zA-Z0-9_:.,\s]+)\]', replace_cite_list, md)

    return md


def postprocess(md: str, tex_path: Path) -> str:
    # Strip ::: div wrappers
    md = re.sub(r'^:::\s*\{[^}]*\}\s*$', '', md, flags=re.MULTILINE)
    md = re.sub(r'^:::\s*\w*\s*$', '', md, flags=re.MULTILINE)

    # Strip raw latex fenced blocks pandoc emits for things it can't convert
    md = re.sub(r'```\{=latex\}.*?```', '', md, flags=re.DOTALL)

    # Strip remaining loose LaTeX commands (but NOT inside ``` blocks)
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

    # Fix figures
    figure_pat = re.compile(
        r'<figure[^>]*>\s*'
        r'<span[^>]*data-original-image-src="([^"]*)"[^>]*></span>'
        r'\s*<figcaption>(.*?)</figcaption>\s*</figure>',
        re.DOTALL,
    )
    def replace_figure(m):
        src = m.group(1)
        caption = re.sub(r'\s+', ' ', m.group(2)).strip()
        img_path = tex_path.parent / src
        if not img_path.exists() and Path(src).suffix.lower() in (".pdf", ".eps", ".ps"):
            for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
                if img_path.with_suffix(ext).exists():
                    src = str(img_path.with_suffix(ext).relative_to(tex_path.parent))
                    break
        return f"![{caption}]({src})\n\n*Figure: {caption}*"

    md = figure_pat.sub(replace_figure, md)
    md = re.sub(r'<span[^>]*data-original-image-src="([^"]*)"[^>]*></span>',
                lambda m: f"![image]({m.group(1)})", md)
    md = re.sub(r'<figure[^>]*>|</figure>', '', md)
    md = re.sub(r'<figcaption>.*?</figcaption>', '', md, flags=re.DOTALL)
    md = re.sub(r'(!\[[^\]]*\]\([^)]+\))#\S+(?:\s+width="[^"]*")?', r'\1', md)

    # Citations
    def clean_cite(m):
        keys = [k.strip().lstrip('@') for k in m.group(1).split(';')]
        return '[' + ', '.join(keys) + ']'
    md = re.sub(r'\[@([^\]]+)\]', clean_cite, md)
    md = re.sub(r'(?<!\[)@([a-zA-Z][a-zA-Z0-9_:]*)', r'[\1]', md)

    # Section IDs
    md = re.sub(r'(#{1,6} .+?)\s*\{#[^}]+\}', r'\1', md)
    md = re.sub(r'(#{1,6} [^#\n]+?)\s+#\w[\w:]*\s*$', r'\1', md, flags=re.MULTILINE)

    # Cleanup artifacts
    md = re.sub(r'^\s*\[\d+\]\s*(?:#\S+)?\s*$', '', md, flags=re.MULTILINE)
    md = re.sub(r'^\s*\d+(?:\.\d+)?(?:pt|em|ex|cm|mm|in|bp|pc)\s*$', '', md, flags=re.MULTILINE)
    md = re.sub(r'\\([`*_{}[\]()#+\-.!|])', r'\1', md)
    lines = md.splitlines()
    md = '\n'.join(l for l in lines if not re.match(r'^\s*\\[a-zA-Z]+\*?\s*$', l.strip()))
    md = re.sub(r'\n{4,}', '\n\n\n', md)

    return md.strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def find_latex_entry(latex_dir: Path) -> Path:
    """Find the real LaTeX entry point (file containing \\documentclass).

    arXiv papers often split content across multiple files. template.tex may
    be a wrapper or may have been pre-converted to markdown. This finds the
    real root .tex file.
    """
    for candidate in ["main.tex", "ms.tex", "paper.tex", "template.tex"]:
        p = latex_dir / candidate
        if p.exists() and r"\documentclass" in p.read_text(errors="replace"):
            return p
    # Fall back to any .tex containing \documentclass
    for p in sorted(latex_dir.glob("*.tex")):
        if r"\documentclass" in p.read_text(errors="replace"):
            return p
    return latex_dir / "template.tex"


def convert(tex_path: Path) -> str:
    tex = tex_path.read_text(encoding="utf-8", errors="replace")

    # Step 1: Inline all \input{} / \include{} so we have one complete source
    tex = inline_inputs(tex, tex_path.parent)

    # Step 2: Strip LaTeX comments
    tex = re.sub(r'(?m)^[ \t]*%.*$', '', tex)

    # Step 3: Trim to Conclusion on raw LaTeX (before pandoc sees it)
    # Extract body first for trimming, then put back in full doc for pandoc
    body_m = re.search(r'(\\begin\{document\})(.*?)(?:\\end\{document\}|$)', tex, re.DOTALL)
    if body_m:
        preamble = tex[:body_m.start(2)]
        body = body_m.group(2)
        body = trim_to_conclusion(body)
        tex = preamble + body + '\n\\end{document}\n'
    else:
        tex = trim_to_conclusion(tex)

    # Step 4: Strip figure environments and captions (inline math in captions
    # causes pandoc parse errors; figures add no prose value)
    tex = strip_problematic_envs(tex)

    # Step 5: Inject dummy \providecommand for undefined math macros
    tex = inject_dummy_macros(tex)

    # Step 6: Stash math/tables as placeholders
    modified_tex, stash = extract_and_stash(tex)

    # Step 7: Run pandoc
    raw_md = run_pandoc(modified_tex, resource_path=tex_path.parent)

    if not raw_md.strip():
        # Pandoc failed — use regex-based fallback on the trimmed source
        raw_md = latex_to_text_fallback(tex)
        # No stash to restore in fallback (math was already in tex)
        stash = {}

    # Step 8: Post-process and restore stash
    md = postprocess(raw_md, tex_path)
    md = restore_stash(md, stash)

    # Step 9: Resolve citations to human-readable labels using .bib file
    bib_map = parse_bib(tex_path)
    if bib_map:
        md = resolve_citations(md, bib_map)

    md = re.sub(r'\n{4,}', '\n\n\n', md)

    return md.strip()


def main():
    parser = argparse.ArgumentParser(description="Convert LaTeX paper to Markdown via pandoc")
    parser.add_argument("input", help="Input .tex file")
    parser.add_argument("--output", "-o", help="Output file (default: overwrite input)")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout")
    args = parser.parse_args()

    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Error: pandoc not found. Install with: apt install pandoc", file=sys.stderr)
        sys.exit(1)

    tex_path = Path(args.input)
    if not tex_path.exists():
        print(f"Error: {tex_path} not found", file=sys.stderr)
        sys.exit(1)

    md = convert(tex_path)

    if args.stdout:
        print(md)
    else:
        output_path = Path(args.output) if args.output else tex_path
        output_path.write_text(md, encoding="utf-8")
        print(f"Converted: {tex_path} -> {output_path}")
        print(f"Output: {len(md):,} chars, {len(md.splitlines())} lines")


if __name__ == "__main__":
    main()
