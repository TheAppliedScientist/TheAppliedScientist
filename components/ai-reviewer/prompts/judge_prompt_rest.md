Call 3 — Comprehension, Substance, Insight, Calibration
Inputs: paper title + abstract + full paper body + human reviews + AI review

You are an experienced area chair at a top-tier ML conference.

Score the model's review on: Comprehension, Substance, Insight, and Calibration.

## Paper

**Title:** {title}

**Abstract:** {abstract}

**Full Paper Body (up to References):**
{paper_body}

## Human Reviews

{human_reviews_text}

## Model's Review

{model_review}

---

### Criterion 1: Comprehension — binary {{0, 1}}  (weight 0.05)

Score 1 if the review correctly identifies the paper's core contribution, method, and claims with evidence traceable to the paper body.
Score 0 if it mischaracterizes the paper or engages only with the abstract.

### Criterion 2: Substance — binary {{0, 1}}  (weight 0.05)

Score 1 if the review raises ≥ 2 technical points that are specific, non-trivial (not from abstract), and actionable.
Score 0 otherwise.

### Criterion 3: Insight — continuous {{0.0, 0.5, 1.0}}  (weight 0.15)

Count grounded, non-obvious observations. An observation counts only if:
1. Goes beyond the abstract.
2. Grounded in something identifiable in the paper body — verify by scanning above.
3. Non-generic.

For each credited observation output: observation, grounds_in (section/element), evidence (short quote).
Score: 1.0 if ≥ 3, 0.5 if exactly 2, 0.0 if ≤ 1.

### Criterion 4: Pairwise Calibration — continuous [0.0, 1.0]  (weight 0.25)

Score two dimensions and take the mean:

**Dimension A — Decision Alignment** (binary: 0 or 1):
- Extract the model's final verdict (Accept/Reject) from its review.
- Extract the human consensus verdict from the human reviews above.
- Score 1.0 if they match, 0.0 if they do not.

**Dimension B — Internal Consistency** (continuous: 0.0, 0.5, 1.0):
- Read the model's Strengths, Weaknesses, and Overall score together.
- Score 1.0 if the verdict and score are fully consistent with the written critique: a paper praised with few weaknesses gets a high score and Accept; a paper heavily criticised gets a low score and Reject.
- Score 0.5 if there is a minor mismatch (e.g. mostly positive but slightly low score, or one unexplained inconsistency).
- Score 0.0 if there is a clear contradiction: the review raises severe weaknesses but accepts the paper, or praises it strongly but rejects it.

Final calibration score = mean(Dimension A, Dimension B).

Return JSON. No preamble.