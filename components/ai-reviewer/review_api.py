"""
review_api.py — HTTP API wrapping the HarborTrajectoryGen review pipeline.

Accepts a paper (LaTeX), runs one review attempt via benchmark_pass_at_k.py
with --skip-judge, and returns the review text.

Usage:
    python review_api.py --port 8282
    nohup python review_api.py --port 8282 > /root/review_api.log 2>&1 &

Endpoints:
    POST /review  —  {latex_content, title, abstract}  →  {review_text, status}
    GET  /health  —  {status: "ok"}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from aiohttp import web

BENCHMARK_SCRIPT = Path(__file__).parent / "benchmark_pass_at_k.py"
INSTRUCTION_TEMPLATE = Path(__file__).parent / "prompts" / "paper_reviewer_instruction_template.md"
SEARCH_SKILL = Path(__file__).parent / "skills" / "search-papers" / "SKILL.md"
SEARCH_CLI = Path(__file__).parent / "skills" / "search-papers" / "search"
TRAJECTORY_ARCHIVE_DIR = Path(os.environ.get("TRAJECTORY_ARCHIVE_DIR", "/root/review-viewer/results"))


def _archive_trajectory(job_id: str, trajectory_path: Path) -> None:
    """Copy a finished run's trajectory.json into the persistent viewer dir."""
    dest_dir = TRAJECTORY_ARCHIVE_DIR / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(trajectory_path, dest_dir / "trajectory.json")


def _make_task_dir(paper_dir: Path, latex_content: str, title: str, abstract: str, published: str = "") -> None:
    """Create a minimal Harbor task directory for a single paper."""
    paper_dir.mkdir(parents=True, exist_ok=True)

    latex_dir = paper_dir / "latex"
    latex_dir.mkdir(exist_ok=True)
    (latex_dir / "template.tex").write_text(latex_content, encoding="utf-8")

    if not published:
        from datetime import datetime, timezone
        published = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    metadata = {
        "paper_id": paper_dir.name,
        "title": title,
        "abstract": abstract,
        "authors": "",
        "categories": "",
        "primary_category": "",
        "published": published,
        "human_reviews": [],
    }
    (paper_dir / "task_metadata.json").write_text(json.dumps(metadata, indent=2))

    # Minimal instruction — the full template is uploaded into the sandbox by
    # PassAtKTrial._setup_environment via PROJECT_DIR/prompts/paper_reviewer_instruction_template.md.
    (paper_dir / "instruction.md").write_text(
        "Review the paper at latex/template.tex\n"
    )

    # Harbor task config
    task_toml = (
        'version = "1.0"\n\n'
        "[metadata]\n"
        'author = "theappliedscientist-ai-reviewer"\n'
        'category = "research"\n'
        'tags = ["peer-review"]\n\n'
        "[environment]\n"
        "cpus = 2\n"
        "memory_mb = 2048\n"
        "storage_mb = 2048\n"
        "allow_internet = true\n\n"
        "[agent]\n"
        "timeout_sec = 3600\n\n"
        "[verifier]\n"
        "timeout_sec = 120\n"
        "disable = true\n"
    )
    (paper_dir / "task.toml").write_text(task_toml)

    tests_dir = paper_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text("#!/bin/bash\necho pass\n")
    (tests_dir / "test.sh").chmod(0o755)

    env_dir = paper_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text(
        "FROM ubuntu:22.04\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends curl git && "
        "rm -rf /var/lib/apt/lists/*\n"
        "WORKDIR /app\n"
    )


def _extract_review_text(trajectory_path: Path) -> str:
    """Extract the review from a trajectory.json file."""
    data = json.loads(trajectory_path.read_text())
    agent_msgs = [
        step["message"] for step in data.get("steps", [])
        if step.get("source") == "agent" and step.get("message") and len(step["message"]) > 200
    ]
    if not agent_msgs:
        return ""
    # Prefer the last message that looks like a structured review.
    review_markers = ("### Summary", "### Strengths", "### Weaknesses", "### Scores", "**Scores**")
    for msg in reversed(agent_msgs):
        if any(m in msg for m in review_markers):
            return msg
    return agent_msgs[-1]


_JOBS: dict[str, dict] = {}
_JOBS_LOCK_KEY = "_lock"


async def _run_review_job(job_id: str, latex_content: str, title: str, abstract: str) -> None:
    """Run a review end-to-end and update _JOBS[job_id] with the result."""
    import asyncio

    job = _JOBS[job_id]
    job["status"] = "running"

    tmp_root = Path(tempfile.gettempdir()) / f"review_api_{job_id}"
    paper_dir = tmp_root / job_id
    results_dir = tmp_root / "results"
    trials_dir = tmp_root / "trials"

    try:
        _make_task_dir(paper_dir, latex_content, title, abstract)

        cmd = [
            sys.executable, "-u", str(BENCHMARK_SCRIPT),
            "--data-dir", str(tmp_root),
            "--results-dir", str(results_dir),
            "--trials-dir", str(trials_dir),
            "--max-tasks", "1",
            "--k", "1",
            "--skip-judge",
        ]

        proc = await asyncio_run_subprocess(cmd, timeout=1800)

        # Always surface benchmark output so local runner failures
        # sandbox are diagnosable. stdout holds the benchmark logger lines
        # (sandbox setup, model calls, errors); stderr catches tracebacks.
        out_tail = (proc.stdout or "")[-3000:]
        err_tail = (proc.stderr or "")[-3000:]
        job["stdout_tail"] = out_tail
        job["stderr_tail"] = err_tail

        if proc.returncode != 0:
            job["status"] = "error"
            job["error"] = f"benchmark failed with code {proc.returncode}"
            return

        trajectory_path = None
        for traj in results_dir.rglob("trajectory.json"):
            trajectory_path = traj
            break

        if not trajectory_path or not trajectory_path.is_file():
            job["status"] = "error"
            result_files = list(results_dir.rglob("result.json"))
            if result_files:
                try:
                    job["error"] = json.loads(result_files[0].read_text()).get("error", "no trajectory found")
                except Exception:
                    job["error"] = "no trajectory found"
            else:
                job["error"] = "no trajectory found"
            return

        _archive_trajectory(job_id, trajectory_path)
        job["review_text"] = _extract_review_text(trajectory_path)
        job["status"] = "success"

    except asyncio.TimeoutError:
        job["status"] = "timeout"
        job["error"] = "review timed out (30 min)"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        job["finished_at"] = _now_iso()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def handle_review_start(request: web.Request) -> web.Response:
    """Async submit: returns job_id immediately, runs review in background."""
    import asyncio

    body = await request.json()
    latex_content = body.get("latex_content", "")
    title = body.get("title", "")
    abstract = body.get("abstract", "")

    if not latex_content:
        return web.json_response(
            {"error": "latex_content is required"},
            status=400,
        )

    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "review_text": "",
        "error": "",
        "submitted_at": _now_iso(),
        "finished_at": None,
    }

    asyncio.create_task(_run_review_job(job_id, latex_content, title, abstract))

    return web.json_response({"job_id": job_id, "status": "pending"})


async def handle_review_status(request: web.Request) -> web.Response:
    job_id = request.match_info.get("job_id", "")
    job = _JOBS.get(job_id)
    if job is None:
        return web.json_response({"error": "unknown job_id", "status": "not_found"}, status=404)
    return web.json_response(job)


async def handle_review(request: web.Request) -> web.Response:
    """Synchronous submit: blocks until done. Kept for back-compat with direct callers."""
    body = await request.json()
    latex_content = body.get("latex_content", "")
    title = body.get("title", "")
    abstract = body.get("abstract", "")

    if not latex_content:
        return web.json_response(
            {"error": "latex_content is required", "review_text": "", "status": "error"},
            status=400,
        )

    run_id = uuid.uuid4().hex[:12]
    tmp_root = Path(tempfile.gettempdir()) / f"review_api_{run_id}"
    paper_dir = tmp_root / run_id
    results_dir = tmp_root / "results"
    trials_dir = tmp_root / "trials"

    try:
        _make_task_dir(paper_dir, latex_content, title, abstract)

        cmd = [
            sys.executable, "-u", str(BENCHMARK_SCRIPT),
            "--data-dir", str(tmp_root),
            "--results-dir", str(results_dir),
            "--trials-dir", str(trials_dir),
            "--max-tasks", "1",
            "--k", "1",
            "--skip-judge",
        ]

        proc = await asyncio_run_subprocess(cmd, timeout=1800)

        if proc.returncode != 0:
            return web.json_response({
                "error": f"benchmark failed with code {proc.returncode}",
                "review_text": "",
                "status": "error",
            }, status=500)

        trajectory_path = None
        for traj in results_dir.rglob("trajectory.json"):
            trajectory_path = traj
            break

        if not trajectory_path or not trajectory_path.is_file():
            return web.json_response({
                "error": "no trajectory found",
                "review_text": "",
                "status": "error",
            }, status=500)

        _archive_trajectory(run_id, trajectory_path)
        review_text = _extract_review_text(trajectory_path)

        return web.json_response({
            "review_text": review_text,
            "status": "success",
        })

    except asyncio.TimeoutError:
        return web.json_response({
            "error": "review timed out (30 min)",
            "review_text": "",
            "status": "timeout",
        }, status=504)
    except Exception as e:
        return web.json_response({
            "error": str(e),
            "review_text": "",
            "status": "error",
        }, status=500)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def asyncio_run_subprocess(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode or 0,
        stdout=stdout.decode() if stdout else "",
        stderr=stderr.decode() if stderr else "",
    )


def main():
    p = argparse.ArgumentParser(description="Review API server")
    p.add_argument("--port", type=int, default=8282)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    if not BENCHMARK_SCRIPT.is_file():
        print(f"error: benchmark_pass_at_k.py not found at {BENCHMARK_SCRIPT}", file=sys.stderr)
        sys.exit(1)

    # Load local endpoint/runtime configuration.
    env_path = Path(__file__).parent / ".env"
    if env_path.is_file():
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)

    app = web.Application()
    app.router.add_post("/review", handle_review)
    app.router.add_post("/review/start", handle_review_start)
    app.router.add_get("/review/status/{job_id}", handle_review_status)
    app.router.add_get("/health", handle_health)

    print(f"Review API on http://{args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
