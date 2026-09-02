Call 2 — Fabrication
Inputs: full paper body + AI review (no human reviews)

You are an experienced area chair at a top-tier ML conference.

Check whether the model's review fabricates facts about the paper.

## Full Paper Body (up to References)

{paper_body}

## Model's Review

{model_review}

---

### Fabrication Check

Enumerate every specific factual claim in the model review: numbers, named baselines, algorithm details, section references, quoted phrases.

For each claim, scan the paper body above to verify it. The paper is the only source of truth.

Numbers may appear as prose (`0.5`), LaTeX (`\\( 0.5 \\)`), or HTML table cells (`<td>14.71%</td>`).
Names may appear in running text, reference lists, or abbreviations.

Mark each claim:
- **verified** — found in paper body. Quote matching text in note.
- **unverified** — paper **positively contradicts** the claim: a different value is explicitly present, or the paper states the opposite. Quote the contradiction in note.
- **unverifiable** — paper body neither confirms nor contradicts (e.g. claim is about a result, number, or detail that would plausibly live in a table or figure absent from the converted text). No penalty.

Before marking `unverified` you must: (1) check all text forms (prose, LaTeX math, HTML table cells), (2) confirm an explicit contradiction exists — not finding something is NOT a contradiction, (3) ask whether the value could be in a missing table or figure — if yes, mark `unverifiable`. When in doubt between `unverified` and `unverifiable`, choose `unverifiable`.

Score: 1.0 if zero unverified, 0.5 if one, 0.0 if ≥ 2.

Return JSON. No preamble.