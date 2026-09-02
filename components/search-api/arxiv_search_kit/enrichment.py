"""Semantic Scholar enrichment — citation counts, references, TLDR, venue."""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from arxiv_search_kit.models import Paper

logger = logging.getLogger(__name__)

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_BATCH_LIMIT = 500
S2_RETRY_WAIT = 1.0

DEFAULT_FIELDS = [
    "citationCount",
    "influentialCitationCount",
    "references.externalIds",
    "tldr",
    "venue",
    "publicationTypes",
]


def _get_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    return headers


# ---------------------------------------------------------------------------
# HTTP with single retry (shared by all S2 calls)
# ---------------------------------------------------------------------------

def _request_with_retry(method, url: str, **kwargs) -> httpx.Response | None:
    """Fire an httpx request, retry once on any failure (wait 1s on 429)."""
    for attempt in range(2):
        try:
            resp = method(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            if attempt == 0:
                if e.response.status_code == 429:
                    time.sleep(S2_RETRY_WAIT)
                continue
            logger.warning("S2 %s failed: %s", url.split("/")[-1], e)
        except Exception as e:
            if attempt == 0:
                continue
            logger.warning("S2 request failed: %s", e)
    return None


async def _async_request_with_retry(coro_fn, url: str, **kwargs) -> httpx.Response | None:
    """Async variant of _request_with_retry."""
    for attempt in range(2):
        try:
            resp = await coro_fn(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            if attempt == 0:
                if e.response.status_code == 429:
                    await asyncio.sleep(S2_RETRY_WAIT)
                continue
            logger.warning("S2 %s failed: %s", url.split("/")[-1], e)
        except Exception as e:
            if attempt == 0:
                continue
            logger.warning("S2 request failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Paper enrichment
# ---------------------------------------------------------------------------

def enrich_papers(
    papers: list[Paper],
    fields: list[str] | None = None,
    timeout: float = 30.0,
) -> list[Paper]:
    """Add S2 metadata to papers in-place.

    Args:
        papers: Papers to enrich.
        fields: S2 fields to request. Defaults to DEFAULT_FIELDS.
        timeout: HTTP timeout in seconds.

    Returns:
        Same list with enrichment fields populated.
    """
    if not papers:
        return papers

    fields_str = ",".join(fields or DEFAULT_FIELDS)
    headers = _get_headers()
    url = f"{S2_API_BASE}/paper/batch"

    with httpx.Client(timeout=timeout) as http:
        for i in range(0, len(papers), S2_BATCH_LIMIT):
            batch = papers[i : i + S2_BATCH_LIMIT]
            ids = [f"ArXiv:{p.arxiv_id}" for p in batch]

            resp = _request_with_retry(
                http.post, url,
                headers=headers, params={"fields": fields_str}, json={"ids": ids},
            )
            if resp is None:
                continue
            for paper, data in zip(batch, resp.json()):
                if data is not None:
                    _apply_enrichment(paper, data)

    return papers


async def async_enrich_papers(
    papers: list[Paper],
    fields: list[str] | None = None,
    timeout: float = 30.0,
) -> list[Paper]:
    """Async variant of enrich_papers."""
    if not papers:
        return papers

    fields_str = ",".join(fields or DEFAULT_FIELDS)
    headers = _get_headers()
    url = f"{S2_API_BASE}/paper/batch"

    async with httpx.AsyncClient(timeout=timeout) as client:
        for i in range(0, len(papers), S2_BATCH_LIMIT):
            batch = papers[i : i + S2_BATCH_LIMIT]
            ids = [f"ArXiv:{p.arxiv_id}" for p in batch]

            resp = await _async_request_with_retry(
                client.post, url,
                headers=headers, params={"fields": fields_str}, json={"ids": ids},
            )
            if resp is None:
                continue
            for paper, data in zip(batch, resp.json()):
                if data is not None:
                    _apply_enrichment(paper, data)

    return papers


# ---------------------------------------------------------------------------
# Citation graph
# ---------------------------------------------------------------------------

def get_citations(
    arxiv_id: str,
    limit: int = 100,
    timeout: float = 30.0,
) -> list[dict]:
    """Get papers that cite the given paper.

    Returns:
        List of dicts: {arxiv_id, title, year, citation_count}.
    """
    return _get_citation_graph(arxiv_id, "citations", limit, timeout)


def get_references(
    arxiv_id: str,
    limit: int = 100,
    timeout: float = 30.0,
) -> list[dict]:
    """Get papers referenced by the given paper.

    Returns:
        List of dicts: {arxiv_id, title, year, citation_count}.
    """
    return _get_citation_graph(arxiv_id, "references", limit, timeout)


async def async_get_citations(arxiv_id: str, limit: int = 100, timeout: float = 30.0) -> list[dict]:
    """Async variant of get_citations."""
    return await _async_get_citation_graph(arxiv_id, "citations", limit, timeout)


async def async_get_references(arxiv_id: str, limit: int = 100, timeout: float = 30.0) -> list[dict]:
    """Async variant of get_references."""
    return await _async_get_citation_graph(arxiv_id, "references", limit, timeout)


def _get_citation_graph(arxiv_id: str, direction: str, limit: int, timeout: float) -> list[dict]:
    fields = "title,year,citationCount,externalIds"
    url = f"{S2_API_BASE}/paper/ArXiv:{arxiv_id}/{direction}?fields={fields}&limit={limit}"

    resp = _request_with_retry(httpx.get, url, headers=_get_headers(), timeout=timeout)
    if resp is None:
        return []
    return _parse_citation_data(resp.json(), direction)


async def _async_get_citation_graph(arxiv_id: str, direction: str, limit: int, timeout: float) -> list[dict]:
    fields = "title,year,citationCount,externalIds"
    url = f"{S2_API_BASE}/paper/ArXiv:{arxiv_id}/{direction}?fields={fields}&limit={limit}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await _async_request_with_retry(client.get, url, headers=_get_headers())
    if resp is None:
        return []
    return _parse_citation_data(resp.json(), direction)


def _parse_citation_data(data: dict, direction: str) -> list[dict]:
    key = "citingPaper" if direction == "citations" else "citedPaper"
    results = []
    for entry in data.get("data", []):
        paper_data = entry.get(key, {})
        if not paper_data:
            continue
        ext_ids = paper_data.get("externalIds", {}) or {}
        results.append({
            "arxiv_id": ext_ids.get("ArXiv"),
            "title": paper_data.get("title", ""),
            "year": paper_data.get("year"),
            "citation_count": paper_data.get("citationCount", 0),
        })
    return results


# ---------------------------------------------------------------------------
# Apply S2 fields to Paper
# ---------------------------------------------------------------------------

def _apply_enrichment(paper: Paper, s2: dict) -> None:
    """Map S2 API response fields onto a Paper object."""
    if "citationCount" in s2:
        paper.citation_count = s2["citationCount"]
    if "influentialCitationCount" in s2:
        paper.influential_citation_count = s2["influentialCitationCount"]
    if s2.get("tldr"):
        paper.tldr = s2["tldr"].get("text")
    if s2.get("references"):
        ids = [r["externalIds"]["ArXiv"] for r in s2["references"]
               if r.get("externalIds") and "ArXiv" in r["externalIds"]]
        paper.references = ids or None
    if s2.get("venue"):
        paper.venue = s2["venue"]
    if s2.get("publicationTypes"):
        paper.publication_types = s2["publicationTypes"]
