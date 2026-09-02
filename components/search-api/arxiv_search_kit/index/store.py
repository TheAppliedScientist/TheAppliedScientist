"""LanceDB index store — runtime query interface for the pre-built paper index."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import lancedb
import numpy as np

from arxiv_search_kit.exceptions import IndexNotFoundError, PaperNotFoundError
from arxiv_search_kit.models import Author, Paper

logger = logging.getLogger(__name__)

TABLE_NAME = "arxiv_papers"

_VERSION_SUFFIX_RE = re.compile(r"v\d+$")


def _strip_arxiv_version(arxiv_id: str) -> str:
    return _VERSION_SUFFIX_RE.sub("", arxiv_id)


def _sanitize_str(value: str) -> str:
    """Escape single quotes for use in LanceDB WHERE clauses."""
    return value.replace("'", "''")


class IndexStore:
    """Query interface for the LanceDB paper index."""

    def __init__(self, index_dir: str | Path):
        index_dir = Path(index_dir)
        if not index_dir.exists():
            raise IndexNotFoundError(f"Index directory not found: {index_dir}")

        try:
            self._db = lancedb.connect(str(index_dir))
            self._table = self._db.open_table(TABLE_NAME)
        except Exception as e:
            raise IndexNotFoundError(f"Failed to open index at {index_dir}: {e}") from e

        self._index_dir = index_dir
        logger.info("Opened index at %s (%s papers)", index_dir, f"{len(self._table):,}")

    def __repr__(self) -> str:
        return f"IndexStore(index_dir='{self._index_dir}', papers={len(self._table):,})"

    @property
    def num_papers(self) -> int:
        return len(self._table)

    def vector_search(
        self,
        query_vector: np.ndarray,
        limit: int = 50,
        where: str | None = None,
        nprobes: int = 20,
    ) -> list[tuple[Paper, float]]:
        """Search by vector similarity. Returns (Paper, similarity) tuples."""
        search = self._table.search(query_vector.tolist()).limit(limit).nprobes(nprobes)
        if where:
            search = search.where(where)

        try:
            results = search.to_pandas()
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            return []

        papers = []
        for _, row in results.iterrows():
            paper = _row_to_paper(row)
            distance = float(row.get("_distance", 0.0))
            similarity = 1.0 - distance
            paper.similarity_score = similarity
            if "vector" in row:
                paper._cached_vector = np.array(row["vector"], dtype=np.float32)
            papers.append((paper, similarity))
        return papers

    def get_paper(self, arxiv_id: str) -> Paper:
        """Look up a paper by arXiv ID. Raises PaperNotFoundError if not found."""
        clean_id = _sanitize_str(_strip_arxiv_version(arxiv_id))

        try:
            results = (
                self._table.search()
                .where(f"arxiv_id = '{clean_id}'")
                .limit(1)
                .to_pandas()
            )
        except Exception as e:
            raise PaperNotFoundError(f"Paper {arxiv_id}: {e}") from e

        if results.empty:
            raise PaperNotFoundError(f"Paper {arxiv_id} not found in index")
        return _row_to_paper(results.iloc[0])

    def get_papers(self, arxiv_ids: list[str]) -> list[Paper]:
        """Get multiple papers by ID. Missing papers are silently skipped."""
        papers = []
        for arxiv_id in arxiv_ids:
            try:
                papers.append(self.get_paper(arxiv_id))
            except PaperNotFoundError:
                pass
        return papers

    def get_paper_vector(self, arxiv_id: str) -> np.ndarray | None:
        """Get the stored embedding vector for a paper, or None."""
        clean_id = _sanitize_str(_strip_arxiv_version(arxiv_id))
        try:
            results = (
                self._table.search()
                .where(f"arxiv_id = '{clean_id}'")
                .limit(1)
                .to_pandas()
            )
            if results.empty:
                return None
            return np.array(results.iloc[0]["vector"], dtype=np.float32)
        except Exception:
            return None

    def full_text_search(
        self,
        query: str,
        limit: int = 50,
        where: str | None = None,
    ) -> list[tuple[Paper, float]]:
        """BM25 full-text search. Returns empty list if FTS index is unavailable."""
        try:
            search = self._table.search(query, query_type="fts").limit(limit)
            if where:
                search = search.where(where)
            results = search.to_pandas()
        except Exception:
            return []

        papers = []
        for _, row in results.iterrows():
            paper = _row_to_paper(row)
            score = float(row.get("_score", 0.0))
            paper.similarity_score = score
            if "vector" in row:
                paper._cached_vector = np.array(row["vector"], dtype=np.float32)
            papers.append((paper, score))
        return papers

    def hybrid_search(
        self,
        query: str,
        query_vector: np.ndarray,
        limit: int = 50,
        where: str | None = None,
        nprobes: int = 20,
    ) -> list[tuple[Paper, float]]:
        """Dense + sparse hybrid search. Falls back to dense-only if FTS unavailable."""
        # Try native hybrid first
        try:
            search = (
                self._table.search(query_vector.tolist(), query_type="hybrid")
                .limit(limit)
                .nprobes(nprobes)
            )
            if where:
                search = search.where(where)
            results = search.to_pandas()

            papers = []
            for _, row in results.iterrows():
                paper = _row_to_paper(row)
                score = float(row.get("_relevance_score", row.get("_distance", 0.0)))
                if "_distance" in row and "_relevance_score" not in row:
                    score = 1.0 - score
                paper.similarity_score = score
                if "vector" in row:
                    paper._cached_vector = np.array(row["vector"], dtype=np.float32)
                papers.append((paper, score))
            return papers
        except Exception:
            pass

        # Fallback: manual RRF fusion
        from arxiv_search_kit.search.bm25 import reciprocal_rank_fusion

        dense = self.vector_search(query_vector, limit=limit, where=where, nprobes=nprobes)
        sparse = self.full_text_search(query, limit=limit, where=where)

        if not sparse:
            return dense
        return reciprocal_rank_fusion(dense, sparse)

    def build_where_clause(
        self,
        categories: list[str] | None = None,
        primary_category: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        year: int | None = None,
    ) -> str | None:
        """Build a sanitized WHERE clause for LanceDB filtering."""
        conditions = []

        if primary_category:
            conditions.append(f"primary_category = '{_sanitize_str(primary_category)}'")
        elif categories:
            cats_str = ", ".join(f"'{_sanitize_str(c)}'" for c in categories)
            conditions.append(f"primary_category IN ({cats_str})")

        if year:
            conditions.append(f"published LIKE '{int(year)}-%'")
        else:
            if date_from:
                conditions.append(f"published >= '{_sanitize_str(date_from)}'")
            if date_to:
                conditions.append(f"published <= '{_sanitize_str(date_to)}'")

        return " AND ".join(conditions) if conditions else None


def _row_to_paper(row) -> Paper:
    """Convert a LanceDB result row to a Paper."""
    authors_raw = row.get("authors", "[]")
    if isinstance(authors_raw, str):
        try:
            authors_list = json.loads(authors_raw)
        except json.JSONDecodeError:
            authors_list = []
    else:
        authors_list = authors_raw if authors_raw is not None else []

    authors = [
        Author(name=a.get("name", ""), affiliation=a.get("affiliation"))
        for a in authors_list
        if isinstance(a, dict)
    ]

    categories_raw = row.get("categories", "[]")
    if isinstance(categories_raw, str):
        try:
            categories = json.loads(categories_raw)
        except json.JSONDecodeError:
            categories = categories_raw.split() if categories_raw else []
    else:
        categories = categories_raw if categories_raw is not None else []

    return Paper(
        arxiv_id=str(row.get("arxiv_id", "")),
        title=str(row.get("title", "")),
        authors=authors,
        abstract=str(row.get("abstract", "")),
        categories=categories,
        primary_category=str(row.get("primary_category", "")),
        published=_parse_datetime(row.get("published", "")),
        updated=_parse_datetime(row.get("updated", "")),
        doi=row.get("doi") or None,
        journal_ref=row.get("journal_ref") or None,
        comment=row.get("comment") or None,
    )


def _parse_datetime(date_str: str | None) -> datetime:
    if not date_str:
        return datetime(1970, 1, 1)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt)
        except ValueError:
            continue
    logger.debug("Unparseable date: %s", date_str)
    return datetime(1970, 1, 1)