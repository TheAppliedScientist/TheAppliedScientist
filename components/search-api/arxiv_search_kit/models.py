"""Data models for arxiv_search_kit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

import numpy as np


@dataclass
class Author:
    """Paper author with optional affiliation."""

    name: str
    affiliation: str | None = None

    def __str__(self) -> str:
        if self.affiliation:
            return f"{self.name} ({self.affiliation})"
        return self.name


@dataclass
class Paper:
    """ArXiv paper metadata.

    Core fields are populated from the LanceDB index (originally from ArXiv bulk metadata).
    Enrichment fields (citation_count, references, tldr) are populated optionally via
    Semantic Scholar API.
    """

    arxiv_id: str
    title: str
    authors: list[Author]
    abstract: str
    categories: list[str]
    primary_category: str
    published: datetime
    updated: datetime
    doi: str | None = None
    journal_ref: str | None = None
    comment: str | None = None

    # Computed URLs
    pdf_url: str = ""
    abs_url: str = ""

    # Search-time fields
    similarity_score: float | None = None

    # Enrichment fields (populated by client.enrich())
    citation_count: int | None = None
    influential_citation_count: int | None = None
    references: list[str] | None = None
    tldr: str | None = None
    venue: str | None = None
    publication_types: list[str] | None = None

    # Internal: cached embedding from search results (not serialized)
    _cached_vector: np.ndarray | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.pdf_url:
            self.pdf_url = f"https://arxiv.org/pdf/{self.arxiv_id}"
        if not self.abs_url:
            self.abs_url = f"https://arxiv.org/abs/{self.arxiv_id}"

    @property
    def year(self) -> int:
        return self.published.year

    @property
    def author_names(self) -> list[str]:
        return [a.name for a in self.authors]

    @property
    def first_author(self) -> str:
        return self.authors[0].name if self.authors else "Unknown"

    def to_bibtex(self, style: str = "default") -> str:
        """Generate BibTeX entry. See bibtex.py for full implementation."""
        from arxiv_search_kit.bibtex import generate_bibtex

        return generate_bibtex(self, style=style)

    def to_dict(self) -> dict:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": [{"name": a.name, "affiliation": a.affiliation} for a in self.authors],
            "abstract": self.abstract,
            "categories": self.categories,
            "primary_category": self.primary_category,
            "published": self.published.isoformat(),
            "updated": self.updated.isoformat(),
            "doi": self.doi,
            "journal_ref": self.journal_ref,
            "comment": self.comment,
            "pdf_url": self.pdf_url,
            "abs_url": self.abs_url,
            "similarity_score": self.similarity_score,
            "citation_count": self.citation_count,
            "influential_citation_count": self.influential_citation_count,
            "references": self.references,
            "tldr": self.tldr,
            "venue": self.venue,
            "publication_types": self.publication_types,
        }

    def __repr__(self) -> str:
        title = self.title[:60] + "..." if len(self.title) > 60 else self.title
        return f"Paper('{self.arxiv_id}', '{title}')"


# Fields returned in default vs extra mode
_DEFAULT_PAPER_FIELDS = {"arxiv_id", "title", "abstract", "year", "citation_count"}
_EXTRA_PAPER_FIELDS = {
    "authors", "categories", "primary_category", "published", "updated",
    "doi", "journal_ref", "comment", "pdf_url", "abs_url",
    "similarity_score", "influential_citation_count", "references",
    "tldr", "venue", "publication_types",
}


def _strip_paper(paper: "Paper") -> dict:
    """Return only the default fields from a Paper as a dict."""
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "citation_count": paper.citation_count,
    }


@dataclass
class SearchResult:
    """Container for search results with metadata about the search."""

    papers: list[Paper]
    query: str
    total_candidates: int
    search_time_ms: float
    details: str = "default"  # "default" or "extra"

    def __len__(self) -> int:
        return len(self.papers)

    def __iter__(self) -> Iterator[Paper]:
        return iter(self.papers)

    def __getitem__(self, idx: int) -> Paper:
        return self.papers[idx]

    def to_dicts(self) -> list[dict]:
        """Serialize papers according to the details level.

        ``details="default"`` → arxiv_id, title, abstract, citation_count.
        ``details="extra"``   → all Paper fields.
        """
        if self.details == "extra":
            return [p.to_dict() for p in self.papers]
        return [_strip_paper(p) for p in self.papers]

    def __repr__(self) -> str:
        return f"SearchResult(query='{self.query[:40]}', papers={len(self.papers)}, time={self.search_time_ms:.0f}ms, details='{self.details}')"
