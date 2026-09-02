from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "components/ai-scientist" / "scripts" / "prepare_paper_inputs.py"


def test_local_paths_are_staged_and_rewritten(tmp_path: Path) -> None:
    paper = tmp_path / "draft.tex"
    paper.write_text("paper")
    code = tmp_path / "source"
    code.mkdir()
    (code / "train.py").write_text("print('ok')")
    reviews = tmp_path / "reviews.md"
    reviews.write_text("review")
    task = tmp_path / "paper.json"
    task.write_text(json.dumps({
        "Name": "paper",
        "Title": "Paper",
        "Task": "Read draft.tex and reviews.md, then run source/train.py.",
        "Paper": {"local_path": "draft.tex", "code_path": "source"},
        "Human_Reviews_From_OpenReview": {"source_file": "reviews.md"},
    }))
    inputs = tmp_path / "staged"
    resolved = tmp_path / "resolved.json"

    subprocess.run(
        [sys.executable, str(PREPARE), str(task), str(inputs), str(resolved)],
        check=True,
    )

    result = json.loads(resolved.read_text())
    assert result["Paper"] == {
        "local_path": "/app/inputs/paper.tex",
        "code_path": "/app/inputs/code",
    }
    assert result["Human_Reviews_From_OpenReview"]["source_file"] == "/app/inputs/reviews.md"
    assert "/app/inputs/paper.tex" in result["Task"]
    assert "/app/inputs/reviews.md" in result["Task"]
    assert "/app/inputs/code/train.py" in result["Task"]
    assert (inputs / "paper.tex").read_text() == "paper"
    assert (inputs / "code" / "train.py").read_text() == "print('ok')"
    assert (inputs / "reviews.md").read_text() == "review"


def test_missing_local_path_fails(tmp_path: Path) -> None:
    task = tmp_path / "paper.json"
    task.write_text(json.dumps({
        "Name": "paper",
        "Title": "Paper",
        "Task": "Read it.",
        "Paper": {"local_path": "missing.tex"},
    }))

    result = subprocess.run(
        [sys.executable, str(PREPARE), str(task), str(tmp_path / "staged"), str(tmp_path / "out.json")],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does not exist" in result.stderr
