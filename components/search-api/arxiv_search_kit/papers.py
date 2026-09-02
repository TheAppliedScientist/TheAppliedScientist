"""Download papers from ArXiv — PDF and LaTeX source archives."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from arxiv_search_kit.exceptions import DownloadError
from arxiv_search_kit.models import Paper

logger = logging.getLogger(__name__)

ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_SOURCE_URL = "https://arxiv.org/e-print/{arxiv_id}"


def download_pdf(
    paper: Paper | str,
    output_dir: str | Path = ".",
    filename: str | None = None,
    timeout: float = 60.0,
) -> Path:
    """Download a paper's PDF from ArXiv.

    Args:
        paper: Paper object or ArXiv ID string.
        output_dir: Directory to save into (created if needed).
        filename: Custom filename. Defaults to ``{arxiv_id}.pdf``.
        timeout: HTTP timeout in seconds.

    Returns:
        Path to the downloaded file.
    """
    arxiv_id = paper.arxiv_id if isinstance(paper, Paper) else paper
    filename = filename or f"{arxiv_id.replace('/', '_')}.pdf"
    return _download(ARXIV_PDF_URL.format(arxiv_id=arxiv_id), output_dir, filename, timeout)


def download_source(
    paper: Paper | str,
    output_dir: str | Path = ".",
    filename: str | None = None,
    timeout: float = 60.0,
) -> Path:
    """Download a paper's LaTeX source archive from ArXiv.

    The e-print endpoint returns a tar.gz for most papers,
    or a single gzipped file for single-file submissions.

    Args:
        paper: Paper object or ArXiv ID string.
        output_dir: Directory to save into (created if needed).
        filename: Custom filename. Defaults to ``{arxiv_id}.tar.gz``.
        timeout: HTTP timeout in seconds.

    Returns:
        Path to the downloaded archive.
    """
    arxiv_id = paper.arxiv_id if isinstance(paper, Paper) else paper
    filename = filename or f"{arxiv_id.replace('/', '_')}.tar.gz"
    return _download(ARXIV_SOURCE_URL.format(arxiv_id=arxiv_id), output_dir, filename, timeout)


def download_papers(
    papers: list[Paper] | list[str],
    output_dir: str | Path = ".",
    format: str = "pdf",
    timeout: float = 60.0,
) -> list[Path]:
    """Batch download papers. Skips failures with a warning.

    Args:
        papers: List of Paper objects or ArXiv ID strings.
        output_dir: Directory to save into.
        format: ``"pdf"`` or ``"source"``.
        timeout: HTTP timeout per paper.

    Returns:
        Paths to successfully downloaded files.
    """
    download_fn = download_pdf if format == "pdf" else download_source
    paths = []
    for paper in papers:
        try:
            paths.append(download_fn(paper, output_dir=output_dir, timeout=timeout))
        except DownloadError as e:
            arxiv_id = paper.arxiv_id if isinstance(paper, Paper) else paper
            logger.warning("Failed to download %s: %s", arxiv_id, e)
    return paths


def _download(url: str, output_dir: str | Path, filename: str, timeout: float) -> Path:
    """Stream-download a URL to a file. Raises DownloadError on failure."""
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest / filename

    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
    except httpx.HTTPStatusError as e:
        raise DownloadError(f"ArXiv returned {e.response.status_code} for {url}") from e
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise DownloadError(f"Download failed for {url}: {e}") from e

    size = dest.stat().st_size
    unit = "KB" if size < 1_048_576 else "MB"
    val = size / 1024 if unit == "KB" else size / 1_048_576
    logger.info("Downloaded %s (%.1f %s)", filename, val, unit)
    return dest
