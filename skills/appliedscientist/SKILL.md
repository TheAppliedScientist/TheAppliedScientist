---
name: appliedscientist
description: Use TheAppliedScientist's hosted Search and AI Reviewer MCPs when a user wants to find papers or request an independent manuscript review.
---

# Search and Review MCPs

Use [Search](https://search.eigenlabs.online/mcp) to find literature. Use
[Review](https://review.eigenlabs.online/mcp) to submit a paper for independent
feedback. The Reviewer searches on its own; do not prepare search results for it.

## Search

- `search_papers`: find papers on a topic.
- `batch_search_papers`: try several queries at once.
- `find_related_papers`: find work related to an arXiv ID.
- `query_papers`: ask a question about full papers by arXiv ID.

Use the results to answer the user's question, with links to the papers. Search
does not start a review.

## Review

1. Use the user's paper file: PDF, Word, HTML, EPUB, image, self-contained
   `.tex`, or a `.zip` of a complete LaTeX project. If TeX needs figures,
   bibliography, or other source files, use a zip.
2. Call `review_paper(filename)` on the Review MCP. `title` and `abstract` are
   optional. Send the file's **binary bytes** by HTTP `PUT` to the returned
   one-use `upload_url` within ten minutes. Do not send just the local path or
   paste the file into the MCP call.
3. Call `review_status(job_id)` until the review is ready, then show the
   feedback. A review can take several minutes plus queue time.

The agent handles the transfer; do not ask the user to run an upload command.
Files must be at most 20 MB. Only the first 12 rendered pages are reviewed.
