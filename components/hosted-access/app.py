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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.mcpserver.context import Context
from pydantic import BaseModel, Field, field_validator

from store import LimitExceeded, Store


log = logging.getLogger("tas.hosted")
logging.basicConfig(level=logging.INFO)

SEARCH_URL = os.environ.get("HOSTED_SEARCH_URL", "http://127.0.0.1:8081").rstrip("/")
REVIEW_URL = os.environ.get("HOSTED_REVIEW_URL", "http://127.0.0.1:8082").rstrip("/")
PUBLIC_HOST = os.environ.get("HOSTED_PUBLIC_HOST", "localhost")
MAX_PENDING = int(os.environ.get("HOSTED_MAX_PENDING", "10"))
REVIEWS_PER_IP_DAY = int(os.environ.get("HOSTED_REVIEWS_PER_IP_DAY", "3"))
REVIEWS_GLOBAL_DAY = int(os.environ.get("HOSTED_REVIEWS_GLOBAL_DAY", "24"))
SEARCHES_PER_IP_HOUR = int(os.environ.get("HOSTED_SEARCHES_PER_IP_HOUR", "60"))
QUERIES_PER_IP_HOUR = int(os.environ.get("HOSTED_QUERIES_PER_IP_HOUR", "10"))
RETAIN_DAYS = int(os.environ.get("HOSTED_RETAIN_DAYS", "7"))
ARCHIVE_DIR = os.environ.get("HOSTED_REVIEW_ARCHIVE_DIR", "")
INDEX_MARKER = os.environ.get("HOSTED_SEARCH_INDEX_MARKER", "")
SKILL_PATH = Path(os.environ.get(
    "HOSTED_SKILL_PATH",
    str(Path(__file__).resolve().parents[2] / "skills/appliedscientist/SKILL.md"),
))

store = Store(
    os.environ.get("HOSTED_DB_PATH", "/var/lib/theappliedscientist/hosted/jobs.db"),
    os.environ["HOSTED_HASH_SECRET"],
)


def index_ready() -> bool:
    return not INDEX_MARKER or Path(INDEX_MARKER).is_file()


class ReviewInput(BaseModel):
    latex_content: str = Field(min_length=500, max_length=2_000_000,
                               description="Full LaTeX source of the paper")
    title: str = Field(min_length=1, max_length=500)
    abstract: str = Field(default="", max_length=10_000)


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


def submit_review(ip: str, data: ReviewInput) -> dict:
    try:
        return store.enqueue(ip, data.title, data.abstract, data.latex_content,
                             max_pending=MAX_PENDING, max_per_day=REVIEWS_PER_IP_DAY,
                             max_global_per_day=REVIEWS_GLOBAL_DAY)
    except LimitExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc


async def review_worker() -> None:
    """One worker means at most one global review on this 2-core host."""
    last_cleanup = 0.0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                if asyncio.get_running_loop().time() - last_cleanup > 3600:
                    store.cleanup(RETAIN_DAYS, ARCHIVE_DIR or None)
                    last_cleanup = asyncio.get_running_loop().time()
                job = store.next_job()
                if job is None:
                    await asyncio.sleep(3)
                    continue
                if job["status"] == "pending":
                    if not index_ready():
                        await asyncio.sleep(10)
                        continue
                    # A repeat POST after a lost response must not start a
                    # second paid review. The backend treats this ID as idempotent.
                    expected_backend_id = hashlib.sha256(job["id"].encode()).hexdigest()[:24]
                    response = await client.post(f"{REVIEW_URL}/review/start", json={
                        "job_id": expected_backend_id,
                        "latex_content": job["latex_content"],
                        "title": job["title"],
                        "abstract": job["abstract"],
                    })
                    if 400 <= response.status_code < 500:
                        store.finish(job["id"], "error", error="Review service rejected this paper")
                        log.warning("Review service rejected job %s: HTTP %s", job["id"], response.status_code)
                        continue
                    response.raise_for_status()
                    try:
                        backend_id = response.json()["job_id"]
                    except (ValueError, KeyError, TypeError):
                        store.finish(job["id"], "error", error="Review service returned an invalid job ID")
                        continue
                    if backend_id != expected_backend_id:
                        store.finish(job["id"], "error", error="Review service returned an unexpected job ID")
                        continue
                    store.set_running(job["id"], backend_id)
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
                log.exception("Review worker retrying after an error")
                await asyncio.sleep(10)


mcp = MCPServer(
    "TheAppliedScientist",
    instructions=("Search the arXiv CS/statistics index and request independent "
                  "paper reviews from the AppliedScientist AI Reviewer. "
                  "Use start_review, then poll review_status; reviews take several minutes."),
)


def mcp_ip(ctx: Context) -> str:
    return request_ip(ctx.headers or {})


@mcp.tool()
async def search_papers(query: str, ctx: Context, max_results: int = 10,
                        date_to: str | None = None) -> dict:
    """Search relevant arXiv CS/statistics papers by semantic and keyword match."""
    data = SearchInput(query=query, max_results=max_results, date_to=date_to)
    return await search_call(mcp_ip(ctx), "search", data.model_dump(exclude_none=True))


@mcp.tool()
async def batch_search_papers(queries: list[str], ctx: Context,
                              max_results: int = 10, date_to: str | None = None) -> dict:
    """Search several formulations of a research topic in one call."""
    data = BatchInput(queries=queries, max_results=max_results, date_to=date_to)
    return await search_call(mcp_ip(ctx), "batch_search", data.model_dump(exclude_none=True))


@mcp.tool()
async def find_related_papers(arxiv_id: str, ctx: Context,
                              max_results: int = 10) -> dict:
    """Find papers related to an arXiv paper ID."""
    data = RelatedInput(arxiv_id=arxiv_id, max_results=max_results)
    return await search_call(mcp_ip(ctx), "find_related", data.model_dump())


@mcp.tool()
async def query_papers(arxiv_ids: list[str], query: str, ctx: Context) -> dict:
    """Read full papers and answer a specific question grounded in their text."""
    data = QueryInput(arxiv_ids=arxiv_ids, query=query)
    return await search_call(mcp_ip(ctx), "query_paper", data.model_dump(), expensive=True)


@mcp.tool()
def start_review(latex_content: str, title: str, ctx: Context,
                 abstract: str = "") -> dict:
    """Queue a full independent review of a paper. Pass the complete LaTeX source."""
    data = ReviewInput(latex_content=latex_content, title=title, abstract=abstract)
    return submit_review(mcp_ip(ctx), data)


@mcp.tool()
def review_status(job_id: str) -> dict:
    """Get queue position or final feedback for a submitted review job."""
    result = store.get(job_id)
    if not result:
        return {"error": "unknown or expired job_id", "status": "not_found"}
    return result


@mcp.prompt()
def appliedscientist_workflow() -> str:
    """Instructions for an agent to run the full AppliedScientist revision loop."""
    return SKILL_PATH.read_text(encoding="utf-8")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with mcp.session_manager.run():
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
)


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
async def review_api(data: ReviewInput, request: Request) -> dict:
    return submit_review(request_ip(request.headers, request.client.host), data)


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
    allowed_hosts=[PUBLIC_HOST, "127.0.0.1:*", "localhost:*"],
    allowed_origins=[],
)
app.mount("/", mcp.streamable_http_app(stateless_http=True, json_response=True,
                                       transport_security=security))
