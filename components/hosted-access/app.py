"""Public HTTP and MCP access to the existing Search and Reviewer services.

This layer owns admission control and durable job state. It never generates a
review itself; the established Review API, prompt, and Claude Code runner do.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.mcpserver.context import Context
from pydantic import BaseModel, Field, field_validator

from store import LimitExceeded, Store
from documents import (MAX_UPLOAD_BYTES, SOURCE_SUFFIXES, DocumentError, convert_with_datalab,
                       paper_title, safe_filename)


log = logging.getLogger("tas.hosted")
logging.basicConfig(level=logging.INFO)

SEARCH_URL = os.environ.get("HOSTED_SEARCH_URL", "http://127.0.0.1:8081").rstrip("/")
REVIEW_URL = os.environ.get("HOSTED_REVIEW_URL", "http://127.0.0.1:8082").rstrip("/")
PUBLIC_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "HOSTED_PUBLIC_HOSTS",
        os.environ.get("HOSTED_PUBLIC_HOST", "localhost"),
    ).split(",")
    if host.strip()
]
MAX_PENDING = int(os.environ.get("HOSTED_MAX_PENDING", "10"))
MAX_CONCURRENT_REVIEWS = int(os.environ.get("HOSTED_MAX_CONCURRENT_REVIEWS", "5"))
if MAX_CONCURRENT_REVIEWS < 1:
    raise ValueError("HOSTED_MAX_CONCURRENT_REVIEWS must be at least 1")
REVIEWS_PER_IP_DAY = int(os.environ.get("HOSTED_REVIEWS_PER_IP_DAY", "3"))
REVIEWS_GLOBAL_DAY = int(os.environ.get("HOSTED_REVIEWS_GLOBAL_DAY", "24"))
SEARCHES_PER_IP_HOUR = int(os.environ.get("HOSTED_SEARCHES_PER_IP_HOUR", "60"))
QUERIES_PER_IP_HOUR = int(os.environ.get("HOSTED_QUERIES_PER_IP_HOUR", "10"))
RETAIN_DAYS = int(os.environ.get("HOSTED_RETAIN_DAYS", "7"))
ARCHIVE_DIR = os.environ.get("HOSTED_REVIEW_ARCHIVE_DIR", "")
INDEX_MARKER = os.environ.get("HOSTED_SEARCH_INDEX_MARKER", "")
DATALAB_API_KEY = os.environ.get("DATALAB_API_KEY", "")
SKILL_PATH = Path(os.environ.get(
    "HOSTED_SKILL_PATH",
    str(Path(__file__).resolve().parents[2] / "skills/appliedscientist/SKILL.md"),
))
UPLOAD_DIR = Path(os.environ.get(
    "HOSTED_UPLOAD_DIR", "/var/lib/theappliedscientist/hosted/uploads",
))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

store = Store(
    os.environ.get("HOSTED_DB_PATH", "/var/lib/theappliedscientist/hosted/jobs.db"),
    os.environ["HOSTED_HASH_SECRET"],
)


def index_ready() -> bool:
    return not INDEX_MARKER or Path(INDEX_MARKER).is_file()


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=10, ge=1, le=20)
    date_to: str | None = None
    year: int | None = None
    sort_by: str = "importance"


class BatchInput(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=5)
    max_results: int = Field(default=10, ge=1, le=20)
    date_to: str | None = None
    year: int | None = None
    sort_by: str = "importance"

    @field_validator("queries")
    @classmethod
    def valid_queries(cls, queries: list[str]) -> list[str]:
        if any(not q.strip() or len(q) > 2000 for q in queries):
            raise ValueError("Each query must contain 1 to 2000 characters")
        return queries


class RelatedInput(BaseModel):
    arxiv_id: str = Field(min_length=4, max_length=40)
    max_results: int = Field(default=10, ge=1, le=20)


class QueryInput(BaseModel):
    arxiv_ids: list[str] = Field(min_length=1, max_length=3)
    query: str = Field(min_length=1, max_length=2000)

    @field_validator("arxiv_ids")
    @classmethod
    def valid_ids(cls, ids: list[str]) -> list[str]:
        if any(not id.strip() or len(id) > 40 for id in ids):
            raise ValueError("Each arXiv ID must contain 1 to 40 characters")
        return ids


def request_ip(headers: Any, fallback: str = "unknown") -> str:
    # The service binds localhost and Nginx overwrites X-Real-IP with its peer.
    return headers.get("x-real-ip") or fallback


async def search_call(ip: str, route: str, payload: dict, *, expensive: bool = False) -> dict:
    if not index_ready():
        raise HTTPException(503, "The Search index is still loading; try again shortly")
    try:
        store.record_usage(ip, "paper_query" if expensive else "search",
                           QUERIES_PER_IP_HOUR if expensive else SEARCHES_PER_IP_HOUR)
    except LimitExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    try:
        async with httpx.AsyncClient(timeout=180 if expensive else 45) as client:
            response = await client.post(f"{SEARCH_URL}/{route}", json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code,
                            exc.response.text[:500]) from exc
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Search request failed: %s", exc)
        raise HTTPException(503, "Search is temporarily unavailable") from exc


def discard_upload(path: str) -> None:
    if path:
        upload = Path(path)
        if upload.resolve().parent == UPLOAD_DIR.resolve():
            upload.unlink(missing_ok=True)


async def review_worker(poll_interval: float = 3) -> None:
    """Schedule up to the global limit in this single-process gateway.

    Each task owns one job until completion, including preparation and retries.
    Existing backend jobs are resumed first after a gateway restart.
    """
    active: dict[str, asyncio.Task[None]] = {}
    last_cleanup = 0.0
    log.info("Global concurrent review limit: %s", MAX_CONCURRENT_REVIEWS)
    try:
        while True:
            try:
                for job_id, task in list(active.items()):
                    if task.done():
                        del active[job_id]
                        if not task.cancelled() and (error := task.exception()):
                            log.error("Review task failed for %s", job_id, exc_info=error)
                if asyncio.get_running_loop().time() - last_cleanup > 3600:
                    store.cleanup(RETAIN_DAYS, ARCHIVE_DIR or None)
                    last_cleanup = asyncio.get_running_loop().time()
                while len(active) < MAX_CONCURRENT_REVIEWS:
                    job = store.next_job(exclude_ids=tuple(active))
                    if job is None:
                        break
                    job_id = job["id"]
                    active[job_id] = asyncio.create_task(
                        process_review(job_id), name=f"review-{job_id}",
                    )
            except Exception:
                log.exception("Review scheduler retrying after an error")
            await asyncio.sleep(poll_interval)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)


async def process_review(job_id: str) -> None:
    """Prepare, submit and monitor one job without giving up its review slot."""
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                job = store.get_work_item(job_id)
                if job is None:
                    return
                if job["status"] == "preparing":
                    upload = Path(job["input_path"])
                    remove_upload = False
                    try:
                        if upload.resolve().parent != UPLOAD_DIR.resolve():
                            raise DocumentError("Invalid stored upload path")
                        if upload.suffix.lower() in SOURCE_SUFFIXES:
                            store.set_source_ready(job["id"], job["title"] or Path(job["input_name"]).stem)
                            log.info("Queued direct source review %s from %s", job["id"], upload.suffix)
                        else:
                            data = await asyncio.to_thread(upload.read_bytes)
                            paper = await convert_with_datalab(
                                job["input_name"], data, DATALAB_API_KEY,
                            )
                            if len(paper) > 2_000_000:
                                raise DocumentError("Converted paper is too large to review")
                            title = job["title"] or paper_title(paper) or Path(job["input_name"]).stem
                            store.set_prepared(job["id"], paper, title)
                            remove_upload = True
                            log.info("Prepared review %s from %s", job["id"], upload.suffix)
                    except (DocumentError, OSError) as exc:
                        store.finish(job["id"], "error", error=str(exc))
                        remove_upload = True
                        log.warning("Could not prepare review %s: %s", job["id"], exc)
                    except Exception:
                        store.finish(job["id"], "error", error="Could not convert this paper")
                        remove_upload = True
                        log.exception("Unexpected conversion failure for review %s", job["id"])
                    finally:
                        if remove_upload and upload.resolve().parent == UPLOAD_DIR.resolve():
                            upload.unlink(missing_ok=True)
                    continue
                if job["status"] == "pending":
                    if not index_ready():
                        await asyncio.sleep(10)
                        continue
                    # A repeat POST after a lost response must not start a
                    # second paid review. The backend treats this ID as idempotent.
                    expected_backend_id = hashlib.sha256(job["id"].encode()).hexdigest()[:24]
                    if job["input_path"]:
                        upload = Path(job["input_path"])
                        if upload.resolve().parent != UPLOAD_DIR.resolve():
                            store.finish(job["id"], "error", error="Invalid stored upload path")
                            continue
                        try:
                            source = await asyncio.to_thread(upload.read_bytes)
                        except OSError:
                            store.finish(job["id"], "error", error="Stored source upload is missing")
                            continue
                        response = await client.post(
                            f"{REVIEW_URL}/review/start_source",
                            data={"job_id": expected_backend_id, "title": job["title"],
                                  "abstract": job["abstract"]},
                            files={"paper": (job["input_name"], source)},
                        )
                    else:
                        note = ("Uploaded document converted to Markdown from its first 12 pages; "
                                "the readable paper is /app/latex/template.tex.\n") if job["input_name"] else ""
                        response = await client.post(f"{REVIEW_URL}/review/start", json={
                            "job_id": expected_backend_id,
                            "latex_content": job["latex_content"],
                            "title": job["title"],
                            "abstract": job["abstract"],
                            "input_note": note,
                        })
                    if 400 <= response.status_code < 500:
                        try:
                            error = response.json().get("error", "Review service rejected this paper")
                        except (ValueError, AttributeError):
                            error = "Review service rejected this paper"
                        store.finish(job["id"], "error", error=str(error)[:500])
                        discard_upload(job["input_path"])
                        log.warning("Review service rejected job %s: HTTP %s", job["id"], response.status_code)
                        continue
                    response.raise_for_status()
                    try:
                        backend_id = response.json()["job_id"]
                    except (ValueError, KeyError, TypeError):
                        store.finish(job["id"], "error", error="Review service returned an invalid job ID")
                        discard_upload(job["input_path"])
                        continue
                    if backend_id != expected_backend_id:
                        store.finish(job["id"], "error", error="Review service returned an unexpected job ID")
                        discard_upload(job["input_path"])
                        continue
                    store.set_running(job["id"], backend_id)
                    discard_upload(job["input_path"])
                    log.info("Started review %s", job["id"])
                    continue
                backend_id = job["backend_id"]
                if not backend_id:
                    store.finish(job["id"], "error", error="Review service lost job state")
                    continue
                response = await client.get(f"{REVIEW_URL}/review/status/{backend_id}")
                if response.status_code == 404:
                    store.finish(job["id"], "error", error="Review service restarted during this job")
                    continue
                response.raise_for_status()
                result = response.json()
                if result["status"] in {"success", "error", "timeout"}:
                    store.finish(job["id"], result["status"],
                                 review_text=result.get("review_text", ""),
                                 error=result.get("error", ""))
                    log.info("Finished review %s: %s", job["id"], result["status"])
                else:
                    await asyncio.sleep(8)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Review %s retrying after an error", job_id)
                await asyncio.sleep(10)


search_mcp = MCPServer(
    "TheAppliedScientist Search",
    instructions="Search arXiv papers. Searching does not start a review.",
)
review_mcp = MCPServer(
    "TheAppliedScientist Reviewer",
    instructions=("Call review_paper with the paper filename, send the local "
                  "file's exact bytes by HTTP PUT to its short-lived upload_url, then "
                  "call review_status with the job_id until complete. The hosted Reviewer "
                  "searches related work itself and reads at most 12 rendered pages. "
                  "Use your own file/network tools for the transfer; do not ask the "
                  "user to run an upload command or paste file contents into MCP."),
)


def mcp_ip(ctx: Context) -> str:
    return request_ip(ctx.headers or {})


@search_mcp.tool()
async def search_papers(query: str, ctx: Context, max_results: int = 10,
                        date_to: str | None = None) -> dict:
    """Search relevant arXiv CS/statistics papers by semantic and keyword match."""
    data = SearchInput(query=query, max_results=max_results, date_to=date_to)
    return await search_call(mcp_ip(ctx), "search", data.model_dump(exclude_none=True))


@search_mcp.tool()
async def batch_search_papers(queries: list[str], ctx: Context,
                              max_results: int = 10, date_to: str | None = None) -> dict:
    """Search several formulations of a research topic in one call."""
    data = BatchInput(queries=queries, max_results=max_results, date_to=date_to)
    return await search_call(mcp_ip(ctx), "batch_search", data.model_dump(exclude_none=True))


@search_mcp.tool()
async def find_related_papers(arxiv_id: str, ctx: Context,
                              max_results: int = 10) -> dict:
    """Find papers related to an arXiv paper ID."""
    data = RelatedInput(arxiv_id=arxiv_id, max_results=max_results)
    return await search_call(mcp_ip(ctx), "find_related", data.model_dump())


@search_mcp.tool()
async def query_papers(arxiv_ids: list[str], query: str, ctx: Context) -> dict:
    """Read full papers and answer a specific question grounded in their text."""
    data = QueryInput(arxiv_ids=arxiv_ids, query=query)
    return await search_call(mcp_ip(ctx), "query_paper", data.model_dump(), expensive=True)


@review_mcp.tool()
def review_paper(filename: str, ctx: Context,
                 title: str = "", abstract: str = "") -> dict:
    """Review a paper file. Returns a one-use URL for the agent to PUT the local
    file's bytes, then a private job ID to check with review_status.
    """
    try:
        filename = safe_filename(filename)
    except DocumentError as exc:
        return {"error": str(exc)}
    if len(title) > 500 or len(abstract) > 10_000:
        return {"error": "Title or abstract is too long"}
    try:
        result = store.reserve_upload(
            mcp_ip(ctx), title.strip(), abstract.strip(), filename,
            max_pending=MAX_PENDING, max_per_day=REVIEWS_PER_IP_DAY,
            max_global_per_day=REVIEWS_GLOBAL_DAY,
        )
    except LimitExceeded as exc:
        return {"error": str(exc), "retry_after_seconds": exc.retry_after}
    return {"job_id": result["job_id"], "status": "awaiting_upload",
            "upload_url": f"https://review.eigenlabs.online/api/reviews/{result['job_id']}/paper",
            "upload_method": "PUT", "expires_in_seconds": 600,
            "next_step": "Send the file's exact bytes to upload_url, then call review_status."}


@review_mcp.tool()
def review_status(job_id: str) -> dict:
    """Get queue position or final feedback for a submitted review job."""
    result = store.get(job_id)
    if not result:
        return {"error": "unknown or expired job_id", "status": "not_found"}
    return result


@review_mcp.prompt()
def appliedscientist_workflow() -> str:
    """Instructions for an agent to run the full AppliedScientist revision loop."""
    return SKILL_PATH.read_text(encoding="utf-8")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with search_mcp.session_manager.run(), review_mcp.session_manager.run():
        worker = asyncio.create_task(review_worker())
        try:
            yield
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker


app = FastAPI(
    title="TheAppliedScientist Search and Review APIs",
    description="Hosted access to the original literature Search and AI Reviewer. Full reviews run asynchronously.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def separate_public_apis(request: Request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    path = request.url.path
    if host == "search.eigenlabs.online" and path.startswith("/api/reviews"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if host == "review.eigenlabs.online" and path.startswith("/api/search"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return await call_next(request)


@app.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> JSONResponse:
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    schema = app.openapi()
    if host == "search.eigenlabs.online":
        prefix, title = "/api/search", "TheAppliedScientist Search API"
    elif host == "review.eigenlabs.online":
        prefix, title = "/api/reviews", "TheAppliedScientist AI Reviewer API"
    else:
        return JSONResponse(schema)
    return JSONResponse({**schema, "info": {**schema["info"], "title": title},
                         "paths": {path: detail for path, detail in schema["paths"].items()
                                   if path == "/health" or path.startswith(prefix)}})


@app.get("/docs", include_in_schema=False)
async def public_docs(request: Request):
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    title = ("Search API" if host == "search.eigenlabs.online" else
             "AI Reviewer API" if host == "review.eigenlabs.online" else
             "TheAppliedScientist APIs")
    return get_swagger_ui_html(openapi_url="/openapi.json", title=title)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok" if index_ready() else "starting", "search_ready": index_ready()}


@app.post("/api/search")
async def search_api(data: SearchInput, request: Request) -> dict:
    return await search_call(request_ip(request.headers, request.client.host), "search",
                             data.model_dump(exclude_none=True))


@app.post("/api/search/batch")
async def batch_api(data: BatchInput, request: Request) -> dict:
    return await search_call(request_ip(request.headers, request.client.host), "batch_search",
                             data.model_dump(exclude_none=True))


@app.post("/api/search/related")
async def related_api(data: RelatedInput, request: Request) -> dict:
    return await search_call(request_ip(request.headers, request.client.host), "find_related",
                             data.model_dump())


@app.post("/api/search/query")
async def query_api(data: QueryInput, request: Request) -> dict:
    return await search_call(request_ip(request.headers, request.client.host), "query_paper",
                             data.model_dump(), expensive=True)


@app.post("/api/reviews", status_code=202)
@app.post("/api/reviews/upload", status_code=202, include_in_schema=False)
async def review_file_api(
    request: Request,
    paper: UploadFile = File(description="Paper file; the first 12 pages are reviewed"),
    title: str = Form(default=""),
    abstract: str = Form(default=""),
) -> dict:
    try:
        filename = safe_filename(paper.filename or "")
    except DocumentError as exc:
        raise HTTPException(415, str(exc)) from exc
    if Path(filename).suffix.lower() not in SOURCE_SUFFIXES and not DATALAB_API_KEY:
        raise HTTPException(503, "Document conversion is not configured")
    if len(title) > 500 or len(abstract) > 10_000:
        raise HTTPException(422, "Title or abstract is too long")
    data = await paper.read(MAX_UPLOAD_BYTES + 1)
    await paper.close()
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload must be between 1 byte and 20 MB")
    if filename.lower().endswith(".pdf") and not data.startswith(b"%PDF-"):
        raise HTTPException(415, "The uploaded file is not a PDF")
    upload = UPLOAD_DIR / f"{secrets.token_hex(24)}{Path(filename).suffix.lower()}"
    try:
        with os.fdopen(os.open(upload, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as out:
            out.write(data)
        return store.enqueue_upload(
            request_ip(request.headers, request.client.host),
            title.strip(), abstract.strip(),
            str(upload), filename, max_pending=MAX_PENDING,
            max_per_day=REVIEWS_PER_IP_DAY,
            max_global_per_day=REVIEWS_GLOBAL_DAY,
        )
    except LimitExceeded as exc:
        upload.unlink(missing_ok=True)
        raise HTTPException(429, str(exc),
                            headers={"Retry-After": str(exc.retry_after)}) from exc
    except Exception:
        upload.unlink(missing_ok=True)
        raise


@app.put("/api/reviews/{job_id}/paper", status_code=202)
async def upload_reserved_paper(job_id: str, request: Request) -> dict:
    filename = store.reserved_upload_name(job_id)
    if not filename:
        raise HTTPException(404, "Upload link is unknown, expired, or already used")
    upload = UPLOAD_DIR / f"{secrets.token_hex(24)}{Path(filename).suffix.lower()}"
    size = 0
    first_bytes = b""
    try:
        with os.fdopen(os.open(upload, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Paper must be at most 20 MB")
                if len(first_bytes) < 5:
                    first_bytes += chunk[:5 - len(first_bytes)]
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "Paper file is empty")
        if filename.lower().endswith(".pdf") and first_bytes != b"%PDF-":
            raise HTTPException(415, "The uploaded file is not a PDF")
        try:
            store.attach_reserved_upload(
                job_id, str(upload), max_per_day=REVIEWS_PER_IP_DAY,
                max_global_per_day=REVIEWS_GLOBAL_DAY,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except LimitExceeded as exc:
            raise HTTPException(429, str(exc),
                                headers={"Retry-After": str(exc.retry_after)}) from exc
    except Exception:
        upload.unlink(missing_ok=True)
        raise
    return {"job_id": job_id, "status": "preparing"}


@app.get("/api/reviews/{job_id}")
async def review_status_api(job_id: str) -> dict:
    result = store.get(job_id)
    if not result:
        raise HTTPException(404, "unknown or expired job_id")
    return result


@app.exception_handler(LimitExceeded)
async def limit_handler(_request: Request, exc: LimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=429,
                        headers={"Retry-After": str(exc.retry_after)})


security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[*PUBLIC_HOSTS, "127.0.0.1:*", "localhost:*"],
    allowed_origins=[],
)


class McpByHost:
    """Serve independent MCP tool lists at the Search and Reviewer hostnames."""

    def __init__(self) -> None:
        self.search = search_mcp.streamable_http_app(
            stateless_http=True, json_response=True, transport_security=security)
        self.review = review_mcp.streamable_http_app(
            stateless_http=True, json_response=True, transport_security=security)

    async def __call__(self, scope, receive, send) -> None:
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("ascii", errors="ignore").split(":", 1)[0].lower()
        target = self.review if host == "review.eigenlabs.online" else self.search
        await target(scope, receive, send)


app.mount("/", McpByHost())
