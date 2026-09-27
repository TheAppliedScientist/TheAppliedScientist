# Search and Review with MCP

[Connect both MCPs and install the skill](../README.md#use-search-and-review-with-mcp)
from your paper's folder. The skill tells your agent how to use each tool.

## Use Search

Ask your agent: “Use the AppliedScientist skill to find papers on [topic] from
several search angles.” The skill recommends `batch_search_papers` with 2 to 5
distinct queries. Search does not start a review.

## Review a paper

Ask your agent: “Use the AppliedScientist skill to review `papers/draft.pdf`.”
Replace the path with your own file:

| Your paper | What to give the agent |
| --- | --- |
| PDF, Word, HTML, EPUB, or image | The file path, such as `draft.pdf` or `draft.docx`. |
| Self-contained TeX | The `.tex` file path, such as `main.tex`. |
| LaTeX with `\input`, bibliography, styles, or figures | A `.zip` containing the full source project, such as `paper-source.zip`. |

Your agent uploads the file to our VPS and checks the review job. You do not
run an upload command. A `.zip` or `.tex` is compiled on the VPS. The Reviewer
searches related work itself, so you do not need to search before submitting.

The file limit is 20 MB. The Reviewer sees at most the first 12 rendered
pages. PDFs are cut before OCR; TeX/zip projects are compiled in isolation and
cut before review. A review usually takes several minutes, plus queue time.
