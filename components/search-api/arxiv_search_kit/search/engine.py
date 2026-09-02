"""Search engine — hybrid SPECTER2 + BM25 retrieval with graph-based re-ranking."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from arxiv_search_kit.categories import get_categories_for_conference
from arxiv_search_kit.index.embedder import GeminiEmbedder, Specter2Embedder
from arxiv_search_kit.index.store import IndexStore
from arxiv_search_kit.models import Paper, SearchResult
from arxiv_search_kit.search.query import expand_query_for_embedding
from arxiv_search_kit.search.reranker import compute_reranked_scores

logger = logging.getLogger(__name__)

# Over-fetch multiplier for re-ranking headroom
RERANK_OVERFETCH = 5


class SearchEngine:
    """Orchestrates the full search pipeline."""

    def __init__(
        self,
        store: IndexStore,
        embedder: Specter2Embedder | GeminiEmbedder,
        rerank: bool = True,
    ):
        self._store = store
        self._embedder = embedder
        self._rerank = rerank

    def search(
        self,
        query: str,
        max_results: int = 20,
        categories: list[str] | None = None,
        conference: str | None = None,
        year: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        context_paper_id: str | None = None,
        context_title: str | None = None,
        context_abstract: str | None = None,
        rerank: bool | None = None,
        sort_by: str | None = None,
        min_citations: int | None = None,
        details: str = "default",
    ) -> SearchResult:
        """Execute the full search pipeline: embed → retrieve → rerank → enrich.

        Args:
            query: Keyword search query.
            max_results: Number of papers to return.
            categories: ArXiv categories to filter (e.g. ``["cs.CV"]``).
            conference: Conference name (e.g. ``"CVPR"``), maps to categories.
            year: Filter to this publication year.
            date_from: Published after this date (YYYY-MM-DD).
            date_to: Published before this date (YYYY-MM-DD).
            context_paper_id: ArXiv ID to bias results toward.
            context_title: Title of context paper (alternative to ID).
            context_abstract: Abstract of context paper.
            rerank: Override default PageRank re-ranking.
            sort_by: ``"relevance"`` | ``"citations"`` | ``"date"`` | ``"importance"``.
            min_citations: Minimum citation count filter (uses S2 API).

        Returns:
            SearchResult with ranked papers.
        """
        t_start = time.time()
        do_rerank = rerank if rerank is not None else self._rerank

        # Resolve conference to categories
        if conference and not categories:
            categories = get_categories_for_conference(conference)
            if not categories:
                logger.warning(f"Unknown conference '{conference}', searching all categories")

        # Build context paper embedding if available
        context_vector = None
        context_cats = None
        if context_paper_id:
            context_vector = self._store.get_paper_vector(context_paper_id)
            if context_vector is not None:
                try:
                    ctx_paper = self._store.get_paper(context_paper_id)
                    context_cats = ctx_paper.categories
                    if not categories:
                        categories = context_cats
                except Exception:
                    pass

        if context_title and context_vector is None:
            # Embed context from raw text
            context_vector = self._embedder.embed_paper(
                context_title, context_abstract or ""
            )

        # Embed the query — GeminiEmbedder handles its own formatting internally
        if isinstance(self._embedder, Specter2Embedder):
            query_text = expand_query_for_embedding(query, context_title, context_abstract)
            query_vector = self._embedder.embed_query(query_text)
        else:
            query_vector = self._embedder.embed_query(query)

        # Blend query + context vectors
        if context_vector is not None:
            query_vector = _blend_vectors(query_vector, context_vector, alpha=0.4)

        # Build filter clause
        where_clause = self._store.build_where_clause(
            categories=categories,
            date_from=date_from,
            date_to=date_to,
            year=year,
        )

        # Retrieve candidates via hybrid search (dense SPECTER2 + sparse BM25)
        fetch_limit = max_results * RERANK_OVERFETCH if do_rerank else max_results
        candidates = self._store.hybrid_search(
            query=query,
            query_vector=query_vector,
            limit=fetch_limit,
            where=where_clause,
        )

        if not candidates:
            return SearchResult(
                papers=[],
                query=query,
                total_candidates=0,
                search_time_ms=_elapsed_ms(t_start),
            )

        # Filter out context paper from results
        if context_paper_id:
            candidates = [(p, s) for p, s in candidates if p.arxiv_id != context_paper_id]

        # Re-rank with graph-based Personalized PageRank
        if do_rerank and len(candidates) > 3:
            papers = self._rerank_candidates(
                candidates,
                seed_id=context_paper_id,
                query_categories=categories or context_cats,
                max_results=max_results,
            )
        else:
            papers = [p for p, _ in candidates[:max_results]]

        # Post-search: sort by citations/importance or filter by min_citations
        if sort_by in ("citations", "importance") or min_citations is not None:
            papers = _enrich_and_filter(
                papers, sort_by=sort_by, min_citations=min_citations
            )
        elif sort_by == "date":
            papers.sort(key=lambda p: p.published, reverse=True)

        return SearchResult(
            papers=papers,
            query=query,
            total_candidates=len(candidates),
            search_time_ms=_elapsed_ms(t_start),
            details=details,
        )

    def search_title(
        self,
        title: str,
        threshold: float = 0.90,
    ) -> Paper | None:
        """Find a paper by title. Prefer S2 /paper/search/match for production use."""
        query_vector = self._embedder.embed_paper(title, title)
        candidates = self._store.vector_search(query_vector=query_vector, limit=50)

        title_norm = _normalize_title(title)
        best_match = None
        best_ratio = 0.0

        for paper, _ in candidates:
            candidate_norm = _normalize_title(paper.title)
            ratio = _title_similarity_strict(title_norm, candidate_norm)
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = paper

        if best_match and best_ratio >= threshold:
            best_match.similarity_score = best_ratio
            return best_match
        return None

    def find_related(
        self,
        arxiv_id: str,
        max_results: int = 20,
        categories: list[str] | None = None,
        rerank: bool | None = None,
        details: str = "default",
    ) -> SearchResult:
        """Find papers most related to a given paper.

        Uses the paper's stored SPECTER2 embedding directly (no keyword query needed).

        Args:
            arxiv_id: ArXiv ID of the paper.
            max_results: Number of results.
            categories: Filter to these categories.
            rerank: Override default re-ranking.

        Returns:
            SearchResult with related papers.
        """
        t_start = time.time()
        do_rerank = rerank if rerank is not None else self._rerank

        # Get paper's stored vector
        paper_vector = self._store.get_paper_vector(arxiv_id)
        if paper_vector is None:
            # Paper not in index — can't find related
            return SearchResult(
                papers=[], query=f"related:{arxiv_id}",
                total_candidates=0, search_time_ms=_elapsed_ms(t_start),
            )

        # Get paper's categories for filtering/boosting
        try:
            source_paper = self._store.get_paper(arxiv_id)
            if not categories:
                categories = source_paper.categories
        except Exception:
            pass

        where_clause = self._store.build_where_clause(categories=categories)

        fetch_limit = max_results * RERANK_OVERFETCH if do_rerank else max_results + 1
        candidates = self._store.vector_search(
            query_vector=paper_vector,
            limit=fetch_limit,
            where=where_clause,
        )

        # Filter out the source paper itself
        candidates = [(p, s) for p, s in candidates if p.arxiv_id != arxiv_id]

        if do_rerank and len(candidates) > 3:
            papers = self._rerank_candidates(
                candidates,
                seed_id=arxiv_id,
                query_categories=categories,
                max_results=max_results,
            )
        else:
            papers = [p for p, _ in candidates[:max_results]]

        return SearchResult(
            papers=papers,
            query=f"related:{arxiv_id}",
            total_candidates=len(candidates),
            search_time_ms=_elapsed_ms(t_start),
            details=details,
        )

    def batch_search(
        self,
        queries: list[str],
        max_results: int = 20,
        categories: list[str] | None = None,
        conference: str | None = None,
        year: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        context_paper_id: str | None = None,
        context_title: str | None = None,
        context_abstract: str | None = None,
        sort_by: str | None = None,
        min_citations: int | None = None,
        details: str = "default",
    ) -> SearchResult:
        """Run multiple queries, merge and deduplicate results.

        Args:
            queries: List of keyword queries.
            max_results: Results per query (all unique papers returned after merge).
            sort_by: ``"relevance"`` | ``"citations"`` | ``"date"`` | ``"importance"``.
            min_citations: Minimum citation count filter (uses S2 API).

        Other arguments are the same as :meth:`search`.

        Returns:
            Merged SearchResult.
        """
        t_start = time.time()
        seen: dict[str, Paper] = {}
        total_candidates = 0

        # Pre-embed all queries in a single API call (critical for Gemini — avoids N round trips)
        if isinstance(self._embedder, GeminiEmbedder):
            texts = [f"task: search result | query: {q}" for q in queries]
            query_vectors = self._embedder.embed_texts(texts)  # single batch call
        else:
            query_texts = [expand_query_for_embedding(q, context_title, context_abstract) for q in queries]
            query_vectors = self._embedder.embed_texts(query_texts)

        # Build shared context vector if needed
        context_vector = None
        if context_paper_id:
            context_vector = self._store.get_paper_vector(context_paper_id)
        if context_title and context_vector is None:
            context_vector = self._embedder.embed_paper(context_title, context_abstract or "")

        # Resolve conference/categories once
        if conference and not categories:
            categories = get_categories_for_conference(conference)

        where_clause = self._store.build_where_clause(
            categories=categories,
            date_from=date_from,
            date_to=date_to,
            year=year,
        )

        fetch_limit = max_results * RERANK_OVERFETCH

        def _search_one(q_and_vec):
            q, qvec = q_and_vec
            if context_vector is not None:
                qvec = _blend_vectors(qvec, context_vector, alpha=0.4)
            results = self._store.hybrid_search(
                query=q,
                query_vector=qvec,
                limit=fetch_limit,
                where=where_clause,
            )
            if context_paper_id:
                results = [(p, s) for p, s in results if p.arxiv_id != context_paper_id]
            return results

        # Run all vector searches in parallel
        with ThreadPoolExecutor(max_workers=len(queries)) as ex:
            futures = {ex.submit(_search_one, (q, qv)): q for q, qv in zip(queries, query_vectors)}
            for future in as_completed(futures):
                candidates = future.result()
                total_candidates += len(candidates)
                for paper, score in candidates[:max_results]:
                    existing = seen.get(paper.arxiv_id)
                    if existing is None:
                        paper.similarity_score = score
                        seen[paper.arxiv_id] = paper
                    elif score > (existing.similarity_score or 0):
                        paper.similarity_score = score
                        seen[paper.arxiv_id] = paper

        papers = sorted(seen.values(), key=lambda p: p.similarity_score or 0, reverse=True)

        # Post-merge: enrich and sort/filter
        if sort_by in ("citations", "importance") or min_citations is not None:
            papers = _enrich_and_filter(
                papers, sort_by=sort_by, min_citations=min_citations
            )
        elif sort_by == "date":
            papers.sort(key=lambda p: p.published, reverse=True)

        return SearchResult(
            papers=papers,
            query=" | ".join(queries),
            total_candidates=total_candidates,
            search_time_ms=_elapsed_ms(t_start),
            details=details,
        )

    def _rerank_candidates(
        self,
        candidates: list[tuple[Paper, float]],
        seed_id: str | None = None,
        query_categories: list[str] | None = None,
        max_results: int = 20,
    ) -> list[Paper]:
        """Apply graph-based re-ranking to candidates."""
        candidate_ids = [p.arxiv_id for p, _ in candidates]
        candidate_similarities = [s for _, s in candidates]
        candidate_categories = [p.categories for p, _ in candidates]

        # Use cached vectors from search results (avoids 100s of individual DB lookups)
        embed_dim = self._embedder.embedding_dim
        candidate_vectors = []
        for p, _ in candidates:
            vec = p._cached_vector
            if vec is not None:
                candidate_vectors.append(vec)
            else:
                candidate_vectors.append(np.zeros(embed_dim, dtype=np.float32))
        candidate_vectors = np.array(candidate_vectors)

        seed_ids = [seed_id] if seed_id and seed_id in set(candidate_ids) else None

        reranked = compute_reranked_scores(
            candidate_ids=candidate_ids,
            candidate_vectors=candidate_vectors,
            candidate_similarities=candidate_similarities,
            candidate_categories=candidate_categories,
            seed_ids=seed_ids,
            query_categories=query_categories,
        )

        # Map back to Paper objects with updated scores
        paper_map = {p.arxiv_id: p for p, _ in candidates}
        result = []
        for cid, score in reranked[:max_results]:
            paper = paper_map[cid]
            paper.similarity_score = score
            result.append(paper)

        return result


def _blend_vectors(
    query_vec: np.ndarray,
    context_vec: np.ndarray,
    alpha: float = 0.4,
) -> np.ndarray:
    """Blend query and context paper vectors.

    alpha controls query weight: final = alpha * query + (1-alpha) * context.
    """
    blended = alpha * query_vec + (1.0 - alpha) * context_vec
    # L2 normalize
    norm = np.linalg.norm(blended)
    if norm > 0:
        blended = blended / norm
    return blended


def _elapsed_ms(start: float) -> float:
    return (time.time() - start) * 1000


def _enrich_and_filter(
    papers: list[Paper],
    sort_by: str | None = None,
    min_citations: int | None = None,
) -> list[Paper]:
    """Enrich via S2 API, then sort/filter by citations or importance."""
    from arxiv_search_kit.enrichment import enrich_papers

    if sort_by == "importance":
        enrich_papers(papers, fields=["citationCount", "influentialCitationCount", "venue"])
    else:
        enrich_papers(papers, fields=["citationCount"])

    if min_citations is not None:
        papers = [p for p in papers if (p.citation_count or 0) >= min_citations]

    if sort_by == "citations":
        papers.sort(key=lambda p: p.citation_count or 0, reverse=True)
    elif sort_by == "importance":
        from arxiv_search_kit.search.importance import rerank_by_importance
        papers = rerank_by_importance(papers)

    return papers


_TITLE_CLEAN_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    title = _TITLE_CLEAN_RE.sub("", title.lower().strip())
    return _WHITESPACE_RE.sub(" ", title)


def _title_similarity_strict(query: str, candidate: str) -> float:
    """Strict title similarity — requires high bidirectional overlap.

    Uses Jaccard similarity (intersection / union) so that
    partial matches like "Language Models" matching
    "Language Models are Few-shot Multilingual Learners" score low.
    """
    tokens_q = set(query.split())
    tokens_c = set(candidate.split())
    if not tokens_q or not tokens_c:
        return 0.0
    intersection = tokens_q & tokens_c
    union = tokens_q | tokens_c
    return len(intersection) / len(union)