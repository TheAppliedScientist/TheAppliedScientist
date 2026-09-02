"""
benchmark_pass_at_k.py — pass@K evaluation with Kimi K2.5 via Fireworks

For each of 100 papers, runs K=4 independent review attempts using Kimi K2.5
(via Fireworks OpenAI-compatible API routed through Harbor/Claude Code).
Each attempt is scored by Gemini 3.1 Pro judge.

Results are saved per-attempt and aggregated for pass@K analysis.

Usage:
    python benchmark_pass_at_k.py --data-dir /root/data/pass_at_k_papers
    python benchmark_pass_at_k.py --data-dir /root/data/pass_at_k_papers --k 4 --max-concurrent 4
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

try:  # Legacy captured-trial reader; normal local execution does not need Harbor.
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial
except ImportError:
    TrialConfig = None
    Trial = object
from native_reviewer import run_native_review


# ---------------------------------------------------------------------------
# Task file upload — same as original benchmark
# ---------------------------------------------------------------------------

_TASK_SKIP_DIRS = {"environment", ".git", "__pycache__"}

PROJECT_DIR = Path(__file__).parent

# Months of safety margin between the paper's submission month and the search
# cutoff. The arXiv index `published` field reflects the latest revision date,
# not the original submission date — and reviewers typically can't cite work
# that appeared in the same conference cycle. 3 months gives a clean buffer.
SEARCH_CUTOFF_MARGIN_MONTHS = 3


_PUB_RE = re.compile(r"^(\d{4})-(\d{1,2})")


def _published_to_cutoff_month(published: str, margin: int = SEARCH_CUTOFF_MARGIN_MONTHS) -> str | None:
    """Return YYYY-MM = submission month minus `margin` months, or None if unparseable.

    `published` comes from task_metadata.json and looks like '2021-02-14 00:00:00'.
    For a 2021-02 paper with margin=3, returns '2020-11'.
    """
    if not published:
        return None
    m = _PUB_RE.match(published.strip())
    if not m:
        return None
    year, mo = int(m.group(1)), int(m.group(2))
    if not (1900 <= year <= 2100 and 1 <= mo <= 12):
        return None
    # Subtract `margin` months with proper year rollover.
    total = year * 12 + (mo - 1) - margin
    new_year, new_mo = divmod(total, 12)
    return f"{new_year:04d}-{new_mo + 1:02d}"


def _find_latex_entry(latex_dir: Path) -> Path:
    """Find the real LaTeX entry point in a paper directory.

    arXiv papers often use main.tex, ms.tex, or paper.tex as the real entry
    point while template.tex is a wrapper or may have been pre-converted.
    Returns the best candidate .tex file.
    """
    # Prefer known entry point names that contain \documentclass
    for candidate in ["main.tex", "ms.tex", "paper.tex", "template.tex"]:
        p = latex_dir / candidate
        if p.exists():
            content = p.read_text(errors="replace")
            if r"\documentclass" in content:
                return p
    # Fall back to any .tex file containing \documentclass
    for p in sorted(latex_dir.glob("*.tex")):
        if r"\documentclass" in p.read_text(errors="replace"):
            return p
    # Last resort: template.tex as-is
    return latex_dir / "template.tex"


def _latex_to_markdown(tex_path: Path) -> str:
    """Convert a LaTeX file to Markdown using latex_to_markdown.py (pandoc-based).

    Finds the real LaTeX entry point (template.tex may have been pre-converted
    to markdown by a prior run), then converts via pandoc with fallback.
    """
    latex_dir = tex_path.parent
    real_tex = _find_latex_entry(latex_dir)

    converter = PROJECT_DIR / "latex_to_markdown.py"
    if converter.is_file():
        try:
            result = subprocess.run(
                [sys.executable, str(converter), str(real_tex), "--stdout"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
            logger.warning(f"latex_to_markdown failed: {result.stderr[:200]}")
        except Exception as e:
            logger.warning(f"latex_to_markdown exception: {e}")
    # Fallback: return raw content of real tex file
    return real_tex.read_text(errors="replace")



class PassAtKTrial(Trial):
    """Trial subclass that uploads task content to local Docker sandboxes."""

    search_api_url: str = ""
    markdown_path: Path | None = None

    async def _setup_environment(self) -> None:
        await super()._setup_environment()

        task_dir = Path(self.config.task.path)
        if not task_dir.is_dir():
            logger.warning(f"Task dir {task_dir} not found, skipping file upload")
            return

        workdir = self._environment._workdir or "/app"

        # Upload task content — skip latex dir (handled separately below)
        for item in sorted(task_dir.iterdir()):
            if item.name in _TASK_SKIP_DIRS or item.name == "latex":
                continue
            target = f"/{workdir.strip('/')}/{item.name}"
            try:
                if item.is_file():
                    await self._environment.upload_file(item, target)
                elif item.is_dir():
                    await self._environment.upload_dir(item, target)
            except Exception as e:
                logger.warning(f"Failed to upload {item.name}: {e}")

        # Resolve paper markdown to upload as latex/template.tex.
        # Priority: OCR markdown (self.markdown_path) > pre-converted template.tex > pandoc conversion.
        latex_dir = task_dir / "latex"
        latex_target = f"/{workdir.strip('/')}/latex/template.tex"
        md_content: str | None = None

        ocr_path = self.markdown_path
        if ocr_path is not None and ocr_path.is_file():
            try:
                text = ocr_path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 500:
                    md_content = text
                    logger.info(f"Using OCR markdown ({ocr_path.name}): {len(md_content):,} chars")
            except Exception as e:
                logger.warning(f"Failed to read OCR markdown {ocr_path}: {e}")

        if md_content is None:
            tex_file = _find_latex_entry(latex_dir) if latex_dir.is_dir() else latex_dir / "template.tex"
            preconverted = latex_dir / "template.tex"
            if tex_file.is_file():
                try:
                    _pre_text = preconverted.read_text(encoding="utf-8", errors="replace") if preconverted.is_file() else ""
                    _preconv_ok = len(_pre_text) > 2000 and not _pre_text.lstrip().startswith("\\documentclass")
                    if _preconv_ok:
                        md_content = _pre_text
                        logger.info(f"Using pre-converted Markdown: {len(md_content):,} chars")
                    else:
                        md_content = _latex_to_markdown(tex_file)
                        logger.info(f"Converted LaTeX→Markdown: {len(md_content):,} chars")
                except Exception as e:
                    logger.warning(f"Failed to convert/upload latex: {e}")

        if md_content is not None:
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as f:
                    f.write(md_content)
                    tmp_tex = f.name
                await self._environment.upload_file(Path(tmp_tex), latex_target)
                os.unlink(tmp_tex)
                logger.info(f"Uploaded paper to {latex_target}")
            except Exception as e:
                logger.warning(f"Failed to upload paper: {e}")

        logger.info(f"Uploaded task content from {task_dir} to {workdir}")

        # Upload instruction template
        instruction_template = PROJECT_DIR / "prompts" / "paper_reviewer_instruction_template.md"
        if instruction_template.is_file():
            target = f"/{workdir.strip('/')}/instruction.md"
            try:
                await self._environment.upload_file(instruction_template, target)
            except Exception as e:
                logger.warning(f"Failed to upload instruction template: {e}")

        # Upload search skill to all likely locations for the claude-code agent
        skill_file = PROJECT_DIR / "skills" / "search-papers" / "SKILL.md"
        if skill_file.is_file():
            for skill_target in [
                "/root/.claude/skills/search-papers/SKILL.md",
                "/home/user/.claude/skills/search-papers/SKILL.md",
                f"/{workdir.strip('/')}/.claude/skills/search-papers/SKILL.md",
                "/logs/agent/sessions/skills/search-papers/SKILL.md",
            ]:
                try:
                    await self._environment.upload_file(skill_file, skill_target)
                    logger.info(f"Uploaded search skill to {skill_target}")
                except Exception:
                    pass

        # Upload the `search` CLI wrapper to /app/search and make it executable.
        # The skill instructs the agent to ONLY use this wrapper (never curl).
        search_cli = PROJECT_DIR / "skills" / "search-papers" / "search"
        if search_cli.is_file():
            cli_target = f"/{workdir.strip('/')}/search"
            try:
                await self._environment.upload_file(search_cli, cli_target)
                # Make executable inside the sandbox
                try:
                    await self._environment.exec(f"chmod +x {cli_target}")
                except Exception:
                    pass
                logger.info(f"Uploaded search CLI to {cli_target}")
            except Exception as e:
                logger.warning(f"Failed to upload search CLI: {e}")

        # Write paper-cutoff month so the search CLI auto-applies a temporal
        # filter even if the agent forgets `--before`. Cutoff = submission
        # month - SEARCH_CUTOFF_MARGIN_MONTHS so search results never include
        # work that postdates the paper under review.
        cutoff_month: str | None = None
        try:
            meta_path = task_dir / "task_metadata.json"
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text())
                cutoff_month = _published_to_cutoff_month(meta.get("published", ""))
        except Exception as e:
            logger.warning(f"Could not derive cutoff month for {task_dir.name}: {e}")
        if cutoff_month:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(cutoff_month)
                tmp_path = f.name
            target = f"/{workdir.strip('/')}/paper_cutoff.txt"
            try:
                await self._environment.upload_file(Path(tmp_path), target)
                logger.info(f"Uploaded paper_cutoff.txt={cutoff_month} to {target}")
            except Exception as e:
                logger.warning(f"Failed to upload paper_cutoff.txt: {e}")
            finally:
                os.unlink(tmp_path)
        else:
            logger.warning(
                f"No usable `published` date for {task_dir.name} — "
                f"agent searches will run UNFILTERED (temporal hallucination risk)"
            )

        # Write search API URL
        if self.search_api_url:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(self.search_api_url)
                tmp_path = f.name
            target = f"/{workdir.strip('/')}/search_api_url.txt"
            try:
                await self._environment.upload_file(Path(tmp_path), target)
            except Exception as e:
                logger.warning(f"Failed to upload search API URL: {e}")
            finally:
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------

_JUDGE_PROMPT_PATH = PROJECT_DIR / "prompts" / "llm_judge_instruction.md"


def _extract_last_review(trajectory_path: Path) -> str:
    data = json.loads(trajectory_path.read_text())
    agent_msgs = [
        step["message"] for step in data.get("steps", [])
        if step.get("source") == "agent" and step.get("message") and len(step["message"]) > 200
    ]
    if not agent_msgs:
        return ""
    # Prefer the last message that looks like a structured review.
    # Reasoning models often keep running after writing the review, so the
    # final message may be internal commentary rather than the review itself.
    review_markers = ("### Summary", "### Strengths", "### Weaknesses", "### Scores", "**Scores**")
    for msg in reversed(agent_msgs):
        if any(m in msg for m in review_markers):
            return msg
    # Fall back to the last long message
    return agent_msgs[-1]


def _load_task_metadata(task_dir: Path, markdown_path: Path | None = None) -> dict:
    metadata = {"title": "", "abstract": "", "human_reviews": [], "paper_body": ""}
    meta_path = task_dir / "task_metadata.json"
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text())
            metadata["title"] = data.get("title", "")
            metadata["abstract"] = data.get("abstract", "")
            metadata["human_reviews"] = data.get("human_reviews", [])
        except Exception:
            pass

    # Load the full paper body for the judge. Priority: OCR > pre-converted > pandoc.
    if markdown_path is not None and markdown_path.is_file():
        try:
            txt = markdown_path.read_text(encoding="utf-8", errors="replace")
            if len(txt) > 500:
                metadata["paper_body"] = txt
                return metadata
        except Exception:
            pass
    latex_dir = task_dir / "latex"
    preconverted = latex_dir / "template.tex"
    paper_body = ""
    if preconverted.is_file():
        try:
            txt = preconverted.read_text(encoding="utf-8", errors="replace")
            if len(txt) > 2000 and not txt.lstrip().startswith("\\documentclass"):
                paper_body = txt
        except Exception:
            pass
    # No pandoc fallback — if OCR and pre-converted both missing, judge gets empty body
    metadata["paper_body"] = paper_body

    if not metadata["title"]:
        body = metadata["paper_body"]
        m = re.search(r"^# (.+)$", body, re.MULTILINE)
        if m:
            metadata["title"] = m.group(1).strip()
        else:
            m = re.search(r"\\title\{([^}]+)\}", body)
            if m:
                metadata["title"] = m.group(1).strip()

    if not metadata["abstract"]:
        body = metadata["paper_body"]
        m = re.search(r"^## Abstract\s*\n(.*?)(?=^#|\Z)", body, re.MULTILINE | re.DOTALL)
        if m:
            metadata["abstract"] = m.group(1).strip()
        else:
            m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.DOTALL)
            if m:
                metadata["abstract"] = m.group(1).strip()

    return metadata


async def score_review(
    trajectory_path: Path,
    task_dir: Path,
    judge_api_key: str,
    judge_model: str = "gemini-3.1-pro-preview",
    judge_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/",
    markdown_path: Path | None = None,
) -> dict:
    from agentic_judge import AgenticJudge, AgenticJudgeConfig

    review = _extract_last_review(trajectory_path)
    if not review or len(review) < 100:
        logger.warning("Review too short or empty")
        return {"error": "review_too_short", "reward": 0.0}

    metadata = _load_task_metadata(task_dir, markdown_path=markdown_path)
    if not metadata["title"]:
        logger.warning(f"No title found in {task_dir}")
        return {"error": "no_title", "reward": 0.0}

    human_reviews_text = "\n\n---\n\n".join(
        f"**Human Review {i+1}:**\n\n{r}"
        for i, r in enumerate(metadata["human_reviews"])
    )

    # Prefer OCR markdown when caller supplies one; fall back to task-dir latex.
    paper_body = ""
    if markdown_path is not None and markdown_path.is_file():
        try:
            text = markdown_path.read_text(encoding="utf-8", errors="replace")
            if len(text) > 500:
                paper_body = text
        except Exception:
            pass
    if not paper_body:
        paper_body = metadata.get("paper_body", "")
    # Truncate at References/Bibliography — drop citation list, keep full content.
    _ref_m = re.search(r"^#+\s*(References|Bibliography)\s*$", paper_body, re.MULTILINE | re.IGNORECASE)
    if not _ref_m:
        _ref_m = re.search(r"^\*{0,2}(References|Bibliography)\*{0,2}\s*$", paper_body, re.MULTILINE | re.IGNORECASE)
    if _ref_m:
        paper_body = paper_body[:_ref_m.start()].rstrip()

    # `judge_base_url` is unused with the native google-genai SDK (it talks
    # to the canonical generativelanguage endpoint). We accept it in the
    # signature for backwards compat with the OpenAI-compat call sites.
    del judge_base_url
    judge = AgenticJudge(
        api_key=judge_api_key,
        prompt_template=_JUDGE_PROMPT_PATH.read_text(),
        config=AgenticJudgeConfig(model=judge_model),
    )
    scores = await judge.score(
        title=metadata["title"],
        abstract=metadata["abstract"],
        paper_body=paper_body,
        human_reviews_text=human_reviews_text,
        model_review=review,
    )
    if "error" in scores and "reward" in scores and scores.get("reward") == 0.0 and "comprehension" not in scores:
        # Hard failure from the judge (parse error, API exception, etc.).
        return scores

    def _s(key: str) -> float:
        node = scores.get(key, {})
        if isinstance(node, dict):
            return float(node.get("score", 0) or 0)
        return float(node or 0)

    comprehension = _s("comprehension")
    substance = _s("substance_and_specificity")
    insight = _s("insight")
    issue_overlap = _s("issue_overlap")
    fabrication = _s("fabrication")
    calibration = _s("calibration_pairwise")

    # Final reward: mean of the 3 discriminative criteria.
    # Comprehension/substance/insight saturate at ~1.0 and add no signal.
    reward = (issue_overlap + fabrication + calibration) / 3.0

    scores["reward"] = reward
    logger.info(
        f"Judge scores: comp={comprehension} sub={substance} ins={insight} "
        f"overlap={issue_overlap} fab={fabrication} cal={calibration} -> reward={reward:.3f}"
    )
    return scores


# ---------------------------------------------------------------------------
# Trial config builder
# ---------------------------------------------------------------------------

def build_trial_config(
    task_path: str,
    trials_dir: str,
    trial_name: str,
    agent_timeout: int = 3600,
    max_turns: int = 200,
) -> dict:
    """Build the verified reviewer trial with a configurable Anthropic endpoint."""
    review_model = os.environ.get("REVIEW_MODEL", "deepseek-v4-flash")
    claude_code_version = os.environ.get("CLAUDE_CODE_VERSION", "2.1.101")
    reasoning_effort = os.environ.get("REVIEW_REASONING_EFFORT", "high")
    return {
        "trial_name": trial_name,
        "trials_dir": trials_dir,
        "task": {"path": task_path},
        "agent": {
            "name": "claude-code",
            "model_name": review_model,
            "override_timeout_sec": agent_timeout,
            # First local image build can install npm/apt dependencies.
            "override_setup_timeout_sec": 900,
            "kwargs": {
                "max_turns": max_turns,
                "reasoning_effort": reasoning_effort,
                # Pin to last pre-April-11 Claude Code to rule out harness regressions.
                "version": claude_code_version,
            },
            "env": {
                # Route Claude Code to Deepseek v4 flash via its Anthropic-compatible endpoint
                "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", ""),
                "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
                "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
                "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", review_model),
                "ANTHROPIC_DEFAULT_OPUS_MODEL": os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL", review_model),
                "ANTHROPIC_DEFAULT_SONNET_MODEL": os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", review_model),
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", review_model),
                "CLAUDE_CODE_SUBAGENT_MODEL": os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL", review_model),
                "CLAUDE_CODE_EFFORT_LEVEL": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL", reasoning_effort),
            },
        },
        "environment": {
            "type": "docker",
            "force_build": False,
            "override_cpus": 2,
            "override_memory_mb": 2048,
            "override_storage_mb": 2048,
            "suppress_override_warnings": True,
        },
        "verifier": {"disable": True},
    }


# ---------------------------------------------------------------------------
# Single attempt runner
# ---------------------------------------------------------------------------

async def run_single_attempt(
    task_dir: Path,
    trials_dir: Path,
    results_dir: Path,
    search_api_url: str,
    judge_api_key: str,
    judge_model: str,
    judge_base_url: str,
    paper_id: str,
    attempt_idx: int,
    markdown_dir: Path | None = None,
    skip_judge: bool = False,
) -> dict:
    """Run one review attempt for a paper."""
    result_dir = results_dir / paper_id / f"attempt_{attempt_idx}"

    # Skip if already successfully completed
    existing_result = result_dir / "result.json"
    if existing_result.exists():
        try:
            existing = json.loads(existing_result.read_text())
            if existing.get("status") == "success" and existing.get("reward", 0) > 0:
                logger.info(f"Skipping {paper_id} attempt {attempt_idx} — already done (reward={existing['reward']:.3f})")
                return existing
        except Exception:
            pass

    result_dir.mkdir(parents=True, exist_ok=True)
    # Use PROXY_MODEL as the trial-name prefix — derived from whatever provider
    # is currently wired up. Sanitize to an alphanumeric short slug.
    model_id = os.environ.get("PROXY_MODEL", "model")
    model_slug = re.sub(r"[^A-Za-z0-9]+", "-", model_id.split("/")[-1]).strip("-").lower() or "model"
    trial_name = f"{model_slug}-pass-at-k-{paper_id}-attempt{attempt_idx}-{int(time.time())}"
    logger.info(f"Starting {paper_id} attempt {attempt_idx}")

    # Copy instruction template
    instruction_template = PROJECT_DIR / "prompts" / "paper_reviewer_instruction_template.md"
    if instruction_template.is_file():
        shutil.copy2(instruction_template, task_dir / "instruction.md")

    start_time = time.time()
    if os.environ.get("REVIEW_EXECUTION_BACKEND", "native") != "harbor-docker":
        try:
            trajectory_path, native_metadata = await run_native_review(
                task_dir, result_dir, search_api_url, paper_id, attempt_idx
            )
        except Exception as e:
            logger.error(f"Native reviewer failed for {paper_id} attempt {attempt_idx}: {e}")
            error_result = {
                "paper_id": paper_id, "attempt": attempt_idx, "status": "error",
                "error": str(e), "duration_sec": time.time() - start_time, "reward": 0.0,
            }
            (result_dir / "result.json").write_text(json.dumps(error_result, indent=2))
            return error_result
        scores = {}
        if not skip_judge:
            scores = await score_review(
                trajectory_path, task_dir, judge_api_key, judge_model, judge_base_url,
                markdown_path=(markdown_dir / f"{paper_id}.md") if markdown_dir else None,
            )
        result = {
            "paper_id": paper_id, "attempt": attempt_idx, "trial_name": trial_name,
            "status": "success", "duration_sec": time.time() - start_time,
            "scores": scores, "reward": scores.get("reward", 0.0),
            "model": os.environ.get("REVIEW_MODEL", "unknown"),
            "judge_model": judge_model, "tokens": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runner": native_metadata["runner"],
        }
        (result_dir / "result.json").write_text(json.dumps(result, indent=2))
        return result
    if TrialConfig is None:
        raise RuntimeError("Legacy harbor-docker backend requested, but Harbor is not installed")
    config_dict = build_trial_config(
        task_path=str(task_dir), trials_dir=str(trials_dir), trial_name=trial_name,
    )
    trial_config = TrialConfig.model_validate(config_dict)
    trial = await PassAtKTrial.create(trial_config)
    trial.search_api_url = search_api_url
    if markdown_dir is not None:
        md = markdown_dir / f"{paper_id}.md"
        if md.is_file(): trial.markdown_path = md
    try:
        trial_result = await trial.run()
    except Exception as e:
        logger.error(f"Trial failed for {paper_id} attempt {attempt_idx}: {e}")
        error_result = {
            "paper_id": paper_id,
            "attempt": attempt_idx,
            "status": "error",
            "error": str(e),
            "duration_sec": time.time() - start_time,
            "reward": 0.0,
        }
        (result_dir / "result.json").write_text(json.dumps(error_result, indent=2))
        return error_result

    duration = time.time() - start_time
    logger.info(f"Trial completed: {paper_id} attempt {attempt_idx} in {duration:.0f}s")

    # Copy trajectory
    trial_dir = trials_dir / trial_name
    trajectory_path = None
    for traj_file in trial_dir.rglob("trajectory.json"):
        trajectory_path = traj_file
        break

    if trajectory_path and trajectory_path.exists():
        dest = result_dir / "trajectory.json"
        shutil.copy2(trajectory_path, dest)
        trajectory_path = dest

    for jsonl_file in trial_dir.rglob("*.jsonl"):
        shutil.copy2(jsonl_file, result_dir / "session.jsonl")
        break

    # Score
    scores = {}
    if not skip_judge and trajectory_path and trajectory_path.exists():
        md_path = None
        if markdown_dir is not None:
            md = markdown_dir / f"{paper_id}.md"
            if md.is_file():
                md_path = md
        scores = await score_review(
            trajectory_path=trajectory_path,
            task_dir=task_dir,
            judge_api_key=judge_api_key,
            judge_model=judge_model,
            judge_base_url=judge_base_url,
            markdown_path=md_path,
        )

    exception_type = None
    if trial_result.exception_info:
        exception_type = trial_result.exception_info.exception_type

    result = {
        "paper_id": paper_id,
        "attempt": attempt_idx,
        "trial_name": trial_name,
        "status": "success" if not exception_type else "error",
        "exception_type": exception_type,
        "duration_sec": duration,
        "scores": scores,
        "reward": scores.get("reward", 0.0),
        "model": os.environ.get("PROXY_MODEL", "unknown"),
        "judge_model": judge_model,
        "tokens": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if trial_result.agent_result:
        result["tokens"] = {
            "input": trial_result.agent_result.n_input_tokens,
            "output": trial_result.agent_result.n_output_tokens,
            "cache": trial_result.agent_result.n_cache_tokens,
            "cost_usd": trial_result.agent_result.cost_usd,
        }

    (result_dir / "result.json").write_text(json.dumps(result, indent=2))
    logger.info(f"{paper_id} attempt {attempt_idx}: reward={result['reward']:.3f}")
    return result


# ---------------------------------------------------------------------------
# Main pass@K runner
# ---------------------------------------------------------------------------

async def run_pass_at_k(
    data_dir: Path,
    trials_dir: Path,
    results_dir: Path,
    search_api_url: str,
    judge_api_key: str,
    judge_model: str,
    judge_base_url: str,
    k: int = 4,
    max_concurrent: int = 4,
    task_filter: list[str] | None = None,
    max_tasks: int | None = None,
    markdown_dir: Path | None = None,
    skip_judge: bool = False,
):
    """Run K attempts for each paper and save all results."""
    # Discover tasks
    task_dirs = sorted([
        d for d in data_dir.iterdir()
        if d.is_dir() and (d / "instruction.md").exists()
    ])

    if task_filter:
        task_dirs = [d for d in task_dirs if d.name in task_filter]

    # Require ≥2 human reviews per paper — single-reviewer ground truth is too noisy
    # for the overlap/calibration criteria to be meaningful. Skip when judging is disabled.
    if not skip_judge:
        before = len(task_dirs)
        task_dirs = [
            d for d in task_dirs
            if len(json.loads((d / "task_metadata.json").read_text()).get("human_reviews", []))
            >= 2
            if (d / "task_metadata.json").is_file()
        ]
        skipped = before - len(task_dirs)
        if skipped:
            logger.info(f"Skipped {skipped} papers with <2 human reviews")

    if max_tasks:
        task_dirs = task_dirs[:max_tasks]

    logger.info(f"Found {len(task_dirs)} tasks (≥2 human reviews), running {k} attempts each = {len(task_dirs) * k} total runs")

    results_dir.mkdir(parents=True, exist_ok=True)
    trials_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrent)
    all_results = []

    async def run_with_semaphore(task_dir, attempt_idx):
        async with semaphore:
            return await run_single_attempt(
                task_dir=task_dir,
                trials_dir=trials_dir,
                results_dir=results_dir,
                search_api_url=search_api_url,
                judge_api_key=judge_api_key,
                judge_model=judge_model,
                judge_base_url=judge_base_url,
                paper_id=task_dir.name,
                attempt_idx=attempt_idx,
                markdown_dir=markdown_dir,
                skip_judge=skip_judge,
            )

    # Create all tasks: K attempts per paper
    tasks = []
    for td in task_dirs:
        for attempt in range(k):
            tasks.append(run_with_semaphore(td, attempt))

    logger.info(f"Launching {len(tasks)} total trials...")
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    results_by_paper = {}
    for r in all_results:
        if isinstance(r, Exception):
            logger.error(f"Task exception: {r}")
            continue
        pid = r.get("paper_id", "unknown")
        if pid not in results_by_paper:
            results_by_paper[pid] = []
        results_by_paper[pid].append(r)

    # Save per-paper aggregation
    for pid, attempts in results_by_paper.items():
        agg = {
            "paper_id": pid,
            "k": k,
            "attempts": attempts,
            "rewards": [a.get("reward", 0.0) for a in attempts],
            "max_reward": max(a.get("reward", 0.0) for a in attempts),
            "mean_reward": sum(a.get("reward", 0.0) for a in attempts) / len(attempts),
            "any_success": any(a.get("status") == "success" for a in attempts),
        }
        paper_result_dir = results_dir / pid
        paper_result_dir.mkdir(parents=True, exist_ok=True)
        (paper_result_dir / "aggregated.json").write_text(json.dumps(agg, indent=2))

    # Global summary
    all_rewards = []
    for r in all_results:
        if isinstance(r, dict):
            all_rewards.append(r.get("reward", 0.0))

    summary = {
        "experiment": "pass@K",
        "model": os.environ.get("PROXY_MODEL", "unknown"),
        "judge_model": judge_model,
        "k": k,
        "total_papers": len(task_dirs),
        "total_attempts": len(tasks),
        "completed_attempts": len([r for r in all_results if isinstance(r, dict)]),
        "failed_attempts": len([r for r in all_results if isinstance(r, Exception)]),
        "mean_reward_all_attempts": sum(all_rewards) / len(all_rewards) if all_rewards else 0.0,
        "per_paper": {
            pid: {
                "rewards": [a.get("reward", 0.0) for a in attempts],
                "max_reward": max(a.get("reward", 0.0) for a in attempts),
                "mean_reward": sum(a.get("reward", 0.0) for a in attempts) / len(attempts),
            }
            for pid, attempts in results_by_paper.items()
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    summary_path = results_dir / f"pass_at_k_summary_{run_ts}.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    logger.info(
        f"\n{'='*60}\n"
        f"PASS@K BENCHMARK COMPLETE\n"
        f"  Model: {os.environ.get('PROXY_MODEL', '?')}\n"
        f"  Judge: {judge_model}\n"
        f"  Papers: {len(task_dirs)}, K={k}\n"
        f"  Total attempts: {summary['completed_attempts']}/{len(tasks)}\n"
        f"  Mean reward (all attempts): {summary['mean_reward_all_attempts']:.3f}\n"
        f"  Results: {summary_path}\n"
        f"{'='*60}"
    )

    return summary


def main():
    import argparse

    parser = argparse.ArgumentParser(description="pass@K benchmark with Kimi K2.5")
    parser.add_argument("--data-dir", type=str, required=True)
    parser.add_argument("--results-dir", type=str, default="/root/pass_at_k/results")
    parser.add_argument("--trials-dir", type=str, default="/root/pass_at_k/trials")
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--tasks", nargs="*", help="Specific paper IDs")
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--markdown-dir", type=str, default=None,
                        help="Directory of OCR markdown files (<paper_id>.md). "
                             "Preferred over task-dir latex when present.")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Run agent only, skip judging/scoring.")
    args = parser.parse_args()

    # Load .env
    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)

    # Claude Code requires an Anthropic Messages-compatible endpoint. This can be
    # Anthropic, DeepSeek's native Anthropic route, or a user-operated converter.
    assert os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"), \
        "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN must be set"
    assert os.environ.get("ANTHROPIC_BASE_URL"), \
        "ANTHROPIC_BASE_URL must point to an Anthropic-format endpoint"

    judge_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not args.skip_judge:
        assert judge_api_key, "GEMINI_API_KEY must be set for judge"

    search_api_url = os.environ.get("SEARCH_PUBLIC_URL", "http://localhost:8081")
    judge_model = os.environ.get("JUDGE_MODEL", "gemini-3.1-pro-preview")
    judge_base_url = os.environ.get(
        "JUDGE_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    logger.info(f"Data dir: {args.data_dir}")
    logger.info(
        "Reviewer model: %s via %s",
        os.environ.get("REVIEW_MODEL", "deepseek-v4-flash"),
        os.environ.get("ANTHROPIC_BASE_URL", "?"),
    )
    logger.info(f"Judge: {judge_model}")
    logger.info(f"K: {args.k}")

    asyncio.run(
        run_pass_at_k(
            data_dir=Path(args.data_dir),
            trials_dir=Path(args.trials_dir),
            results_dir=Path(args.results_dir),
            search_api_url=search_api_url,
            judge_api_key=judge_api_key,
            judge_model=judge_model,
            judge_base_url=judge_base_url,
            k=args.k,
            max_concurrent=args.max_concurrent,
            task_filter=args.tasks,
            max_tasks=args.max_tasks,
            markdown_dir=Path(args.markdown_dir) if args.markdown_dir else None,
            skip_judge=args.skip_judge,
        )
    )


if __name__ == "__main__":
    main()
