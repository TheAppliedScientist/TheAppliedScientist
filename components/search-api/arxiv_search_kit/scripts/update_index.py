"""CLI script for incremental index updates.

Fetches recent papers from ArXiv API and adds them to the existing index.
This is for keeping the index up-to-date with papers published after the
bulk metadata snapshot.

Usage:
    python -m arxiv_search_kit.scripts.update_index \
        --index-dir /path/to/arxiv_index \
        --days 30 \
        --device cpu
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from arxiv_search_kit.categories import ALL_CATEGORIES
from arxiv_search_kit.index.builder import add_papers_to_index
from arxiv_search_kit.index.embedder import Specter2Embedder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
MAX_RESULTS_PER_REQUEST = 2000
RATE_LIMIT_SECONDS = 3


def _fetch_arxiv_page(
    category: str,
    date_from: str,
    date_to: str,
    start: int = 0,
    max_results: int = MAX_RESULTS_PER_REQUEST,
) -> list[dict]:
    """Fetch a page of results from ArXiv API for a category + date range."""
    date_from_fmt = date_from.replace("-", "") + "0000"
    date_to_fmt = date_to.replace("-", "") + "2359"

    query = f"cat:{category}+AND+submittedDate:[{date_from_fmt}+TO+{date_to_fmt}]"
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })

    url = f"{ARXIV_API_URL}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            xml_data = response.read()
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return []

    return _parse_atom_response(xml_data)


def _parse_atom_response(xml_data: bytes) -> list[dict]:
    """Parse ArXiv Atom XML response into paper dicts."""
    root = ET.fromstring(xml_data)
    papers = []

    for entry in root.findall(f"{ATOM_NS}entry"):
        arxiv_id_url = entry.findtext(f"{ATOM_NS}id", "")
        arxiv_id = arxiv_id_url.split("/abs/")[-1] if "/abs/" in arxiv_id_url else ""
        # Strip version
        if arxiv_id and "v" in arxiv_id:
            parts = arxiv_id.rsplit("v", 1)
            if parts[-1].isdigit():
                arxiv_id = parts[0]

        if not arxiv_id:
            continue

        title = (entry.findtext(f"{ATOM_NS}title", "") or "").replace("\n", " ").strip()
        abstract = (entry.findtext(f"{ATOM_NS}summary", "") or "").replace("\n", " ").strip()

        if not title or not abstract:
            continue

        # Authors
        authors = []
        for author_elem in entry.findall(f"{ATOM_NS}author"):
            name = author_elem.findtext(f"{ATOM_NS}name", "")
            affiliation = author_elem.findtext(f"{ARXIV_NS}affiliation", None)
            if name:
                authors.append({"name": name.strip(), "affiliation": affiliation})

        # Categories
        categories = []
        primary_category = ""
        prim_elem = entry.find(f"{ARXIV_NS}primary_category")
        if prim_elem is not None:
            primary_category = prim_elem.get("term", "")

        for cat_elem in entry.findall(f"{ATOM_NS}category"):
            term = cat_elem.get("term", "")
            if term:
                categories.append(term)

        # Dates
        published = entry.findtext(f"{ATOM_NS}published", "")
        updated = entry.findtext(f"{ATOM_NS}updated", "")

        # DOI, journal_ref, comment
        doi_elem = entry.find(f"{ARXIV_NS}doi")
        doi = doi_elem.text.strip() if doi_elem is not None and doi_elem.text else ""

        jr_elem = entry.find(f"{ARXIV_NS}journal_ref")
        journal_ref = jr_elem.text.strip() if jr_elem is not None and jr_elem.text else ""

        comment_elem = entry.find(f"{ARXIV_NS}comment")
        comment = comment_elem.text.strip() if comment_elem is not None and comment_elem.text else ""

        papers.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": json.dumps(authors),
            "categories": json.dumps(categories),
            "primary_category": primary_category,
            "published": published,
            "updated": updated,
            "doi": doi,
            "journal_ref": journal_ref,
            "comment": comment,
        })

    return papers


def fetch_recent_papers(
    categories: list[str],
    days: int = 30,
) -> list[dict]:
    """Fetch recent papers from ArXiv API across categories.

    Args:
        categories: ArXiv categories to fetch.
        days: How many days back to fetch.

    Returns:
        List of paper dicts (deduplicated by arxiv_id).
    """
    date_to = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    all_papers: dict[str, dict] = {}

    for cat in categories:
        logger.info(f"Fetching {cat} papers from {date_from} to {date_to}")
        papers = _fetch_arxiv_page(cat, date_from, date_to)
        for p in papers:
            if p["arxiv_id"] not in all_papers:
                all_papers[p["arxiv_id"]] = p

        # Rate limit
        time.sleep(RATE_LIMIT_SECONDS)

    logger.info(f"Fetched {len(all_papers)} unique papers from {len(categories)} categories")
    return list(all_papers.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Update ArXiv index with recent papers")
    parser.add_argument("--index-dir", type=str, required=True, help="Path to existing LanceDB index")
    parser.add_argument("--days", type=int, default=30, help="Days back to fetch (default: 30)")
    parser.add_argument("--device", type=str, default="cpu", help="Torch device (default: cpu)")
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding batch size")
    parser.add_argument("--categories", type=str, nargs="*", default=None, help="Categories to fetch")

    args = parser.parse_args()
    categories = args.categories or ALL_CATEGORIES

    logger.info(f"Updating index at {args.index_dir}")
    logger.info(f"Fetching papers from last {args.days} days")

    # Fetch recent papers
    papers = fetch_recent_papers(categories, days=args.days)

    if not papers:
        logger.info("No new papers found")
        return

    # Embed papers
    embedder = Specter2Embedder(device=args.device, batch_size=args.batch_size)
    from arxiv_search_kit.index.embedder import format_paper_text

    texts = [format_paper_text(p["title"], p["abstract"]) for p in papers]
    logger.info(f"Embedding {len(texts)} papers...")
    embeddings = embedder.embed_texts(texts)

    # Add to index
    added = add_papers_to_index(args.index_dir, papers, embeddings)
    logger.info(f"Added {added} papers to index")


if __name__ == "__main__":
    main()
