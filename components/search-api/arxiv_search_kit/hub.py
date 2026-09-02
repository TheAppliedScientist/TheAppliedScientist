"""HuggingFace Hub integration — download/upload pre-built indexes and metadata.

For most users, the SDK auto-downloads the pre-built LanceDB index from HF
on first use. No manual setup needed.

For researchers rebuilding the index, this also supports downloading raw
metadata JSONL and uploading built artifacts.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# HF repo where we host the pre-built index and metadata
HF_REPO_ID = "anonymousatom/arxiv-search-index"
HF_GEMINI_REPO_ID = os.environ.get("GEMINI_INDEX_HF_REPO", "")
HF_METADATA_REPO_ID = "anonymousatom/arxiv-metadata"

# Default local cache directory
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "arxiv_search_kit"

# Files in the HF repo
INDEX_DIR_NAME = "arxiv_index"
GEMINI_INDEX_DIR_NAME = "arxiv_gemini_index"
METADATA_FILENAME = "arxiv_metadata.jsonl"


def get_default_index_dir() -> Path:
    """Get the default local path for the pre-built index."""
    return DEFAULT_CACHE_DIR / INDEX_DIR_NAME


def download_gemini_index(
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Download the Gemini-2 embedding index from HuggingFace.

    Args:
        cache_dir: Local directory to store the index.
        force: Re-download even if cached.

    Returns:
        Path to the local index directory.
    """
    from huggingface_hub import snapshot_download

    cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    index_dir = cache_dir / GEMINI_INDEX_DIR_NAME

    if index_dir.exists() and not force:
        if any(index_dir.iterdir()):
            logger.info(f"Using cached Gemini index at {index_dir}")
            return index_dir

    logger.info(f"Downloading Gemini-2 index from HF: {HF_GEMINI_REPO_ID}")
    logger.info("This is a one-time download (~10 GB). Subsequent loads will be instant.")

    try:
        downloaded_path = snapshot_download(
            repo_id=HF_GEMINI_REPO_ID,
            repo_type="dataset",
            local_dir=str(index_dir),
        )
        logger.info(f"Gemini index downloaded to {downloaded_path}")
        return Path(downloaded_path)
    except Exception as e:
        raise RuntimeError(
            f"Failed to download Gemini index from HF ({HF_GEMINI_REPO_ID}). "
            f"Error: {e}"
        ) from e


def download_index(
    cache_dir: str | Path | None = None,
    repo_id: str = HF_REPO_ID,
    force: bool = False,
) -> Path:
    """Download the pre-built LanceDB index from HuggingFace.

    This is called automatically by ArxivClient if no index_dir is provided.
    The index is cached locally so subsequent calls are instant.

    Args:
        cache_dir: Local directory to store the index. Defaults to ~/.cache/arxiv_search_kit/.
        repo_id: HF repo containing the pre-built index.
        force: Re-download even if cached.

    Returns:
        Path to the local index directory.
    """
    from huggingface_hub import snapshot_download

    cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    index_dir = cache_dir / INDEX_DIR_NAME

    if index_dir.exists() and not force:
        # Check if index looks valid (has the lance directory)
        if any(index_dir.iterdir()):
            logger.info(f"Using cached index at {index_dir}")
            return index_dir

    logger.info(f"Downloading pre-built index from HF: {repo_id}")
    logger.info("This is a one-time download (~2-4 GB). Subsequent loads will be instant.")

    try:
        downloaded_path = snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            local_dir=str(cache_dir),
            allow_patterns=[f"{INDEX_DIR_NAME}/**"],
        )
        index_dir = Path(downloaded_path) / INDEX_DIR_NAME
        logger.info(f"Index downloaded to {index_dir}")
        return index_dir
    except Exception as e:
        raise RuntimeError(
            f"Failed to download index from HF ({repo_id}). "
            f"Make sure you have internet access and `huggingface_hub` installed. "
            f"Alternatively, build the index locally with: "
            f"python -m arxiv_search_kit.scripts.build_index all --output-dir ./arxiv_index\n"
            f"Error: {e}"
        ) from e


def download_metadata(
    output_path: str | Path | None = None,
    repo_id: str = HF_METADATA_REPO_ID,
) -> Path:
    """Download the pre-harvested metadata JSONL from HuggingFace.

    For researchers who want to rebuild the index with custom settings.
    Much faster than re-crawling ArXiv OAI-PMH (~seconds vs ~hours).

    Args:
        output_path: Where to save the JSONL. Defaults to cache dir.
        repo_id: HF repo containing the metadata.

    Returns:
        Path to the downloaded JSONL file.
    """
    from huggingface_hub import hf_hub_download

    if output_path is None:
        output_path = DEFAULT_CACHE_DIR / METADATA_FILENAME

    output_path = Path(output_path)

    if output_path.exists():
        logger.info(f"Metadata already cached at {output_path}")
        return output_path

    logger.info(f"Downloading metadata from HF: {repo_id}")

    try:
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=METADATA_FILENAME,
            repo_type="dataset",
            local_dir=str(output_path.parent),
        )
        logger.info(f"Metadata downloaded to {downloaded}")
        return Path(downloaded)
    except Exception as e:
        raise RuntimeError(
            f"Failed to download metadata from HF ({repo_id}). "
            f"You can harvest directly from ArXiv instead: "
            f"python -m arxiv_search_kit.scripts.build_index download --output ./arxiv_metadata.jsonl\n"
            f"Error: {e}"
        ) from e


def upload_index(
    index_dir: str | Path,
    repo_id: str = HF_REPO_ID,
    token: str | None = None,
) -> str:
    """Upload a built index to HuggingFace (for maintainers).

    Args:
        index_dir: Path to the LanceDB index directory.
        repo_id: HF repo to upload to.
        token: HF write token. Defaults to HF_TOKEN env var.

    Returns:
        URL of the uploaded repo.
    """
    from huggingface_hub import HfApi

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("HF token required. Set HF_TOKEN env var or pass token=")

    api = HfApi(token=token)

    # Create repo if it doesn't exist
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, private=False)

    logger.info(f"Uploading index from {index_dir} to {repo_id}")
    api.upload_folder(
        folder_path=str(index_dir),
        path_in_repo=INDEX_DIR_NAME,
        repo_id=repo_id,
        repo_type="dataset",
    )

    url = f"https://huggingface.co/datasets/{repo_id}"
    logger.info(f"Index uploaded to {url}")
    return url


def upload_metadata(
    metadata_path: str | Path,
    repo_id: str = HF_METADATA_REPO_ID,
    token: str | None = None,
) -> str:
    """Upload harvested metadata JSONL to HuggingFace (for maintainers).

    Args:
        metadata_path: Path to the JSONL file.
        repo_id: HF repo to upload to.
        token: HF write token.

    Returns:
        URL of the uploaded repo.
    """
    from huggingface_hub import HfApi

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("HF token required. Set HF_TOKEN env var or pass token=")

    api = HfApi(token=token)

    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, private=False)

    logger.info(f"Uploading metadata from {metadata_path} to {repo_id}")
    api.upload_file(
        path_or_fileobj=str(metadata_path),
        path_in_repo=METADATA_FILENAME,
        repo_id=repo_id,
        repo_type="dataset",
    )

    url = f"https://huggingface.co/datasets/{repo_id}"
    logger.info(f"Metadata uploaded to {url}")
    return url