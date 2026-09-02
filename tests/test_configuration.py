from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_no_secret_file_is_committed() -> None:
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()


def test_examples_do_not_contain_live_keys() -> None:
    text = (ROOT / ".env.example").read_text()
    assert "sk-" not in text
    assert "AIza" not in text
    assert "e2b_" not in text


def test_no_remote_sandbox_runtime() -> None:
    checked = [ROOT / "tas", ROOT / ".env.example", ROOT / "compose.yaml"]
    assert all("e2b" not in path.read_text().lower() for path in checked)


def test_component_selectors_are_documented() -> None:
    help_text = (ROOT / "README.md").read_text()
    for command in ("setup search", "setup reviewer", "setup scientist"):
        assert command in help_text


def test_compose_defines_all_components() -> None:
    text = (ROOT / "compose.yaml").read_text()
    for service in ("search-api:", "ai-reviewer:", "ai-scientist:"):
        assert service in text


def test_documented_paper_task_examples_are_valid() -> None:
    for path in (ROOT / "components/ai-scientist" / "examples" / "ideas").glob("*.json"):
        data = json.loads(path.read_text())
        assert all(isinstance(data.get(key), str) and data[key] for key in ("Name", "Title", "Task"))


def test_private_input_sidecars_are_staged_in_both_runtimes() -> None:
    runner = (ROOT / "components/ai-scientist" / "run.sh").read_text()
    assert 'IDEA_INPUT_DIR="${IDEA_JSON%.json}.inputs"' in runner
    assert '"$NATIVE_WORKSPACE/inputs"' in runner
    for name in ("Dockerfile.cpu", "Dockerfile.gpu"):
        dockerfile = ROOT / "components/ai-scientist" / "harbor-task" / "environment" / name
        assert "COPY inputs/ /app/inputs/" in dockerfile.read_text()


def test_gpu_image_protects_its_cuda_pytorch_build() -> None:
    dockerfile = (ROOT / "components/ai-scientist/harbor-task/environment/Dockerfile.gpu").read_text()
    assert "UV_CONSTRAINT=" in dockerfile
    assert "PIP_CONSTRAINT=" in dockerfile
    assert "importlib.metadata" in dockerfile
    for package in ("torch", "torchvision", "torchaudio"):
        assert package in dockerfile
    assert "torch==2.10.0" not in dockerfile


def test_fresh_workspace_allows_repository_clone_target() -> None:
    for name in ("Dockerfile.cpu", "Dockerfile.gpu"):
        dockerfile = (ROOT / "components/ai-scientist/harbor-task/environment" / name).read_text()
        setup = dockerfile.split("# Copy previous artifacts", 1)[0]
        assert "mkdir -p /app/experiment_codebase" not in setup


def test_up_waits_for_api_health() -> None:
    operator = (ROOT / "tas").read_text()
    assert "wait_for_health(component, env)" in operator
    assert "Waiting for {component} to become ready" in operator
