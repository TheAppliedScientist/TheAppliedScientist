"""arxiv_search_kit — ArXiv paper search SDK.

Usage::

    from arxiv_search_kit import ArxivClient
    client = ArxivClient()
    papers = client.search("attention mechanism transformers")
"""

from __future__ import annotations

from arxiv_search_kit.models import Author, Paper, SearchResult
from arxiv_search_kit.categories import (
    ALL_CATEGORIES,
    CONFERENCE_CATEGORIES,
    CS_CATEGORIES,
    STAT_CATEGORIES,
    get_categories_for_conference,
)
from arxiv_search_kit.exceptions import (
    EmbeddingError,
    EnrichmentError,
    IndexBuildError,
    IndexNotFoundError,
    OpenArxivError,
    PaperNotFoundError,
)

__version__ = "0.2.4"


def __getattr__(name: str):
    if name == "ArxivClient":
        from arxiv_search_kit.client import ArxivClient
        return ArxivClient
    raise AttributeError(f"module 'arxiv_search_kit' has no attribute {name}")


__all__ = [
    "ArxivClient",
    "Author",
    "Paper",
    "SearchResult",
    "ALL_CATEGORIES",
    "CONFERENCE_CATEGORIES",
    "CS_CATEGORIES",
    "STAT_CATEGORIES",
    "get_categories_for_conference",
    "EmbeddingError",
    "EnrichmentError",
    "IndexBuildError",
    "IndexNotFoundError",
    "OpenArxivError",
    "PaperNotFoundError",
]
