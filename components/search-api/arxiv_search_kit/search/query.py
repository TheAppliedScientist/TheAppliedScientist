"""Query preprocessing — keyword extraction and expansion for SPECTER2 embedding."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Common English stopwords + academic filler words to skip during keyword extraction
STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "that", "which", "who", "whom", "this", "these", "those", "it", "its",
    "not", "no", "nor", "if", "then", "than", "so", "such", "very",
    "too", "also", "just", "about", "above", "after", "again", "all",
    "any", "both", "each", "few", "more", "most", "other", "some",
    "into", "over", "under", "between", "through", "during", "before",
    "we", "our", "us", "they", "their", "them", "he", "she", "his", "her",
    # Academic filler
    "paper", "propose", "proposed", "approach", "method", "methods",
    "result", "results", "show", "shows", "using", "based", "new",
    "novel", "recent", "state", "art",
}


def preprocess_query(query: str) -> str:
    """Clean and normalize a search query.

    - Lowercases
    - Removes special characters except hyphens
    - Collapses whitespace
    """
    query = query.lower().strip()
    query = re.sub(r"[^\w\s\-]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query


def extract_keywords(text: str, max_keywords: int = 15) -> list[str]:
    """Extract keywords from text using YAKE if available, else simple extraction.

    Args:
        text: Input text (query, title, or abstract).
        max_keywords: Maximum number of keywords to extract.

    Returns:
        List of keyword strings, ordered by importance.
    """
    try:
        return _extract_with_yake(text, max_keywords)
    except ImportError:
        return _extract_simple(text, max_keywords)


def _extract_with_yake(text: str, max_keywords: int) -> list[str]:
    """Extract keywords using YAKE (unsupervised keyword extraction)."""
    import yake

    kw_extractor = yake.KeywordExtractor(
        lan="en",
        n=3,  # max n-gram size
        dedupLim=0.7,  # deduplication threshold
        top=max_keywords,
        features=None,
    )
    keywords = kw_extractor.extract_keywords(text)
    # YAKE returns (keyword, score) where lower score = more important
    return [kw for kw, _score in keywords]


def _extract_simple(text: str, max_keywords: int) -> list[str]:
    """Simple keyword extraction fallback (no external deps).

    Extracts unique meaningful words ordered by position (earlier = more important).
    Also captures 2-grams for compound terms.
    """
    words = preprocess_query(text).split()
    meaningful = [w for w in words if w not in STOPWORDS and len(w) > 2]

    # Capture bigrams (compound terms are often important in CS)
    bigrams = []
    for i in range(len(meaningful) - 1):
        bigrams.append(f"{meaningful[i]} {meaningful[i+1]}")

    # Deduplicate while preserving order
    seen = set()
    keywords = []
    # Bigrams first (more specific)
    for bg in bigrams:
        if bg not in seen and len(keywords) < max_keywords:
            seen.add(bg)
            keywords.append(bg)
    # Then unigrams
    for w in meaningful:
        if w not in seen and len(keywords) < max_keywords:
            seen.add(w)
            keywords.append(w)

    return keywords[:max_keywords]


def expand_query_for_embedding(
    query: str,
    context_title: str | None = None,
    context_abstract: str | None = None,
) -> str:
    """Build the text that will be embedded by SPECTER2.

    If a context paper is provided, we blend the query with context
    to create a more targeted embedding.

    Args:
        query: User's keyword query.
        context_title: Title of the paper being reviewed (optional).
        context_abstract: Abstract of the paper being reviewed (optional).

    Returns:
        Text string ready for SPECTER2 embedding.
    """
    parts = [query]

    if context_title:
        parts.append(context_title)
    if context_abstract:
        # Use first 200 chars of abstract to keep within token limits
        parts.append(context_abstract[:200])

    return " [SEP] ".join(parts)