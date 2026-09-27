"""
Search API server.

HTTP interface to the arXiv search index, called by sandboxed agents.

Exposes: search, batch_search, find_related, enrich, citations/references,
and download.

Pass "embedding": "gemini" in the request body to use Gemini-2 embeddings.
Defaults to "gemini".

Usage:
    python search_api.py [--port 8081] [--gemini-api-key AIza...] [--gemini-index-dir /path]
"""

import argparse
import logging
import os
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("search-api")

MAX_QUERIES = 100
MAX_RESULTS = 500
MAX_QUERY_LEN = 10_000


def _bad(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def _validate_queries(queries) -> tuple[list[str] | None, web.Response | None]:
    if not isinstance(queries, list) or len(queries) == 0:
        return None, _bad("'queries' must be a non-empty list of strings")
    if len(queries) > MAX_QUERIES:
        return None, _bad(f"Too many queries (max {MAX_QUERIES})")
    cleaned = []
    for q in queries:
        if not isinstance(q, str):
            return None, _bad("Each query must be a string")
        q = q.strip()
        if not q:
            return None, _bad("Queries must not be empty strings")
        if len(q) > MAX_QUERY_LEN:
            return None, _bad(f"Query too long (max {MAX_QUERY_LEN} chars)")
        cleaned.append(q)
    return cleaned, None


def _validate_max_results(body: dict) -> tuple[int | None, web.Response | None]:
    val = body.get("max_results", 20)
    if not isinstance(val, int) or isinstance(val, bool):
        return None, _bad("'max_results' must be an integer")
    if val < 1 or val > MAX_RESULTS:
        return None, _bad(f"'max_results' must be between 1 and {MAX_RESULTS}")
    return val, None

# Both clients are loaded lazily on first use
_clients: dict = {}  # gemini-only (specter2 model/index deleted)
_gemini_api_key: str | None = None
_gemini_index_dir: str | None = None


def _get_client(embedding: str = "gemini"):
    global _clients, _gemini_api_key, _gemini_index_dir
    # gemini-only: any specter2 request (legacy callers) is routed to the gemini client.
    if embedding == "specter2":
        embedding = "gemini"
    if embedding not in _clients:
        from arxiv_search_kit import ArxivClient
        log.info("Initializing ArxivClient(embedding=gemini)...")
        _clients["gemini"] = ArxivClient(
            embedding="gemini",
            gemini_api_key=_gemini_api_key,
            index_dir=_gemini_index_dir,
            device="cpu",
        )
        log.info("ArxivClient(gemini) ready.")
    return _clients[embedding]


def _paper_to_dict(paper):
    d = {
        "arxiv_id": getattr(paper, "arxiv_id", ""),
        "title": getattr(paper, "title", ""),
        "abstract": getattr(paper, "abstract", ""),
        "authors": getattr(paper, "author_names", []),
        "categories": getattr(paper, "categories", []),
        "primary_category": getattr(paper, "primary_category", ""),
        "published": str(getattr(paper, "published", "")),
        "year": getattr(paper, "year", None),
        "similarity_score": getattr(paper, "similarity_score", 0.0),
        "pdf_url": getattr(paper, "pdf_url", ""),
        "abs_url": getattr(paper, "abs_url", ""),
        "doi": getattr(paper, "doi", None),
        "journal_ref": getattr(paper, "journal_ref", None),
        "comment": getattr(paper, "comment", None),
    }
    if getattr(paper, "citation_count", None) is not None:
        d["citation_count"] = paper.citation_count
    if getattr(paper, "influential_citation_count", None) is not None:
        d["influential_citation_count"] = paper.influential_citation_count
    if getattr(paper, "venue", None):
        d["venue"] = paper.venue
    if getattr(paper, "tldr", None):
        d["tldr"] = paper.tldr
    if getattr(paper, "publication_types", None):
        d["publication_types"] = paper.publication_types
    return d


def _build_search_kwargs(body):
    kwargs = {}
    for key in ("categories", "year", "date_from", "date_to",
                "conference", "min_citations", "sort_by",
                "context_paper_id", "context_title", "context_abstract",
                "details"):
        val = body.get(key)
        if val is not None:
            kwargs[key] = val
    return kwargs


async def handle_search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _bad("Invalid JSON body")
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        return _bad("'query' must be a non-empty string")
    query = query.strip()
    if len(query) > MAX_QUERY_LEN:
        return _bad(f"'query' too long (max {MAX_QUERY_LEN} chars)")
    max_results, err = _validate_max_results(body)
    if err:
        return err
    embedding = body.get("embedding", "gemini")
    if embedding not in ("specter2", "gemini"):
        return _bad("'embedding' must be 'specter2' or 'gemini'")
    kwargs = _build_search_kwargs(body)
    kwargs["max_results"] = max_results
    log.info(f"search: query={query!r} embedding={embedding} kwargs={kwargs}")

    try:
        client = _get_client(embedding)
        result = client.search(query, **kwargs)
    except Exception as e:
        log.exception("search failed")
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({
        "papers": result.to_dicts(),
        "query": result.query,
        "total": len(result),
        "embedding": embedding,
    })


async def handle_batch_search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _bad("Invalid JSON body")
    queries, err = _validate_queries(body.get("queries"))
    if err:
        return err
    max_results, err = _validate_max_results(body)
    if err:
        return err
    embedding = body.get("embedding", "gemini")
    if embedding not in ("specter2", "gemini"):
        return _bad("'embedding' must be 'specter2' or 'gemini'")
    kwargs = _build_search_kwargs(body)
    kwargs["max_results"] = max_results
    log.info(f"batch_search: {len(queries)} queries, embedding={embedding}, kwargs={kwargs}")

    try:
        client = _get_client(embedding)
        result = client.batch_search(queries, **kwargs)
    except Exception as e:
        log.exception("batch_search failed")
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response({
        "papers": result.to_dicts(),
        "total": len(result),
        "embedding": embedding,
    })


async def handle_find_related(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_id = body.get("arxiv_id", "")
    embedding = body.get("embedding", "gemini")
    max_results = body.get("max_results", 10)
    categories = body.get("categories", None)
    details = body.get("details", "default")
    log.info(f"find_related: arxiv_id={arxiv_id} embedding={embedding}")

    client = _get_client(embedding)
    kwargs = {"max_results": max_results, "details": details}
    if categories:
        kwargs["categories"] = categories
    result = client.find_related(arxiv_id, **kwargs)
    return web.json_response({"papers": result.to_dicts(), "total": len(result), "embedding": embedding})


async def handle_enrich(request: web.Request) -> web.Response:
    from arxiv_search_kit import SearchResult

    body = await request.json()
    arxiv_ids = body.get("arxiv_ids", [])
    fields = body.get("fields", None)
    log.info(f"enrich: {len(arxiv_ids)} papers")

    client = _get_client("specter2")  # enrich doesn't depend on embedding
    papers = []
    for aid in arxiv_ids:
        p = client.get_paper(aid)
        if p is not None:
            papers.append(p)
    if papers:
        sr = SearchResult(papers=papers, query="enrich", total_candidates=len(papers), search_time_ms=0)
        enrich_kwargs = {}
        if fields:
            enrich_kwargs["fields"] = fields
        client.enrich(sr, **enrich_kwargs)
    return web.json_response({
        "papers": [_paper_to_dict(p) for p in papers],
        "total": len(papers),
    })


async def handle_citations(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_id = body.get("arxiv_id", "")
    limit = body.get("limit", 50)
    log.info(f"citations: arxiv_id={arxiv_id} limit={limit}")

    client = _get_client("specter2")
    citations = client.get_citations(arxiv_id, limit=limit)
    return web.json_response({"citations": citations, "total": len(citations)})


async def handle_references(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_id = body.get("arxiv_id", "")
    limit = body.get("limit", 50)
    log.info(f"references: arxiv_id={arxiv_id} limit={limit}")

    client = _get_client("specter2")
    references = client.get_references(arxiv_id, limit=limit)
    return web.json_response({"references": references, "total": len(references)})


async def handle_get_paper(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_id = body.get("arxiv_id", "")
    log.info(f"get_paper: arxiv_id={arxiv_id}")

    client = _get_client("specter2")
    paper = client.get_paper(arxiv_id)
    if paper is None:
        return web.json_response({"error": "Paper not found"}, status=404)
    return web.json_response({"paper": _paper_to_dict(paper)})


_extracted_cache: dict[str, str] = {}


def _extract_source(arxiv_id: str) -> tuple[str | None, str | None]:
    import tarfile, tempfile, gzip

    if arxiv_id in _extracted_cache:
        d = _extracted_cache[arxiv_id]
        if os.path.isdir(d):
            return d, None
        del _extracted_cache[arxiv_id]

    client = _get_client("specter2")
    tmpdir = tempfile.mkdtemp(prefix=f"arxiv_{arxiv_id}_")
    try:
        archive_path = client.download_source(arxiv_id, output_dir=tmpdir)
    except Exception as e:
        return None, f"Download failed: {e}"

    if archive_path is None or not os.path.exists(archive_path):
        return None, f"Source not available for {arxiv_id}"

    extract_dir = os.path.join(tmpdir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=extract_dir, filter="data")
    except Exception:
        try:
            with gzip.open(archive_path, "rb") as gz:
                content = gz.read()
            with open(os.path.join(extract_dir, "main.tex"), "wb") as f:
                f.write(content)
        except Exception as e:
            return None, f"Extraction failed: {e}"

    _extracted_cache[arxiv_id] = extract_dir
    return extract_dir, None


async def handle_download_source(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_id = body.get("arxiv_id", "")
    log.info(f"download_source: arxiv_id={arxiv_id}")

    extract_dir, err = _extract_source(arxiv_id)
    if err:
        return web.json_response({"error": err}, status=404 if "not available" in err else 500)

    files = []
    for root, dirs, filenames in os.walk(extract_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, extract_dir)
            size = os.path.getsize(fpath)
            files.append({"path": rel, "size_bytes": size})
    files.sort(key=lambda f: f["path"])

    return web.json_response({
        "arxiv_id": arxiv_id,
        "files": files,
        "total_files": len(files),
    })


async def handle_read_file(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_id = body.get("arxiv_id", "")
    file_path = body.get("file_path", "")
    max_chars = body.get("max_chars", 100000)
    log.info(f"read_file: arxiv_id={arxiv_id} file={file_path}")

    extract_dir, err = _extract_source(arxiv_id)
    if err:
        return web.json_response({"error": err}, status=404 if "not available" in err else 500)

    full_path = os.path.normpath(os.path.join(extract_dir, file_path))
    if not full_path.startswith(extract_dir):
        return web.json_response({"error": "Invalid path"}, status=400)
    if not os.path.isfile(full_path):
        return web.json_response({"error": f"File not found: {file_path}"}, status=404)

    try:
        with open(full_path, "r", errors="replace") as f:
            content = f.read(max_chars)
        truncated = os.path.getsize(full_path) > max_chars
    except Exception as e:
        return web.json_response({"error": f"Read failed: {e}"}, status=500)

    return web.json_response({
        "arxiv_id": arxiv_id,
        "file_path": file_path,
        "content": content,
        "truncated": truncated,
        "size_bytes": os.path.getsize(full_path),
    })


async def handle_query_paper(request: web.Request) -> web.Response:
    body = await request.json()
    arxiv_ids = body.get("arxiv_ids", [])
    arxiv_id = body.get("arxiv_id", "")
    # support both "query" (new) and "question" (old ask_paper compat)
    query = body.get("query") or body.get("question", "summarize this paper")
    query_format = os.environ.get("SEARCH_QUERY_API_FORMAT", "gemini").lower()
    provider_key = {
        "gemini": _gemini_api_key or os.environ.get("GEMINI_API_KEY", ""),
        "anthropic": os.environ.get("ANTHROPIC_AUTH_TOKEN", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai": os.environ.get("OPENAI_API_KEY", ""),
    }.get(query_format, "")
    api_key = body.get("api_key") or os.environ.get("SEARCH_QUERY_API_KEY", "") or provider_key

    if arxiv_id and not arxiv_ids:
        arxiv_ids = [arxiv_id]
    if not arxiv_ids:
        return web.json_response({"error": "Provide arxiv_id or arxiv_ids"}, status=400)
    if not api_key:
        return web.json_response(
            {"error": f"No API key configured for SEARCH_QUERY_API_FORMAT={query_format}"},
            status=400,
        )

    max_concurrent = body.get("max_concurrent", 5)
    papers = arxiv_ids if len(arxiv_ids) > 1 else arxiv_ids[0]
    log.info(f"query_paper: {len(arxiv_ids)} paper(s), query={query!r:.80}")
    client = _get_client("specter2")
    try:
        result = client.query_paper(papers, query, api_key=api_key, max_concurrent=max_concurrent)
    except Exception as e:
        return web.json_response({"error": f"query_paper failed: {e}"}, status=500)

    if isinstance(result, str):
        return web.json_response({"results": {arxiv_ids[0]: result}})
    return web.json_response({"results": result})


# backward-compat aliases
handle_summarize_paper = handle_query_paper
handle_ask_paper = handle_query_paper


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "clients_loaded": list(_clients.keys())})


app = web.Application()
app.router.add_get("/health", handle_health)
app.router.add_post("/search", handle_search)
app.router.add_post("/batch_search", handle_batch_search)
app.router.add_post("/find_related", handle_find_related)
app.router.add_post("/enrich", handle_enrich)
app.router.add_post("/citations", handle_citations)
app.router.add_post("/references", handle_references)
app.router.add_post("/get_paper", handle_get_paper)
app.router.add_post("/download_source", handle_download_source)
app.router.add_post("/read_file", handle_read_file)
app.router.add_post("/query_paper", handle_query_paper)
app.router.add_post("/summarize_paper", handle_summarize_paper)
app.router.add_post("/ask_paper", handle_ask_paper)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY"))
    parser.add_argument("--gemini-index-dir", default="/workspace/gemini_index")
    args = parser.parse_args()

    _gemini_api_key = args.gemini_api_key
    _gemini_index_dir = args.gemini_index_dir

    log.info(f"Starting search API on :{args.port}")
    log.info(f"  Gemini index: {_gemini_index_dir}")
    log.info(f"  Both embeddings available via 'embedding' field in request body")
    web.run_app(app, host=args.host, port=args.port, print=None)
