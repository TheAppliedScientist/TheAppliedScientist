"""Prepare uploaded papers for the existing Reviewer agent."""

from __future__ import annotations

import asyncio
import io
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader, PdfWriter


MAX_PAGES = 12
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
CONVERT_URL = "https://www.datalab.to/api/v1/convert"
SUPPORTED_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".odt", ".html", ".epub",
    ".png", ".jpg", ".jpeg", ".webp", ".tex", ".zip",
}
SOURCE_SUFFIXES = {".tex", ".zip"}


class DocumentError(Exception):
    """The upload cannot be safely converted into a reviewable paper."""


def safe_filename(name: str) -> str:
    filename = Path(name.replace("\\", "/")).name[:150]
    if not filename or Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise DocumentError("Upload a PDF, TeX, LaTeX zip, Word document, HTML, EPUB, or image")
    return filename


def first_twelve_pdf_pages(data: bytes) -> tuple[bytes, int]:
    """Return a PDF containing no more than the first 12 original pages."""
    if not data.startswith(b"%PDF-"):
        raise DocumentError("The uploaded file is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentError("Password-protected PDFs are not supported")
        count = len(reader.pages)
        if count == 0:
            raise DocumentError("The PDF has no pages")
        if count <= MAX_PAGES:
            return data, count
        writer = PdfWriter()
        for page in reader.pages[:MAX_PAGES]:
            writer.add_page(page)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue(), MAX_PAGES
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("The PDF could not be read") from exc


def _result_markdown(result: dict) -> str:
    if result.get("status") != "complete":
        raise DocumentError(result.get("error") or "Document conversion did not complete")
    if result.get("success") is False:
        raise DocumentError(result.get("error") or "Document conversion failed")
    pages = result.get("page_count")
    if not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES:
        raise DocumentError("Document conversion did not confirm the 12-page limit")
    failed = (result.get("metadata") or {}).get("failed_pages") or []
    if failed:
        raise DocumentError("Document conversion missed one or more pages")
    markdown = result.get("markdown")
    if not isinstance(markdown, str) or len(markdown.strip()) < 200:
        raise DocumentError("Document conversion returned too little text to review")
    return markdown


def paper_title(markdown: str) -> str | None:
    """Use the first H1 near the start of a converted paper as its title."""
    for line in markdown.splitlines()[:40]:
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            title = re.sub(r"<[^>]+>", "", match.group(1)).strip(" *#")
            return title[:500] if title else None
    return None


async def convert_with_datalab(filename: str, data: bytes, api_key: str) -> str:
    """Use Datalab's highest-accuracy conversion, never processing >12 pages."""
    if not api_key:
        raise DocumentError("Document conversion is not configured")
    filename = safe_filename(filename)
    if Path(filename).suffix.lower() in SOURCE_SUFFIXES:
        raise DocumentError("TeX source goes directly to the Reviewer, not to OCR")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError("Upload must be between 1 byte and 20 MB")
    if filename.lower().endswith(".pdf"):
        data, _ = first_twelve_pdf_pages(data)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    headers = {"X-API-Key": api_key, "User-Agent": "Mozilla/5.0"}
    form = {"mode": "accurate", "output_format": "markdown",
            "max_pages": str(MAX_PAGES), "paginate": "true"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        try:
            submitted = await client.post(CONVERT_URL, headers=headers, data=form,
                                          files={"file": (filename, data, content_type)})
            submitted.raise_for_status()
            start = submitted.json()
            if start.get("success") is False:
                raise DocumentError(start.get("error") or "Datalab rejected the upload")
            request_id = start.get("request_id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
                raise DocumentError("Datalab returned an invalid request ID")
            for _ in range(150):
                result_response = await client.get(f"{CONVERT_URL}/{request_id}", headers=headers)
                result_response.raise_for_status()
                result = result_response.json()
                if result.get("status") == "complete":
                    if result.get("result_url") and not result.get("markdown"):
                        signed_url = result["result_url"]
                        parsed = urlparse(signed_url)
                        if parsed.scheme != "https" or not parsed.hostname:
                            raise DocumentError("Datalab returned an invalid result URL")
                        downloaded = await client.get(signed_url, follow_redirects=True)
                        downloaded.raise_for_status()
                        result = {**result, **downloaded.json()}
                    return _result_markdown(result)
                if result.get("status") in {"failed", "error"}:
                    raise DocumentError(result.get("error") or "Datalab conversion failed")
                await asyncio.sleep(2)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {400, 401, 403, 413, 415, 422}:
                raise DocumentError("Datalab rejected this file or conversion request") from exc
            raise DocumentError("Datalab is temporarily unavailable") from exc
        except httpx.HTTPError as exc:
            raise DocumentError("Datalab is temporarily unavailable") from exc
    raise DocumentError("Datalab conversion timed out")
