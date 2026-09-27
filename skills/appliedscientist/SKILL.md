---
name: appliedscientist
description: Use TheAppliedScientist's hosted Search and AI Reviewer MCPs when a user wants to find papers or request an independent manuscript review.
---

# Search and Review MCPs

Use [Search](https://search.eigenlabs.online/mcp) to find literature and
[Review](https://review.eigenlabs.online/mcp) to get an independent paper review.
The Reviewer performs its own searches. For a review request, submit directly
to Review; use Search only for a separate literature question.

## Search

For paper discovery, **prefer `batch_search_papers`** with 2 to 5 distinct
queries from different angles. It merges and deduplicates results. Do not
repeat the same query with minor wording changes.

| Tool | Inputs | Use |
| --- | --- | --- |
| `batch_search_papers` | `queries`: 1 to 5 search strings; `max_results`: 1 to 20 per query, default 10; optional `date_to`: `YYYY-MM-DD` | Recommended for finding papers on a topic. |
| `search_papers` | `query`: one search string; `max_results`: 1 to 20, default 10; optional `date_to`: `YYYY-MM-DD` | A focused single-query lookup. |
| `find_related_papers` | `arxiv_id`: one arXiv ID; `max_results`: 1 to 20, default 10 | Find papers similar to a known paper. |
| `query_papers` | `arxiv_ids`: 1 to 3 arXiv IDs; `query`: a question about their full text | Read selected papers to answer a specific question. |

`date_to` excludes papers published after that date. Give the user paper titles
and links, and distinguish search results from claims checked in full text.

## Review

1. Find the user's manuscript file. PDF, Word, HTML, EPUB, and image files work.
   Use a `.zip` for LaTeX projects with figures, bibliography, or other files;
   a lone `.tex` must be self-contained.
2. Call `review_paper` with the file name, including its extension. `title` and
   `abstract` are optional. The tool returns a `job_id` and an `upload_url`.
   If it returns an error, report it and stop.
3. Upload the actual local file to `upload_url` with an HTTP `PUT` within ten
   minutes. Use your own file/network tools. The MCP call does not read the
   local file, so **you handle this transfer**, not the user.
4. Once the upload succeeds, call `review_status(job_id)` about every 20
   seconds until `success`, `error`, or `timeout`. Show the review or error.

Keep the job ID and upload URL private. Files must be at most 20 MB. Only the
first 12 rendered pages are reviewed.
