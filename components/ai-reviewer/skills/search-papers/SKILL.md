---
name: search-papers
description: Search 928K+ CS/stat arXiv papers via the local `search` CLI. Find related work, verify novelty, check baselines, query papers.
argument-hint: "[query or topic]"
---

# MANDATORY: Use the `search` CLI — do NOT use curl, WebSearch, or WebFetch

You have a local CLI at `/app/search` that wraps the Paper Search API. It is the ONLY way to search academic papers. Do NOT call the API directly with curl. Do NOT use WebSearch or WebFetch — they are prohibited for this task.

The CLI has exactly **three subcommands**. Use them verbatim.

---

## Temporal rule — CRITICAL

The paper you are reviewing was submitted on a specific date. **Citing later work as "missing" is fabrication.** It is the single biggest source of bad reviews from this pipeline.

The CLI auto-applies a temporal cutoff:

- The file `/app/paper_cutoff.txt` contains a `YYYY-MM` value = (submission month − 3 months). Read it once at the start of your review so you know what era the paper lives in.
- `search batch` and `search related` automatically pass `date_to=<last day of that month>` to the API, so results never include papers published after the cutoff. You will see a `[search] applying --before YYYY-MM` line on stderr confirming this.
- You may override with `--before YYYY-MM` to widen or narrow the window. **Never set `--before` to a date AFTER the paper's submission month.**
- When you write the review, do not flag missing citations to papers published after `/app/paper_cutoff.txt + 3 months` — they did not exist when this paper was written.

---

## 1. `search batch "query 1" "query 2" ...` — Find papers on a topic

Takes multiple keyword queries (positional args), deduplicates, ranks by citations + venue prestige.

**Write it as a single line. Each query is a quoted positional arg — there is NO `--queries` flag and NO backslash line continuation.**

```bash
/app/search batch "core topic of the paper" "specific method or technique" "problem domain or application" --max 6 --sort importance
```

Optional flags: `--categories cs.LG cs.CV`, `--year 2024`, `--conference NeurIPS`, `--min-citations 50`.

**WRONG (do not do this):**

```bash
# Wrong: --queries doesn't exist, and backslash-split lines get interpreted as separate commands
/app/search batch --queries \
  "query 1" \
  "query 2"
```

Output: JSON `{"papers": [{"arxiv_id": "...", "title": "...", "abstract": "...", "citation_count": N}, ...]}`.

---

## 2. `search related <arxiv_id>` — Find papers related to a specific paper

```bash
/app/search related 1706.03762 --max 6
```

---

## 3. `search query` — Ask questions about one or more papers

Downloads each paper(s) and answers via LLM.

### Mode A: same question for one or many papers

Positional arxiv ids + `--q`. **`--q` MUST come last.** It greedily consumes everything after it, so quoting the question is optional.

```bash
# Single paper (quoting optional)
/app/search query 1706.03762 --q summarize this paper

# Multiple papers, same question (preferred for efficiency when the question applies uniformly)
/app/search query 1706.03762 2010.11929 --q what are the key contributions
```

### Mode B: different question per paper

Use repeated `--pair ARXIV_ID "QUESTION"`. The per-pair question **MUST be quoted** (unlike `--q`). Runs in parallel, returns one merged JSON.

```bash
/app/search query \
  --pair 1706.03762 "what is the attention mechanism and why does it work" \
  --pair 2010.11929 "how are image patches tokenized" \
  --pair 1512.03385 "what is the residual connection insight"
```

Output shape is identical in both modes: `{"results": {"<arxiv_id>": "<answer>", ...}}`.

Example questions: `summarize this paper`, `what datasets were used`, `explain the loss function`, `what are the ablation results`.

---

## Workflow for reviewing a paper

1. **Identify 5–8 angles** from the paper you are reviewing: core claim, method family, closest baseline, problem domain, application.
2. **Run `search batch`** with those 5–8 queries in a single call. Skim the top results.
3. **Run `search related <id>`** on the 2–3 most-relevant hits to explore their neighborhood.
4. **Run `search query <id1> <id2> ... --q "..."`** on the ~5 most important papers to get summaries / specific answers.

---

## Rules (read before first call)

- **Always** use `/app/search`. Never run `curl`, `wget`, or hit the API host directly.
- **Never** use WebSearch or WebFetch.
- Use keyword phrases, not full sentences: `"vision transformer pruning"`, not `"how to prune vision transformers"`.
- Prefer one `search batch` with 5–8 queries over many separate calls.
- Prefer one `search query <id1> <id2> <id3> --q "..."` over separate per-paper calls.

---

## Quick reference

| Goal | Command |
|---|---|
| Find papers on a topic | `/app/search batch "q1" "q2" "q3" --max 6` |
| Find papers related to id X | `/app/search related X --max 6` |
| Same question, one or many papers | `/app/search query X [Y Z...] --q <question>` |
| Different question per paper | `/app/search query --pair X "q1" --pair Y "q2"` |