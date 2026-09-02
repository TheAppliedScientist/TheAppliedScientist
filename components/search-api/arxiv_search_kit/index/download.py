"""Download ArXiv bulk metadata for indexing.

Supports three sources (in order of recommendation):

1. **OAI-PMH harvesting** (recommended): Fetches latest metadata directly from
   ArXiv's OAI-PMH endpoint. Always up-to-date. Handles resumption tokens for
   reliable bulk download. Filters by ArXiv sets (cs, stat).

2. **ArXiv S3 bulk export**: Downloads from ArXiv's public S3 bucket.
   Updated monthly. Faster than OAI-PMH for full dataset.

3. **Kaggle snapshot** (fallback): Static JSON file, often months stale.
"""

from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Generator
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from arxiv_search_kit.categories import ALL_CATEGORIES

logger = logging.getLogger(__name__)

# ArXiv OAI-PMH endpoint
OAI_PMH_BASE = "https://export.arxiv.org/oai2"
OAI_METADATA_PREFIX = "arXiv"  # arXiv-native format with categories, abstract, etc.

# OAI-PMH XML namespaces
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
ARXIV_NS = "http://arxiv.org/OAI/arXiv/"

# Rate limiting for OAI-PMH (ArXiv asks for politeness)
OAI_RATE_LIMIT_SECONDS = 2
# Retry settings
MAX_RETRIES = 5
RETRY_BACKOFF = 10  # seconds


# ============================================================
# Source 1: OAI-PMH Harvesting (recommended, always up-to-date)
# ============================================================


def iter_metadata_from_oai_pmh(
    categories: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sets: list[str] | None = None,
    save_path: str | Path | None = None,
) -> Generator[dict, None, None]:
    """Harvest paper metadata from ArXiv OAI-PMH endpoint.

    This fetches the LATEST metadata directly from ArXiv. It handles
    resumption tokens automatically for reliable bulk download.

    ArXiv OAI-PMH returns records in the native `arXiv` metadata format which
    includes: id, title, abstract, authors, categories, doi, journal-ref, etc.

    Args:
        categories: Filter to these ArXiv categories. Defaults to ALL_CATEGORIES.
        date_from: Harvest records from this date (YYYY-MM-DD). If None, gets all.
        date_to: Harvest records until this date (YYYY-MM-DD).
        sets: OAI-PMH set names to harvest (e.g. ["cs", "stat"]).
              Defaults to ["cs", "stat"] which covers all CS and Statistics papers.
        save_path: If provided, save raw XML responses to this directory for caching.

    Yields:
        Dict with normalized paper metadata (same format as Kaggle loader).
    """
    target_cats = set(categories or ALL_CATEGORIES)

    if sets is None:
        # Default: harvest CS and Statistics sets
        sets = _get_oai_sets_for_categories(target_cats)

    if save_path:
        save_path = Path(save_path)
        save_path.mkdir(parents=True, exist_ok=True)

    total_harvested = 0
    total_kept = 0

    for oai_set in sets:
        logger.info(f"Harvesting OAI-PMH set: {oai_set}")
        set_count = 0

        for record in _harvest_oai_set(oai_set, date_from, date_to, save_path):
            total_harvested += 1
            set_count += 1

            if total_harvested % 10_000 == 0:
                logger.info(f"Harvested {total_harvested:,} records, kept {total_kept:,}")

            # Parse the arXiv metadata record
            paper = _parse_oai_arxiv_record(record)
            if paper is None:
                continue

            # Filter by target categories
            paper_cats = paper.get("_categories_raw", "")
            if not _has_target_category(paper_cats, target_cats):
                continue

            total_kept += 1
            # Remove internal field before yielding
            paper.pop("_categories_raw", None)
            yield paper

        logger.info(f"Set '{oai_set}': harvested {set_count:,} records")

    logger.info(f"OAI-PMH harvest complete. {total_harvested:,} total, {total_kept:,} kept")


def _get_oai_sets_for_categories(target_cats: set[str]) -> list[str]:
    """Determine which OAI-PMH sets to harvest based on target categories."""
    sets = set()
    for cat in target_cats:
        prefix = cat.split(".")[0]  # "cs.CV" -> "cs", "stat.ML" -> "stat"
        sets.add(prefix)
    return sorted(sets)


def _harvest_oai_set(
    oai_set: str,
    date_from: str | None = None,
    date_to: str | None = None,
    save_path: Path | None = None,
) -> Generator[ET.Element, None, None]:
    """Harvest all records from an OAI-PMH set, handling resumption tokens.

    Yields individual <record> XML elements.
    """
    # Build initial request URL
    params = {
        "verb": "ListRecords",
        "metadataPrefix": OAI_METADATA_PREFIX,
        "set": oai_set,
    }
    if date_from:
        params["from"] = date_from
    if date_to:
        params["until"] = date_to

    url = _build_oai_url(params)
    page = 0

    while url:
        page += 1
        xml_data = _fetch_oai_page(url)
        if xml_data is None:
            break

        # Save raw response if requested
        if save_path:
            (save_path / f"{oai_set}_page_{page:05d}.xml").write_bytes(xml_data)

        # Parse XML
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            logger.warning(f"Failed to parse OAI-PMH response page {page}: {e}")
            break

        # Check for errors
        error = root.find(f"{{{OAI_NS}}}error")
        if error is not None:
            error_code = error.get("code", "")
            if error_code == "noRecordsMatch":
                logger.info(f"No records match for set '{oai_set}' with given date range")
                break
            logger.warning(f"OAI-PMH error: {error_code} - {error.text}")
            break

        # Extract records
        list_records = root.find(f"{{{OAI_NS}}}ListRecords")
        if list_records is None:
            break

        for record in list_records.findall(f"{{{OAI_NS}}}record"):
            # Skip deleted records
            header = record.find(f"{{{OAI_NS}}}header")
            if header is not None and header.get("status") == "deleted":
                continue
            yield record

        # Check for resumption token (pagination)
        token_elem = list_records.find(f"{{{OAI_NS}}}resumptionToken")
        if token_elem is not None and token_elem.text:
            url = _build_oai_url({
                "verb": "ListRecords",
                "resumptionToken": token_elem.text,
            })
            time.sleep(OAI_RATE_LIMIT_SECONDS)
        else:
            url = None  # No more pages


def _build_oai_url(params: dict) -> str:
    """Build OAI-PMH request URL from parameters."""
    from urllib.parse import urlencode
    return f"{OAI_PMH_BASE}?{urlencode(params)}"


def _fetch_oai_page(url: str) -> bytes | None:
    """Fetch a single OAI-PMH page with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(url, headers={"User-Agent": "arxiv_search_kit/0.1"})
            with urlopen(req, timeout=120) as response:
                return response.read()
        except HTTPError as e:
            if e.code == 503:
                # ArXiv sends 503 with Retry-After header when overloaded
                retry_after = int(e.headers.get("Retry-After", RETRY_BACKOFF))
                logger.info(f"OAI-PMH 503, retrying in {retry_after}s (attempt {attempt+1}/{MAX_RETRIES})")
                time.sleep(retry_after)
            elif e.code == 429:
                wait = RETRY_BACKOFF * (attempt + 1)
                logger.info(f"OAI-PMH rate limited, waiting {wait}s")
                time.sleep(wait)
            else:
                logger.warning(f"OAI-PMH HTTP error {e.code}: {e.reason}")
                return None
        except Exception as e:
            logger.warning(f"OAI-PMH request failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF)
            else:
                return None
    return None


def _parse_oai_arxiv_record(record: ET.Element) -> dict | None:
    """Parse an OAI-PMH <record> element in arXiv metadata format.

    The arXiv metadata format has this structure:
    <record>
      <header>
        <identifier>oai:arXiv.org:2401.12345</identifier>
        <datestamp>2024-01-15</datestamp>
        <setSpec>cs</setSpec>
      </header>
      <metadata>
        <arXiv>
          <id>2401.12345</id>
          <created>2024-01-15</created>
          <updated>2024-01-20</updated>
          <authors>
            <author><keyname>Smith</keyname><forenames>John</forenames></author>
          </authors>
          <title>Paper Title</title>
          <categories>cs.LG cs.AI</categories>
          <abstract>Abstract text...</abstract>
          <doi>10.xxxx/yyyy</doi>
          <journal-ref>...</journal-ref>
          <comments>...</comments>
        </arXiv>
      </metadata>
    </record>
    """
    metadata = record.find(f"{{{OAI_NS}}}metadata")
    if metadata is None:
        return None

    arxiv_elem = metadata.find(f"{{{ARXIV_NS}}}arXiv")
    if arxiv_elem is None:
        return None

    # Extract fields
    arxiv_id = _text(arxiv_elem, f"{{{ARXIV_NS}}}id")
    if not arxiv_id:
        return None

    title = _text(arxiv_elem, f"{{{ARXIV_NS}}}title")
    abstract = _text(arxiv_elem, f"{{{ARXIV_NS}}}abstract")
    if not title or not abstract:
        return None

    # Clean whitespace
    title = " ".join(title.split())
    abstract = " ".join(abstract.split())

    # Parse authors
    authors = []
    authors_elem = arxiv_elem.find(f"{{{ARXIV_NS}}}authors")
    if authors_elem is not None:
        for author_elem in authors_elem.findall(f"{{{ARXIV_NS}}}author"):
            keyname = _text(author_elem, f"{{{ARXIV_NS}}}keyname") or ""
            forenames = _text(author_elem, f"{{{ARXIV_NS}}}forenames") or ""
            name = f"{forenames} {keyname}".strip()
            affiliation_elem = author_elem.find(f"{{{ARXIV_NS}}}affiliation")
            affiliation = affiliation_elem.text.strip() if affiliation_elem is not None and affiliation_elem.text else None
            if name:
                authors.append({"name": name, "affiliation": affiliation})

    # Categories
    categories_str = _text(arxiv_elem, f"{{{ARXIV_NS}}}categories") or ""
    all_cats = categories_str.split()
    primary_cat = all_cats[0] if all_cats else ""

    # Dates
    created = _text(arxiv_elem, f"{{{ARXIV_NS}}}created") or ""
    updated = _text(arxiv_elem, f"{{{ARXIV_NS}}}updated") or created

    published = _parse_date(created) if created else ""
    updated_parsed = _parse_date(updated) if updated else published

    # Optional fields
    doi = _text(arxiv_elem, f"{{{ARXIV_NS}}}doi") or ""
    journal_ref = _text(arxiv_elem, f"{{{ARXIV_NS}}}journal-ref") or ""
    comment = _text(arxiv_elem, f"{{{ARXIV_NS}}}comments") or ""

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "abstract": abstract,
        "authors": json.dumps(authors),
        "categories": json.dumps(all_cats),
        "primary_category": primary_cat,
        "published": published,
        "updated": updated_parsed,
        "doi": doi,
        "journal_ref": journal_ref,
        "comment": comment,
        "_categories_raw": categories_str,  # kept for filtering, removed before yield
    }


def _text(parent: ET.Element, tag: str) -> str | None:
    """Safely extract text from an XML element."""
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


# ============================================================
# Source 2: Kaggle dataset (fallback, may be stale)
# ============================================================


def _parse_authors_parsed(authors_parsed: list[list[str]]) -> list[dict[str, str | None]]:
    """Parse the authors_parsed field from Kaggle metadata.

    Each entry is [last_name, first_name, suffix].
    """
    authors = []
    for parts in authors_parsed:
        if len(parts) >= 2:
            last = parts[0].strip()
            first = parts[1].strip()
            name = f"{first} {last}".strip() if first else last
        elif len(parts) == 1:
            name = parts[0].strip()
        else:
            continue
        if name:
            authors.append({"name": name, "affiliation": None})
    return authors


def _has_target_category(categories_str: str, target_categories: set[str]) -> bool:
    """Check if any of the paper's categories are in our target set."""
    paper_cats = categories_str.split()
    return any(cat in target_categories for cat in paper_cats)


def _parse_date(date_str: str) -> str:
    """Parse date string to ISO format. Handles various formats."""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.isoformat()
    except ValueError:
        pass
    for fmt in ["%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d"]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return date_str


def iter_metadata_from_kaggle(
    metadata_path: str | Path,
    categories: list[str] | None = None,
) -> Generator[dict, None, None]:
    """Iterate over papers from the Kaggle ArXiv metadata snapshot.

    Args:
        metadata_path: Path to `arxiv-metadata-oai-snapshot.json`.
        categories: Filter to these categories. Defaults to ALL_CATEGORIES.

    Yields:
        Dict with normalized paper metadata.
    """
    metadata_path = Path(metadata_path)
    target_cats = set(categories or ALL_CATEGORIES)

    logger.info(f"Reading metadata from {metadata_path}")
    logger.info(f"Filtering to {len(target_cats)} target categories")

    count = 0
    kept = 0

    with open(metadata_path, "r", encoding="utf-8") as f:
        for line in f:
            count += 1
            if count % 500_000 == 0:
                logger.info(f"Processed {count:,} papers, kept {kept:,}")

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            categories_str = record.get("categories", "")
            if not _has_target_category(categories_str, target_cats):
                continue

            arxiv_id = record.get("id", "").strip()
            if not arxiv_id:
                continue

            title = record.get("title", "").replace("\n", " ").strip()
            abstract = record.get("abstract", "").replace("\n", " ").strip()
            if not title or not abstract:
                continue

            authors_parsed = record.get("authors_parsed", [])
            authors = _parse_authors_parsed(authors_parsed)

            all_cats = categories_str.split()
            primary_cat = all_cats[0] if all_cats else ""

            versions = record.get("versions", [])
            published = ""
            updated = ""
            if versions:
                published = _parse_date(versions[0].get("created", ""))
                updated = _parse_date(versions[-1].get("created", ""))
            if not published:
                published = _parse_date(record.get("update_date", ""))
                updated = published

            kept += 1
            yield {
                "arxiv_id": arxiv_id,
                "title": title,
                "abstract": abstract,
                "authors": json.dumps(authors),
                "categories": json.dumps(all_cats),
                "primary_category": primary_cat,
                "published": published,
                "updated": updated,
                "doi": record.get("doi") or "",
                "journal_ref": record.get("journal-ref") or "",
                "comment": record.get("comments") or "",
            }

    logger.info(f"Done. Processed {count:,} total, kept {kept:,} papers in target categories")


# ============================================================
# Utility
# ============================================================


def save_metadata_to_jsonl(
    output_path: str | Path,
    source: str = "oai-pmh",
    metadata_path: str | Path | None = None,
    categories: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Download metadata and save to a JSONL file for later indexing.

    This is useful for separating the download step (slow, network-bound)
    from the embedding step (GPU-bound).

    Args:
        output_path: Path to save JSONL file.
        source: "oai-pmh" for live ArXiv, "kaggle" for local file.
        metadata_path: Path to Kaggle JSON (required if source="kaggle").
        categories: Categories to filter.
        date_from: Start date for OAI-PMH harvesting.
        date_to: End date for OAI-PMH harvesting.

    Returns:
        Number of papers saved.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if source == "oai-pmh":
        iterator = iter_metadata_from_oai_pmh(
            categories=categories,
            date_from=date_from,
            date_to=date_to,
        )
    elif source == "kaggle":
        if not metadata_path:
            raise ValueError("metadata_path required for kaggle source")
        iterator = iter_metadata_from_kaggle(metadata_path, categories)
    else:
        raise ValueError(f"Unknown source: {source}")

    # Resume support: if file exists, load already-downloaded IDs and append
    seen_ids: set[str] = set()
    if output_path.exists() and output_path.stat().st_size > 0:
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    seen_ids.add(json.loads(line)["arxiv_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
        logger.info(f"Resuming: {len(seen_ids):,} papers already downloaded, appending new ones")

    count = len(seen_ids)
    # Open in append mode to preserve existing data
    with open(output_path, "a", encoding="utf-8") as f:
        for paper in iterator:
            if paper["arxiv_id"] in seen_ids:
                continue
            seen_ids.add(paper["arxiv_id"])
            f.write(json.dumps(paper) + "\n")
            count += 1

            # Checkpoint: flush to disk every 5K papers
            if count % 5_000 == 0:
                f.flush()
                os.fsync(f.fileno())
                logger.info(f"Checkpoint: {count:,} papers saved to disk")

    logger.info(f"Saved {count:,} papers to {output_path}")
    return count


def iter_metadata_from_jsonl(
    jsonl_path: str | Path,
) -> Generator[dict, None, None]:
    """Iterate over papers from a previously saved JSONL file.

    Args:
        jsonl_path: Path to JSONL file (created by save_metadata_to_jsonl).

    Yields:
        Dict with paper metadata.
    """
    jsonl_path = Path(jsonl_path)
    logger.info(f"Reading metadata from {jsonl_path}")

    count = 0
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
                count += 1
                if count % 500_000 == 0:
                    logger.info(f"Read {count:,} papers")
            except json.JSONDecodeError:
                continue

    logger.info(f"Read {count:,} papers total from {jsonl_path}")


def count_papers(metadata_path: str | Path, categories: list[str] | None = None) -> int:
    """Count papers matching target categories without loading all into memory."""
    return sum(1 for _ in iter_metadata_from_kaggle(metadata_path, categories))
