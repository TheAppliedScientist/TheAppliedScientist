#!/usr/bin/env python3
"""CLI wrapper for the paper Search API.

Three subcommands that map 1:1 to the real endpoints. Reads the API URL
from /app/search_api_url.txt so the agent never needs to type URLs,
endpoint names, or JSON payload fields by hand.

Usage:
    search batch "query 1" "query 2" [--max 6] [--sort importance] [--categories cs.LG cs.CV] [--year 2024]
    search related <arxiv_id> [--max 6]
    search query <arxiv_id> [<arxiv_id> ...] --q "what are the key contributions?"

Output is raw JSON from the API on stdout. On non-2xx it prints the
server body and exits 1.
"""
import argparse
import concurrent.futures
import datetime
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path


API_URL_FILE = Path("/app/search_api_url.txt")
CUTOFF_FILE = Path("/app/paper_cutoff.txt")

_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def _api_url() -> str:
    if not API_URL_FILE.is_file():
        print(f"error: {API_URL_FILE} not found", file=sys.stderr)
        sys.exit(2)
    return API_URL_FILE.read_text().strip().rstrip("/")


def _default_cutoff_month() -> str | None:
    """Return YYYY-MM cutoff baked into the sandbox at task setup, or None.

    The benchmark writes /app/paper_cutoff.txt = (paper_submission_month - 3mo)
    so that even if the agent forgets --before, the CLI auto-applies it.
    """
    if not CUTOFF_FILE.is_file():
        return None
    txt = CUTOFF_FILE.read_text().strip()
    return txt if _MONTH_RE.match(txt) else None


def _month_to_date_to(month_str: str) -> str:
    """Convert YYYY-MM to YYYY-MM-DD = last day of that month.

    `date_to` in the search API is inclusive, so passing the last day of the
    month gives the agent everything published *up to and including* that month.
    """
    m = _MONTH_RE.match(month_str)
    if not m:
        raise ValueError(f"--before must be YYYY-MM, got {month_str!r}")
    year, mo = int(m.group(1)), int(m.group(2))
    if not (1900 <= year <= 2100 and 1 <= mo <= 12):
        raise ValueError(f"--before out of range: {month_str!r}")
    if mo == 12:
        last = datetime.date(year, 12, 31)
    else:
        last = datetime.date(year, mo + 1, 1) - datetime.timedelta(days=1)
    return last.isoformat()


def _post(path: str, payload: dict) -> dict:
    url = _api_url() + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; HarborSearchCLI/1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from {path}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"request to {path} failed: {e}", file=sys.stderr)
        sys.exit(1)


def _resolve_before(arg_before: str | None) -> tuple[str | None, str | None]:
    """Resolve the effective --before cutoff: explicit > sandbox default > none.

    Returns (date_to_iso, source_label). `date_to_iso` is the YYYY-MM-DD string
    to send to the API (or None to skip). `source_label` is "explicit",
    "default", or None — used by the CLI to print a one-line note on stderr so
    the agent sees that a cutoff was applied.
    """
    chosen = arg_before
    label = "explicit" if arg_before else None
    if chosen is None:
        chosen = _default_cutoff_month()
        label = "default" if chosen else None
    if chosen is None:
        return None, None
    return _month_to_date_to(chosen), label


def cmd_batch(args: argparse.Namespace) -> None:
    queries = list(args.queries) + list(args.queries_flag) + list(args.query_repeat)
    if not queries:
        print("error: at least one query is required (positional, --queries, or --query)", file=sys.stderr)
        sys.exit(2)
    payload: dict = {
        "queries": queries,
        "max_results": args.max,
        "sort_by": args.sort,
    }
    if args.categories:
        payload["categories"] = args.categories
    if args.year:
        payload["year"] = args.year
    if args.conference:
        payload["conference"] = args.conference
    if args.min_citations is not None:
        payload["min_citations"] = args.min_citations
    date_to, source = _resolve_before(args.before)
    if date_to:
        payload["date_to"] = date_to
        print(f"[search] applying --before {args.before or _default_cutoff_month()} "
              f"(date_to={date_to}, source={source})", file=sys.stderr)
    print(json.dumps(_post("/batch_search", payload), indent=2))


def cmd_related(args: argparse.Namespace) -> None:
    payload: dict = {"arxiv_id": args.arxiv_id, "max_results": args.max}
    date_to, source = _resolve_before(args.before)
    if date_to:
        payload["date_to"] = date_to
        print(f"[search] applying --before {args.before or _default_cutoff_month()} "
              f"(date_to={date_to}, source={source})", file=sys.stderr)
    print(json.dumps(_post("/find_related", payload), indent=2))


def cmd_query(args: argparse.Namespace) -> None:
    # Mode 1: per-paper questions via --pair ID QUESTION ...
    if args.pair:
        pairs = [(aid, q) for aid, q in args.pair]
        # Fan out to /query_paper in parallel, one call per pair.
        def _one(aid: str, q: str) -> tuple[str, object]:
            resp = _post("/query_paper", {"arxiv_id": aid, "query": q})
            # Server wraps single-paper answers in {"results": {id: text}}.
            if isinstance(resp, dict) and "results" in resp:
                return aid, resp["results"].get(aid, resp["results"])
            return aid, resp
        merged: dict[str, object] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pairs))) as ex:
            for aid, ans in ex.map(lambda p: _one(*p), pairs):
                merged[aid] = ans
        print(json.dumps({"results": merged}, indent=2))
        return

    # Mode 2: same question for one or many papers.
    # Forgiveness: model sometimes puts --q BEFORE the ids, so argparse sucks the
    # ids into args.q. Rescue them: split args.q into leading arxiv-id-shaped tokens
    # vs the remaining question text.
    import re as _re
    _arxiv_pat = _re.compile(r'^[\w.\-/]+$')
    if args.q:
        # Model sometimes puts --q BEFORE the ids, so argparse sucks the ids into
        # args.q after the question text. Rescue trailing arxiv-id-shaped tokens.
        q_tokens = list(args.q)
        rescued_ids = []
        while q_tokens and _arxiv_pat.match(q_tokens[-1]) and not q_tokens[-1].startswith('-'):
            rescued_ids.insert(0, q_tokens.pop())
        if rescued_ids:
            args.arxiv_ids = list(args.arxiv_ids) + rescued_ids
            args.q = q_tokens if q_tokens else None

    if not args.arxiv_ids:
        print("error: provide arxiv_ids + --q, OR --pair ID QUESTION ...", file=sys.stderr)
        sys.exit(2)
    if not args.q:
        # Forgiveness: model passed the question as a positional arg instead of --q.
        question_guess = None
        ids_only = []
        for tok in args.arxiv_ids:
            if " " in tok or not _arxiv_pat.match(tok):
                question_guess = tok
            else:
                ids_only.append(tok)
        if question_guess and ids_only:
            args.arxiv_ids = ids_only
            args.q = [question_guess]
        else:
            print("error: --q is required when using positional arxiv_ids", file=sys.stderr)
            sys.exit(2)
    # --q is nargs="+", so args.q is a list of tokens — join them.
    q_text = " ".join(args.q) if isinstance(args.q, list) else args.q
    payload: dict = {"query": q_text}
    if len(args.arxiv_ids) == 1:
        payload["arxiv_id"] = args.arxiv_ids[0]
    else:
        payload["arxiv_ids"] = args.arxiv_ids
    print(json.dumps(_post("/query_paper", payload), indent=2))


def main() -> None:
    p = argparse.ArgumentParser(prog="search", description="Paper Search API CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("batch", help="Find papers on a topic (multi-query)")
    # Positional queries are the documented form, but models often try --queries
    # or --query as a flag. Accept both so the CLI is forgiving.
    b.add_argument("queries", nargs="*", default=[], help="One or more keyword queries (positional)")
    b.add_argument("--queries", dest="queries_flag", nargs="+", default=[],
                   help="Alias for positional queries")
    b.add_argument("--query", "-q", dest="query_repeat", action="append", default=[],
                   help="Single query; may be passed multiple times")
    b.add_argument("--max", type=int, default=6, help="Results per query (default 6)")
    b.add_argument("--sort", default="importance", choices=["importance", "recency", "relevance"])
    b.add_argument("--categories", nargs="*", help="e.g. cs.LG cs.CV")
    b.add_argument("--year", type=int)
    b.add_argument("--conference")
    b.add_argument("--min-citations", type=int, dest="min_citations")
    b.add_argument("--before", metavar="YYYY-MM",
                   help="Cap results to papers published on/before the last day of this month. "
                        "If omitted, falls back to the sandbox default at /app/paper_cutoff.txt "
                        "(set to the paper-under-review's submission month minus 3 months). "
                        "Citing later work as 'missing' is fabrication.")
    b.set_defaults(fn=cmd_batch)

    r = sub.add_parser("related", help="Find papers related to an arxiv id")
    r.add_argument("arxiv_id")
    r.add_argument("--max", type=int, default=6)
    r.add_argument("--before", metavar="YYYY-MM",
                   help="Cap results to papers published on/before the last day of this month. "
                        "Defaults to /app/paper_cutoff.txt if present.")
    r.set_defaults(fn=cmd_related)

    q = sub.add_parser("query", help="Ask a question about one or more papers")
    # Same-question mode: positional ids + --q
    q.add_argument("arxiv_ids", nargs="*", default=[],
                   help="One or more arxiv ids (used with --q for a shared question)")
    # --q: greedy so unquoted multi-word questions work.
    q.add_argument("--q", nargs="+", default=None,
                   help="Question applied to all arxiv_ids. Must come last; consumes remaining tokens. "
                        "Quoting is optional.")
    # Per-paper-question mode: repeatable --pair ID QUESTION. QUESTION must be quoted.
    q.add_argument("--pair", nargs=2, action="append", default=[],
                   metavar=("ARXIV_ID", "QUESTION"),
                   help="Ask a different question per paper. Repeat for each pair. "
                        'QUESTION MUST be quoted: --pair 1706.03762 "what is attention" '
                        '--pair 2010.11929 "how do patches work"')
    q.set_defaults(fn=cmd_query)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()