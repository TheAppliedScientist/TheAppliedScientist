#!/usr/bin/env python3
"""Copy local paths named by a paper JSON into the run workspace.

Paths are resolved relative to the JSON file. The resulting JSON points at the
copies under /app/inputs, which works in both Docker and native runs.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


def copy_input(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=False)
    else:
        shutil.copy2(source, destination)


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: prepare_paper_inputs.py PAPER_JSON INPUT_DIR OUTPUT_JSON", file=sys.stderr)
        return 2

    json_path = Path(sys.argv[1]).resolve()
    input_dir = Path(sys.argv[2]).resolve()
    output_path = Path(sys.argv[3]).resolve()
    data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))

    fields: list[tuple[dict[str, Any], str, str]] = []
    paper = data.get("Paper")
    if isinstance(paper, dict):
        fields.extend((paper, key, target) for key, target in (
            ("local_path", "paper"),
            ("code_path", "code"),
        ))

    for review_key in ("Human_Reviews_From_OpenReview", "Human_Reviews"):
        reviews = data.get(review_key)
        if isinstance(reviews, dict):
            fields.append((reviews, "source_file", "reviews"))

    replacements: dict[str, str] = {}
    for owner, key, target_name in fields:
        raw = owner.get(key)
        if not isinstance(raw, str) or not raw or raw.startswith("/app/inputs/"):
            continue
        source = Path(raw).expanduser()
        if not source.is_absolute():
            source = json_path.parent / source
        source = source.resolve()
        if not source.exists():
            print(f"Local input named by {key} does not exist: {source}", file=sys.stderr)
            return 1

        suffix = source.suffix if source.is_file() else ""
        destination = input_dir / f"{target_name}{suffix}"
        if source.is_dir() and destination.is_relative_to(source):
            print(f"Local input directory cannot contain the run staging directory: {source}", file=sys.stderr)
            return 1
        copy_input(source, destination)
        runtime_path = f"/app/inputs/{destination.name}"
        owner[key] = runtime_path
        replacements[raw] = runtime_path
        print(f"Staged {key}: {source}")

    def replace_paths(value: Any) -> Any:
        if isinstance(value, str):
            if value.startswith("/app/inputs/"):
                return value
            for old, new in replacements.items():
                left = r"(?<![A-Za-z0-9_])" if old[:1].isalnum() else ""
                right = r"(?![A-Za-z0-9_])" if old[-1:].isalnum() else ""
                value = re.sub(left + re.escape(old) + right, lambda _: new, value)
            return value
        if isinstance(value, list):
            return [replace_paths(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_paths(item) for key, item in value.items()}
        return value

    data = replace_paths(data)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
