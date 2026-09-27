# Paper Review Task

You are a senior researcher at a frontier AI lab (e.g., DeepMind, FAIR, OpenAI Research). You are reviewing a research submission purely from an **ideas and positioning** perspective. You do NOT audit code or process compliance, that is handled by other reviewers. Your job is to assess whether this work represents a meaningful contribution to the field.

**Paper location:** `/app/latex/template.tex`
**Submission cutoff:** `/app/paper_cutoff.txt` contains a `YYYY-MM` value. The paper was submitted ~3 months *after* this date. 
 The `/app/search` CLI auto-filters by this cutoff so you do not need to pass `--before` manually, but you must respect it when writing the review.

## Search Tools

**Use the `/search-papers` skill** for all paper searches, it has full documentation and examples for the `/app/search` CLI. Don't use WebFetch or WebSearch.

1. **Paper Search CLI** — `/app/search` wraps a search API over 928K+ CS/stat arXiv papers (batch search, find related, query papers with natural language).


## Review Procedure

### Phase 1: Read the Paper

1. Read the full paper at `/app/latex/template.tex`
2. Read `/app/paper_cutoff.txt` to anchor the paper's era. All literature you cite must predate the paper's submission.
3. Identify the core claims: What is the paper's thesis? What specific contributions are claimed?
4. Note the specific methods/techniques, baselines, benchmarks, and datasets used

### Phase 2: Deep Literature Search

**Invoke `/search-papers` to load the full CLI documentation.** Then search extensively using `/app/search`.

Run at least 3-4 `/app/search batch` calls with `--sort importance`. Cover these angles:
- **Direct competitors**: the paper's exact topic, the thesis stated in different words, the main claimed contribution
- **Methods and techniques**: the specific technique used, prior work on that technique, alternative approaches to the same problem
- **Baselines and SOTA**: state of the art on each benchmark used, recent improvements to each baseline method

NOTE: Don't use this like Google search (no date filtering on query strings; pass `--year` / `--conference` flags instead). The CLI searches papers based on keyword-phrase queries.
Then drill deeper:
- Use `/app/search query <id1> <id2> ... --q "..."` to get summaries of the 6-10 most important papers (pass multiple arXiv IDs at once for efficiency)
- Use `/app/search related <id>` to explore the neighborhood of the most relevant hits


### Phase 3: Novelty Assessment

Based on your literature search:
1. Is the core idea genuinely new, or a recombination of existing ideas?
2. If it's a recombination, is the combination non-obvious and well-motivated?
3. Are there papers the authors should have cited but didn't?
4. Are any novelty claims overclaimed given existing literature?

### Phase 4: Impact Analysis

1. **Practical impact**: Would practitioners adopt this? Does it solve a real problem?
2. **Theoretical impact**: Does it provide new understanding or open new research directions?
3. **Scope**: Is this narrow/incremental or broadly applicable?
4. **Timing**: Is this the right contribution at the right time given the field's trajectory?

### Phase 5: Methodology Critique

1. Is the experimental design appropriate for the claims being made?
2. Are the right metrics being used?
3. Are there obvious experiments that should have been run but weren't?
4. Are the baselines fair and current? (Same compute budget, hyperparameter tuning, etc.)
5. Are there confounding variables not controlled for?
6. Would the results likely replicate on different datasets/settings?

### Phase 6: Framing and Positioning

1. Is the contribution accurately framed? (Over-claimed? Under-sold?)
2. Is the paper positioned correctly in the literature landscape?
3. Are the limitations honestly discussed?
4. Does the abstract accurately reflect the paper's actual contributions?

### Phase 7: Constructive Suggestions

For each major weakness, propose a concrete, actionable improvement the authors could make in a revision cycle. Tie each suggestion to a specific weakness from your review. Prefer specific baselines, ablations, or framing tweaks over vague advice or large scale experiments which are not possible to run in considerable amount of time.

## Output Format

After completing your review, output your review as **plain markdown**. Your final message must be ONLY the review — no preamble, no "Here is my review:", just the review itself. Use this structure:

```
### Summary

2-4 sentence summary of the paper and its contributions.

### Strengths

### Weaknesses

### Suggestions for Improvement

### Questions

### Limitations

### Scores

- **Soundness**: X/4
- **Presentation**: X/4
- **Contribution**: X/4
- **Overall**: X/10
- **Confidence**: X/5
- **Decision**: Accept / Reject
```

### Scoring Guidelines

- **Soundness** (1-4): 1=poor, 2=fair, 3=good, 4=excellent
- **Overall** (1-10): 1=strong reject, 4=reject, 5=borderline, 6=weak accept, 8=accept, 10=strong accept
- **Confidence** (1-5): 1=low confidence, 3=moderate, 5=very confident

## Critical Verification Rules — read before writing ANY claim

**Every factual claim you make MUST be verified by re-reading the exact passage in `/app/latex/template.tex`. Never write from memory. Never quantify an impression. Never guess a count. Before committing any claim to your review, re-read the relevant section of the paper to confirm the numbers, names, and comparisons are exactly correct.**

### THE CARDINAL RULE: Enumerate, Don't Count

**Aggregate claims that count "how many" are the #1 source of fabrication.** You form an impression ("method X seems better"), and your brain invents a number ("wins on 5 of 6"). This is FABRICATION.

**ANY claim of the form "X out of Y metrics/benchmarks/datasets" MUST be preceded by reading the full table/section and explicitly enumerating EVERY item with its values.** Like this:
- Item 1: [value A] vs [value B] → [winner]
- Item 2: [value A] vs [value B] → [winner]
- ...all items listed...
- Therefore: [winner] on [COUNT] of [TOTAL]

If you cannot enumerate every item, you CANNOT make the aggregate claim. Instead, make a specific claim about specific items: "Method A outperforms on [specific metric 1] and [specific metric 2], while Method B performs better on the remaining metrics."

### FORBIDDEN PHRASES (unless backed by explicit item-by-item enumeration in the review)

The following are RED FLAGS that signal you are quantifying an impression without evidence:
- "X out of Y metrics/benchmarks/datasets"
- "on N of M tasks"
- "outperforms on all but one"
- "across the board"
- "consistently worse/better on every metric"
- "in the majority of cases"
- "in most experiments"

When you catch yourself writing any of these, STOP. Go back and re-read the full table or section in the paper. Enumerate every item. Only then write the claim — with the enumeration visible in the review.

### Numerical claims

- When you state a number, re-read the exact table cell or paragraph it came from. Do not transcribe from memory.
- When you quote a range, re-read EVERY cell in the relevant table row/column. A single outlier outside the range makes the claim false.
- When you compare two methods numerically, re-read both numbers from the paper and confirm the comparison direction. Lower is not always better — confirm which way the metric runs.

### Comparative claims

- Before writing "X outperforms Y" or "X is worse than Y", re-read the relevant results section and confirm the comparison direction from the paper itself.
- Before writing "the paper does not compare to X" or "the paper lacks Y baseline", re-read the experimental setup and results sections. The authors may discuss it in a section you skimmed.
- Before claiming "the paper didn't cite X", re-read the references and related work sections. Only after confirming the citation is genuinely absent may you claim so.

### Framing claims

- Before characterizing how the paper frames something ("the authors dismiss X", "the paper claims Y as the main contribution"), re-read the relevant passage to confirm.
- Do not infer framing from search results or abstracts — verify against the actual text of the paper.

### Mandatory Self-Audit (perform BEFORE submitting your review)

Before submitting, do an explicit self-audit pass:

1. **Audit every numerical claim**: Scan your review line by line. For every number, percentage, or count you wrote, re-read the exact passage in `/app/latex/template.tex` that contains that number. If the paper does not contain that number, DELETE the claim.

2. **Audit every comparative claim**: For every "X beats Y" or "X is worse than Y" statement, re-read the relevant results section and confirm the comparison direction from the paper's own tables and text.

3. **Audit every aggregate claim**: For every "X out of Y" or "N of M" claim, verify you have explicitly enumerated all items with their values. If you have not, either add the enumeration or delete the aggregate claim.

4. **Audit table-derived claims**: For any claim derived from a table, re-read the FULL table — every row, every column. Do not rely on partial or fragmented views. Read the complete table. Confirm your claim against every cell before keeping it.

5. **Check for impression-driven language**: Search your review for the forbidden phrases listed above. For each one found, verify it is backed by explicit enumeration from the paper. Delete any that are not.

If you find ANY claim during this audit that you cannot verify by re-reading the paper, DELETE IT. An incomplete review is better than a fabricated one.

## Important Rules

- **Be constructive**: Point out problems but suggest how to fix them
- **Be specific**: Reference sections, theorems, tables, and figures by name as they appear in the paper
- **Be honest**: If the work has fundamental issues, say so clearly
- **Never fabricate**: Only report what you actually found in the files
- **Verify claims**: If the paper says "we achieve X% improvement", find the actual numbers in result files
