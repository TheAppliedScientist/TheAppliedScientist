---
name: appliedscientist
description: "Improve a research paper through the AppliedScientist loop: literature search, real experiments, manuscript revision, independent AI Reviewer feedback, and another revision. Use when the author asks to run the full revision workflow on a paper with code. For a one-off search or review, use the hosted tools directly."
---

# AppliedScientist

Use the [Search MCP](https://search.eigenlabs.online/mcp) for your own
literature work and the [Review MCP](https://review.eigenlabs.online/mcp) for
independent feedback. The hosted Reviewer runs its own searches and uses the
project's review prompt. These instructions guide the agent that acts on its
feedback.

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

Choose the current PDF or other complete manuscript file. A self-contained
`.tex` works; if it includes other TeX files, bibliography files, or figures,
zip the whole project. Do not send a path alone or paste base64 into the MCP.
Call the Review MCP's `review_paper` with the file name, send the exact
file bytes by HTTP `PUT` to its one-use `upload_url` within ten minutes, then
call `review_status(job_id)` until `success`, `error`, or `timeout`. Do the
transfer yourself using the agent's existing file/network tools; never ask the
author to run an upload command. The server reviews only the first 12 rendered
pages, including for TeX/zip. Keep a frozen copy of the submitted file and
feedback. Do not search on the Reviewer's behalf or replace its review with a
generic prompt. A review commonly takes several minutes plus queue time.

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
