---
name: appliedscientist
description: Use TheAppliedScientist's hosted Search and AI Reviewer MCPs when a user wants to find papers or request an independent manuscript review.
---

# Search and Review MCPs

Use [Search](https://search.eigenlabs.online/mcp) to find literature and
[Review](https://review.eigenlabs.online/mcp) to get an independent paper review.
The Reviewer performs its own searches. A search request does not start a review.

## Search

For paper discovery, **prefer `batch_search_papers`** with 2 to 5 queries from
different angles, such as the problem, method, and benchmark. It merges and
deduplicates results. Do not repeat the same query with minor wording changes.

| Tool | Inputs | Use |
| --- | --- | --- |
| `batch_search_papers` | `queries`: 1 to 5 search strings; `max_results`: 1 to 20 per query, default 10; optional `date_to`: `YYYY-MM-DD` | Recommended for finding papers on a topic. |
| `search_papers` | `query`: one search string; `max_results`: 1 to 20, default 10; optional `date_to`: `YYYY-MM-DD` | A focused single-query lookup. |
| `find_related_papers` | `arxiv_id`: one arXiv ID; `max_results`: 1 to 20, default 10 | Find papers similar to a known paper. |
| `query_papers` | `arxiv_ids`: 1 to 3 arXiv IDs; `query`: a question about their full text | Read selected papers to answer a specific question. |

`date_to` excludes papers published after that date. Give the user paper titles
and links, and distinguish search results from claims checked in full text.

## Review

1. Find the user's file. PDF, Word, HTML, EPUB, and image files work. A lone
   `.tex` must be self-contained; if it uses figures, bibliography, styles, or
   other TeX files, use a `.zip` of the complete source project.
2. Call `review_paper(filename)` on the Review MCP. `filename` is the file's
   name with its extension. For local `papers/draft.pdf`, pass `draft.pdf`.
   `title` and `abstract` are optional paper metadata. Leave them out if
   unknown; they never replace uploading the complete file.
   The tool returns a `job_id` and an `upload_url`. If it returns an error,
   report that error instead. This call only reserves a review; it has **not
   uploaded the file yet**.
3. Send the local file to that URL within ten minutes, using the agent's own
   network or shell access. For example, replace the URL below with the exact
   `upload_url` from step 2:

   ```bash
   curl -fS -X PUT --data-binary @papers/draft.pdf '<upload_url from review_paper>'
   ```

   `@papers/draft.pdf` sends the file's contents, not its name. Use the actual path
   for a zip or TeX file. The agent runs this command, not the user.
4. After the upload succeeds, call `review_status(job_id)` about every 20
   seconds until `success`, `error`, or `timeout`. On success, show the returned
   feedback; on failure, show the error. A review takes minutes, plus queue time.

Keep the job ID and upload URL private. Files must be at most 20 MB. Only the
first 12 rendered pages are reviewed.
