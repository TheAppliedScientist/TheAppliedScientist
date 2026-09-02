from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "experiment-compatibility" / "locked-files.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reference_files_are_unchanged() -> None:
    manifest = json.loads(MANIFEST.read_text())
    for relative, expected in manifest["locked_files"].items():
        assert digest(ROOT / relative) == expected, relative
