---
name: appliedscientist
description: Improve a research paper through the AppliedScientist loop: literature search, real experiments, manuscript revision, independent AI Reviewer feedback, and another revision. Use when the author asks to run the full revision workflow on a paper with code. For a one-off search or review, call the MCP tools directly.
---

# AppliedScientist

Use the connected `TheAppliedScientist` MCP server. The server provides `search_papers`,
`batch_search_papers`, `find_related_papers`, `query_papers`, `start_review`, and
`review_status`. The hosted Reviewer uses the project's original review prompt;
these instructions guide the research agent that acts on its feedback.

This workflow follows the research process and experimental rules in the project's
[`CLAUDE.md`](https://github.com/TheAppliedScientist/TheAppliedScientist/blob/main/components/ai-scientist/.claude/CLAUDE.md).
Run experiments in the user's own repository and environment. Ask before using
substantial compute, paid services other than the hosted tools, or publishing work.

## Read the work

Read the complete manuscript, its code, public reviews if available, and any
instructions from the author. Identify the paper's claims, datasets, metrics,
baselines, and gaps. Keep the author's stated scope and constraints.

Search the literature with `batch_search_papers` using several distinct queries.
Open and read the most relevant full papers with `query_papers` or their source.
Use `find_related_papers` around close competitors. Set `date_to` when assessing
what was known at a submission date. Cite only work that actually supports a
claim. Search again when new results or reviewer objections make it necessary.

## Run experiments before submitting

Reproduce at least one baseline and run at least one new experiment that addresses
a concrete weakness. Use the paper's code where possible. Keep commands, settings,
seeds, raw outputs, and failures in an experiment log. Numbers in the revision
must come from runs you checked; label original-paper numbers as such. Inspect
plots and the compiled manuscript before submitting.

If an environment or dataset fails, try a direct fix and a feasible alternative.
Do not spend the whole run on one setup issue. Scale the work to the available
time and hardware, and report limitations honestly.

## Request independent review

Pass the **full current LaTeX source**, title, and abstract to `start_review`.
The tool returns a job ID immediately. Poll `review_status(job_id)` until it
returns `success`, `error`, or `timeout`; a review commonly takes several
minutes. Keep a frozen copy of the submitted version and the returned review.
Do not replace the hosted review with a self-review or a generic prompt.

## Revise and repeat

Check every reviewer concern against the manuscript, literature, and actual
experiment outputs. Address consequential weaknesses with new baselines,
ablations, metrics, datasets, analysis, or necessary structural changes. Keep a
response explaining each change and any concern left unresolved. Every later
submission should contain meaningful progress; do not resubmit for wording
changes alone. Recompile, inspect, and submit the new version for another
independent review when the author wants another round.

The author decides when to stop. Return the revised paper, runnable code,
experiment log, figures, reviewer feedback, and a clear account of what changed.
