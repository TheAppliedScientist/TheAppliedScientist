# Hosted Search and AI Reviewer

Connect one or both MCPs to your coding agent. No clone or API key is needed.

| MCP | URL | Tools |
| --- | --- | --- |
| Search | `https://search.eigenlabs.online/mcp` | `search_papers`, `batch_search_papers`, `find_related_papers`, `query_papers` |
| AI Reviewer | `https://review.eigenlabs.online/mcp` | `review_paper`, `review_status` |

The Reviewer calls Search itself. Connect Search separately only when you want
your agent to explore the literature outside a review.

## Connect Claude Code

```bash
claude mcp add --transport http appliedscientist-search https://search.eigenlabs.online/mcp
claude mcp add --transport http appliedscientist-review https://review.eigenlabs.online/mcp
```

## Connect Codex

```bash
codex mcp add appliedscientist-search --url https://search.eigenlabs.online/mcp
codex mcp add appliedscientist-review --url https://review.eigenlabs.online/mcp
```

## Use Search

Ask your agent: “Find recent papers on [topic] using several search angles and
compare their methods.” For discovery, prefer `batch_search_papers` with 2 to
5 distinct queries. Use `find_related_papers` for a known arXiv paper and
`query_papers` to ask a question about selected full papers. Search does not
start a review. The [MCP skill](../skills/appliedscientist/SKILL.md) lists each
tool's inputs.

## Review a paper

Ask your agent: “Review `paper.pdf` with the AppliedScientist Reviewer. Send
the file to the URL returned by `review_paper`, then check `review_status` and
show me the feedback.” Use the same request with another file:

| Your paper | What to give the agent |
| --- | --- |
| PDF, Word, HTML, EPUB, or image | The file path, such as `draft.pdf` or `draft.docx`. |
| Self-contained TeX | The `.tex` file path, such as `main.tex`. |
| LaTeX with `\input`, bibliography, styles, or figures | A `.zip` containing the full source project, such as `paper-source.zip`. |

The agent calls `review_paper` with the file name. This reserves a job but does
not upload the file. The tool returns a private job ID and one-use HTTPS URL.
Your agent sends the file to that URL within ten minutes, then checks
`review_status` until feedback is ready. You do not run an upload command. The
file path stays on your machine; your agent transfers the file to our VPS. No
extra local MCP process runs. A zip is unpacked and compiled on our VPS. A
single `.tex` is compiled there too.

`title` and `abstract` are optional tool fields. Leave them out: the Reviewer
reads the paper. Supply them only if you want to override the inferred title
or add abstract metadata.

The file limit is 20 MB. The Reviewer sees at most the first 12 rendered
pages. PDFs are cut before OCR; TeX/zip projects are compiled in isolation and
cut before review. The Reviewer searches related work itself. A review usually
takes several minutes, plus queue time.

## Direct APIs and privacy

Scripts can use the [Search API](https://search.eigenlabs.online/docs) or
[Reviewer API](https://review.eigenlabs.online/docs). The Review MCP's one-use
URL accepts a raw binary `PUT`; the Reviewer API also accepts multipart files
at `POST /api/reviews`. Both enter the same queue and run the same
Reviewer.

One review per IP may be queued or running, with three submissions per IP per
day. Anyone holding a job ID can read that job's feedback; there is no public
job list. Results expire after seven days. Paper files sent for review are
processed by our VPS, Datalab for rendered documents, and the review model
provider. Do not submit confidential work to a public service.
