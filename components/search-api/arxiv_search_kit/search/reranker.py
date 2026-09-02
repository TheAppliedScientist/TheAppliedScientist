"""Graph-based re-ranking via Personalized PageRank on candidate similarity graph."""

from __future__ import annotations

import logging

import networkx as nx
import numpy as np

logger = logging.getLogger(__name__)


def compute_reranked_scores(
    candidate_ids: list[str],
    candidate_vectors: np.ndarray,
    candidate_similarities: list[float],
    candidate_categories: list[list[str]],
    seed_ids: list[str] | None = None,
    query_categories: list[str] | None = None,
    k_neighbors: int = 8,
    alpha_similarity: float = 0.6,
    beta_pagerank: float = 0.3,
    gamma_category: float = 0.1,
    pagerank_alpha: float = 0.85,
) -> list[tuple[str, float]]:
    """Re-rank candidates using graph-based Personalized PageRank.

    Pipeline:
    1. Build k-NN similarity graph from candidate embeddings
    2. Run Personalized PageRank seeded from seed papers
    3. Combine: final_score = α*similarity + β*pagerank + γ*category_overlap

    Args:
        candidate_ids: ArXiv IDs of candidates.
        candidate_vectors: Embeddings matrix (N x 768).
        candidate_similarities: Initial cosine similarity scores from LanceDB.
        candidate_categories: List of category lists for each candidate.
        seed_ids: IDs to seed PageRank from. If None, uses top-3 by similarity.
        query_categories: Categories to boost overlap for.
        k_neighbors: Number of neighbors per node in the similarity graph.
        alpha_similarity: Weight for initial similarity score.
        beta_pagerank: Weight for PageRank score.
        gamma_category: Weight for category overlap bonus.
        pagerank_alpha: PageRank damping factor.

    Returns:
        List of (arxiv_id, final_score) sorted by final_score descending.
    """
    n = len(candidate_ids)
    if n == 0:
        return []
    if n <= 3:
        # Too few candidates for graph analysis, return as-is
        return list(zip(candidate_ids, candidate_similarities))

    # Step 1: Build k-NN similarity graph
    graph = _build_knn_graph(candidate_ids, candidate_vectors, k_neighbors)

    # Step 2: Determine seed nodes for Personalized PageRank
    if seed_ids is None:
        # Use top-3 by initial similarity
        sorted_by_sim = sorted(
            zip(candidate_ids, candidate_similarities),
            key=lambda x: x[1],
            reverse=True,
        )
        seed_ids = [cid for cid, _ in sorted_by_sim[:3]]

    # Build personalization dict (uniform over seed nodes)
    personalization = {}
    seed_set = set(seed_ids)
    for cid in candidate_ids:
        if cid in seed_set:
            personalization[cid] = 1.0 / len(seed_ids)
        else:
            personalization[cid] = 0.0

    # Step 3: Run Personalized PageRank
    try:
        pagerank_scores = nx.pagerank(
            graph,
            alpha=pagerank_alpha,
            personalization=personalization,
            max_iter=100,
            tol=1e-6,
        )
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank failed to converge, using uniform scores")
        pagerank_scores = {cid: 1.0 / n for cid in candidate_ids}

    # Normalize PageRank scores to [0, 1]
    pr_values = list(pagerank_scores.values())
    pr_min, pr_max = min(pr_values), max(pr_values)
    pr_range = pr_max - pr_min if pr_max > pr_min else 1.0
    normalized_pr = {
        cid: (score - pr_min) / pr_range for cid, score in pagerank_scores.items()
    }

    # Step 4: Compute category overlap bonus
    category_bonuses = _compute_category_overlap(
        candidate_ids, candidate_categories, query_categories
    )

    # Step 5: Combine scores
    final_scores = []
    for i, cid in enumerate(candidate_ids):
        sim_score = candidate_similarities[i]
        pr_score = normalized_pr.get(cid, 0.0)
        cat_bonus = category_bonuses.get(cid, 0.0)

        final = (
            alpha_similarity * sim_score
            + beta_pagerank * pr_score
            + gamma_category * cat_bonus
        )
        final_scores.append((cid, final))

    # Sort by final score descending
    final_scores.sort(key=lambda x: x[1], reverse=True)
    return final_scores


def _build_knn_graph(
    candidate_ids: list[str],
    candidate_vectors: np.ndarray,
    k: int,
) -> nx.Graph:
    """Build a k-NN similarity graph from candidate embeddings.

    Each node is a paper. Edges connect each paper to its k most similar
    neighbors, weighted by cosine similarity.
    """
    n = len(candidate_ids)
    k = min(k, n - 1)

    # Compute pairwise cosine similarity
    # Normalize vectors
    norms = np.linalg.norm(candidate_vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    normalized = candidate_vectors / norms
    sim_matrix = normalized @ normalized.T

    # Build graph
    graph = nx.Graph()
    for i, cid in enumerate(candidate_ids):
        graph.add_node(cid)

    for i in range(n):
        # Get top-k neighbors (excluding self)
        sims = sim_matrix[i].copy()
        sims[i] = -1  # exclude self
        top_k_indices = np.argsort(sims)[-k:]

        for j in top_k_indices:
            if sims[j] > 0:
                graph.add_edge(
                    candidate_ids[i],
                    candidate_ids[j],
                    weight=float(sims[j]),
                )

    return graph


def _compute_category_overlap(
    candidate_ids: list[str],
    candidate_categories: list[list[str]],
    query_categories: list[str] | None,
) -> dict[str, float]:
    """Compute category overlap bonus for each candidate.

    Returns a score in [0, 1] based on how much the candidate's categories
    overlap with the query/context paper's categories.
    """
    if not query_categories:
        return {cid: 0.0 for cid in candidate_ids}

    query_set = set(query_categories)
    bonuses = {}

    for cid, cats in zip(candidate_ids, candidate_categories):
        if not cats:
            bonuses[cid] = 0.0
            continue
        cat_set = set(cats)
        overlap = len(query_set & cat_set)
        total = len(query_set | cat_set)
        bonuses[cid] = overlap / total if total > 0 else 0.0

    return bonuses