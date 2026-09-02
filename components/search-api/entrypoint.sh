#!/bin/sh
# search-api entrypoint: download index on first start, then run server.
set -e

INDEX_DIR="${GEMINI_INDEX_DIR:-/data/arxiv_search_kit/arxiv_gemini_index}"
HF_REPO="${GEMINI_INDEX_HF_REPO:?set GEMINI_INDEX_HF_REPO to the dataset repo hosting the prebuilt index}"
PORT="${PORT:-8081}"

if [ ! -f "${INDEX_DIR}/.complete" ]; then
    # If the dir already has the lancedb files (e.g. mounted from a prior install),
    # mark complete and skip the download.
    if [ -d "${INDEX_DIR}" ] && [ "$(find "${INDEX_DIR}" -maxdepth 2 -name '*.lance' -print -quit 2>/dev/null)" ]; then
        echo "[search-api] Found existing index at ${INDEX_DIR}, skipping download"
        touch "${INDEX_DIR}/.complete"
    else
        echo "[search-api] Index not found at ${INDEX_DIR}"
        echo "[search-api] Downloading from huggingface.co/${HF_REPO} (~10 GB, one-time, 5-15 min)..."
        mkdir -p "${INDEX_DIR}"
        python -c "
import os
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='${HF_REPO}',
    repo_type='dataset',
    local_dir='${INDEX_DIR}',
    local_dir_use_symlinks=False,
)
"
        touch "${INDEX_DIR}/.complete"
        echo "[search-api] Index ready at ${INDEX_DIR}"
    fi
else
    echo "[search-api] Reusing existing index at ${INDEX_DIR}"
fi

if [ -z "${GEMINI_API_KEY}" ]; then
    echo "[search-api] WARNING: GEMINI_API_KEY is not set. /batch_search and /find_related need it for query embeddings." >&2
fi

echo "[search-api] Starting server on port ${PORT}"
exec python /app/search_api.py \
    --port "${PORT}" \
    --gemini-index-dir "${INDEX_DIR}"
