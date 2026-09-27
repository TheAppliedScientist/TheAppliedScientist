"""Download the published Search index before hosted traffic is served."""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    destination = Path(os.environ["GEMINI_INDEX_DIR"])
    marker = destination / ".complete"
    if marker.is_file():
        return
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=os.environ["GEMINI_INDEX_HF_REPO"],
        repo_type="dataset",
        local_dir=str(destination),
    )
    marker.touch()


if __name__ == "__main__":
    main()
