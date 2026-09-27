"""Direct-host reviewer runner used when Docker is unavailable.

It preserves the reviewed paper, prompt, search skill, Claude Code version/model,
and ATIF-like trajectory artifacts used by the historical Harbor run. Only the
execution boundary changes: Claude Code runs in a private local job directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def _native_path(path: str, workspace: Path) -> str:
    return path.replace("/app", str(workspace))


def _copy_runtime_files(
    task_dir: Path, workspace: Path, search_api_url: str, cutoff_month: str | None,
) -> str:
    shutil.copytree(task_dir, workspace, dirs_exist_ok=True)
    prompt = (PROJECT_DIR / "prompts/paper_reviewer_instruction_template.md").read_text()
    prompt = _native_path(prompt, workspace)
    note_path = workspace / "source_note.txt"
    if note_path.is_file():
        note = _native_path(note_path.read_text(encoding="utf-8").strip(), workspace)
        location = prompt.find("**Paper location:**")
        if location >= 0:
            line_end = prompt.find("\n", location)
            prompt = prompt[:line_end + 1] + note + "\n" + prompt[line_end + 1:]
    (workspace / "instruction.md").write_text(prompt, encoding="utf-8")

    skill_dir = workspace / ".claude/skills/search-papers"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill = (PROJECT_DIR / "skills/search-papers/SKILL.md").read_text()
    skill_dir.joinpath("SKILL.md").write_text(_native_path(skill, workspace), encoding="utf-8")
    search = workspace / "search"
    shutil.copy2(PROJECT_DIR / "skills/search-papers/search", search)
    search.chmod(0o755)
    # The CLI historically uses fixed /app paths. Rewriting only those runtime
    # locations leaves its API behavior unchanged.
    search.write_text(_native_path(search.read_text(), workspace), encoding="utf-8")
    (workspace / "search_api_url.txt").write_text(search_api_url, encoding="utf-8")
    if cutoff_month:
        (workspace / "paper_cutoff.txt").write_text(cutoff_month, encoding="utf-8")
    return prompt


def _assistant_text(event: dict) -> str:
    if event.get("type") == "result" and isinstance(event.get("result"), str):
        return event["result"]
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    chunks = []
    for block in message.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(block.get("text", ""))
    return "\n".join(chunks)


async def run_native_review(
    task_dir: Path,
    result_dir: Path,
    search_api_url: str,
    paper_id: str,
    attempt_idx: int,
    cutoff_month: str | None,
) -> tuple[Path, dict]:
    """Run one reviewer attempt and return ``(trajectory_path, metadata)``."""
    base = Path(os.environ.get("TAS_NATIVE_JOB_DIR", tempfile.gettempdir()))
    base.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="tas-review-", dir=base))
    prompt = _copy_runtime_files(task_dir, workspace, search_api_url, cutoff_month)
    claude = os.environ.get("CLAUDE_REVIEW_BIN", "claude")
    if not Path(claude).is_absolute():
        resolved = shutil.which(claude)
        if not resolved:
            raise RuntimeError(f"Claude Code executable not found: {claude}")
        claude = resolved

    model = os.environ.get("REVIEW_MODEL", os.environ.get("ANTHROPIC_MODEL", ""))
    command = [
        claude, "--print", "--verbose", "--output-format", "stream-json",
        "--permission-mode", "bypassPermissions", "--model", model,
        "--effort", os.environ.get("REVIEW_REASONING_EFFORT", "high"), prompt,
    ]
    child_env = os.environ.copy()
    preexec_fn = None
    if os.geteuid() == 0:
        account = pwd.getpwnam(os.environ.get("TAS_AGENT_USER", "nobody"))
        home = workspace / ".home"
        home.mkdir(exist_ok=True)
        for path in [workspace, *workspace.rglob("*")]:
            try:
                os.chown(path, account.pw_uid, account.pw_gid)
            except FileNotFoundError:
                pass
        child_env["HOME"] = str(home)
        os.chown(home, account.pw_uid, account.pw_gid)
        def drop_privileges() -> None:
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
        preexec_fn = drop_privileges
    started = time.time()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=workspace,
        env=child_env,
        preexec_fn=preexec_fn,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = int(os.environ.get("REVIEW_TIMEOUT_SEC", "1800"))
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"native Claude Code reviewer timed out after {timeout}s")

    events: list[dict] = []
    messages: list[str] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        text = _assistant_text(event)
        if text:
            messages.append(text)
    if process.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"Claude Code reviewer exited {process.returncode}: {tail}")
    if not messages:
        raise RuntimeError("Claude Code reviewer returned no assistant response")

    result_dir.mkdir(parents=True, exist_ok=True)
    trajectory = {
        "schema_version": "1.0",
        "steps": [{"source": "agent", "message": text} for text in messages],
        "metadata": {
            "runner": "native-claude-code",
            "paper_id": paper_id,
            "attempt": attempt_idx,
            "model": model,
            "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "duration_sec": time.time() - started,
        },
    }
    trajectory_path = result_dir / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    (result_dir / "session.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return trajectory_path, trajectory["metadata"]
