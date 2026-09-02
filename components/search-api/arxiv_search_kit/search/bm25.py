"""Reciprocal Rank Fusion (RRF) for combining dense and sparse retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from arxiv_search_kit.models import Paper

logger = logging.getLogger(__name__)

# RRF constant — controls how quickly lower-ranked results lose influence.
# k=60 is the standard value from the original RRF paper (Cormack et al., 2009).
RRF_K = 60


@dataclass
class ScoredCandidate:
    """A paper with its dense, sparse, and fused scores."""
    paper: Paper
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float = 0.0
    sparse_score: float = 0.0
    fused_score: float = 0.0


def reciprocal_rank_fusion(
    dense_results: list[tuple[Paper, float]],
    sparse_results: list[tuple[Paper, float]],
    dense_weight: float = 0.7,
    sparse_weight: float = 0.3,
) -> list[tuple[Paper, float]]:
    """Fuse dense (SPECTER2) and sparse (BM25) results using RRF.

    RRF score for a document d:
        score(d) = w_dense * 1/(k + rank_dense(d)) + w_sparse * 1/(k + rank_sparse(d))

    Documents appearing in both lists get boosted. Documents in only one list
    still contribute. This naturally handles the score scale mismatch between
    dense cosine similarity and BM25 scores.

    Args:
        dense_results: (Paper, similarity_score) from vector search, ordered by rank.
        sparse_results: (Paper, bm25_score) from FTS, ordered by rank.
        dense_weight: Weight for dense retrieval (default 0.7 — SPECTER2 is primary).
        sparse_weight: Weight for sparse retrieval (default 0.3 — BM25 supplements).

    Returns:
        Fused (Paper, rrf_score) list, sorted by fused score descending.
    """
    candidates: dict[str, ScoredCandidate] = {}

    # Process dense results
    for rank, (paper, score) in enumerate(dense_results):
        cand = candidates.get(paper.arxiv_id)
        if cand is None:
            cand = ScoredCandidate(paper=paper)
            candidates[paper.arxiv_id] = cand
        cand.dense_rank = rank
        cand.dense_score = score

    # Process sparse results
    for rank, (paper, score) in enumerate(sparse_results):
        cand = candidates.get(paper.arxiv_id)
        if cand is None:
            cand = ScoredCandidate(paper=paper)
            candidates[paper.arxiv_id] = cand
        cand.sparse_rank = rank
        cand.sparse_score = score

    # Compute RRF scores
    for cand in candidates.values():
        rrf_dense = 0.0
        rrf_sparse = 0.0

        if cand.dense_rank is not None:
            rrf_dense = 1.0 / (RRF_K + cand.dense_rank + 1)  # +1 because ranks are 0-indexed

        if cand.sparse_rank is not None:
            rrf_sparse = 1.0 / (RRF_K + cand.sparse_rank + 1)

        cand.fused_score = dense_weight * rrf_dense + sparse_weight * rrf_sparse

    # Sort by fused score
    sorted_candidates = sorted(candidates.values(), key=lambda c: c.fused_score, reverse=True)

    return [(c.paper, c.fused_score) for c in sorted_candidates]
