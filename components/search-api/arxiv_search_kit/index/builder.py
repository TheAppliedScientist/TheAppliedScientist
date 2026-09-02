"""Build LanceDB index from ArXiv metadata + SPECTER2 embeddings."""

from __future__ import annotations

import logging
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

from arxiv_search_kit.exceptions import IndexBuildError
from arxiv_search_kit.index.download import iter_metadata_from_jsonl, iter_metadata_from_kaggle
from arxiv_search_kit.index.embedder import Specter2Embedder
from arxiv_search_kit.index.store import TABLE_NAME

logger = logging.getLogger(__name__)


def build_index(
    metadata_path: str | Path,
    output_dir: str | Path,
    device: str = "cuda",
    batch_size: int = 64,
    categories: list[str] | None = None,
    num_partitions: int = 256,
    num_sub_vectors: int = 96,
) -> None:
    """Build a LanceDB index from ArXiv metadata.

    Args:
        metadata_path: Path to Kaggle arxiv-metadata-oai-snapshot.json.
        output_dir: Directory to store the LanceDB database.
        device: Torch device for SPECTER2 ("cuda" or "cpu").
        batch_size: Embedding batch size.
        categories: Categories to filter to. Defaults to all target categories.
        num_partitions: IVF-PQ partitions for the vector index.
        num_sub_vectors: PQ sub-vectors for compression.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Building index at {output_dir}")
    logger.info(f"Metadata source: {metadata_path}")
    logger.info(f"Device: {device}, Batch size: {batch_size}")

    # Initialize embedder
    embedder = Specter2Embedder(device=device, batch_size=batch_size)

    # Connect to LanceDB
    db = lancedb.connect(str(output_dir))

    # Define schema
    schema = pa.schema([
        pa.field("vector", pa.list_(pa.float32(), 768)),
        pa.field("arxiv_id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("abstract", pa.string()),
        pa.field("authors", pa.string()),  # JSON string
        pa.field("categories", pa.string()),  # JSON string
        pa.field("primary_category", pa.string()),
        pa.field("published", pa.string()),
        pa.field("updated", pa.string()),
        pa.field("doi", pa.string()),
        pa.field("journal_ref", pa.string()),
        pa.field("comment", pa.string()),
    ])

    # Stream papers — auto-detect JSONL (from OAI-PMH download) vs Kaggle JSON
    metadata_path = Path(metadata_path)
    if metadata_path.suffix == ".jsonl" or metadata_path.name.endswith(".jsonl"):
        logger.info("Detected JSONL format (from OAI-PMH download)")
        papers_iter = iter_metadata_from_jsonl(metadata_path)
    else:
        logger.info("Detected Kaggle JSON format")
        papers_iter = iter_metadata_from_kaggle(metadata_path, categories)
    table = None
    total_papers = 0

    try:
        for batch_papers, batch_embeddings in embedder.embed_papers_iter(papers_iter):
            # Build records for this batch
            records = []
            for paper, embedding in zip(batch_papers, batch_embeddings):
                record = {
                    "vector": embedding.tolist(),
                    "arxiv_id": paper["arxiv_id"],
                    "title": paper["title"],
                    "abstract": paper["abstract"],
                    "authors": paper["authors"],
                    "categories": paper["categories"],
                    "primary_category": paper["primary_category"],
                    "published": paper["published"],
                    "updated": paper["updated"],
                    "doi": paper["doi"],
                    "journal_ref": paper["journal_ref"],
                    "comment": paper["comment"],
                }
                records.append(record)

            if table is None:
                table = db.create_table(TABLE_NAME, records, schema=schema, mode="overwrite")
            else:
                table.add(records)

            total_papers += len(records)

            if total_papers % 10_000 == 0:
                logger.info(f"Indexed {total_papers:,} papers")

    except Exception as e:
        raise IndexBuildError(f"Failed to build index: {e}") from e

    if table is None or total_papers == 0:
        raise IndexBuildError("No papers were indexed. Check metadata path and category filters.")

    logger.info(f"Indexed {total_papers:,} papers total")

    # Build vector index for fast ANN search
    logger.info(f"Building IVF-PQ vector index (partitions={num_partitions}, sub_vectors={num_sub_vectors})")
    try:
        table.create_index(
            metric="cosine",
            num_partitions=num_partitions,
            num_sub_vectors=num_sub_vectors,
        )
    except Exception as e:
        raise IndexBuildError(f"Failed to build vector index: {e}") from e

    # Build full-text search (BM25) indexes — one per field (LanceDB requirement)
    for field in ["title", "abstract"]:
        logger.info(f"Building FTS (BM25) index on '{field}'")
        try:
            table.create_fts_index([field], replace=True)
        except Exception as e:
            logger.warning(f"FTS index on '{field}' failed (non-fatal): {e}")

    logger.info(f"Index build complete. {total_papers:,} papers indexed at {output_dir}")


def add_papers_to_index(
    output_dir: str | Path,
    papers: list[dict],
    embeddings: np.ndarray,
) -> int:
    """Add new papers to an existing index (for incremental updates).

    Args:
        output_dir: Path to existing LanceDB database.
        papers: List of paper dicts.
        embeddings: Corresponding SPECTER2 embeddings.

    Returns:
        Number of papers added.
    """
    db = lancedb.connect(str(output_dir))
    table = db.open_table(TABLE_NAME)

    records = []
    for paper, embedding in zip(papers, embeddings):
        records.append({
            "vector": embedding.tolist(),
            **paper,
        })

    table.add(records)
    logger.info(f"Added {len(records)} papers to index")
    return len(records)