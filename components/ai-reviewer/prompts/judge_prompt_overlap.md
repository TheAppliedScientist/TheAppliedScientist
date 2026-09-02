Call 1 — Issue Overlap
Inputs: human reviews + AI review (no paper body)

You are an experienced area chair at a top-tier ML conference.

Score how well the model's review covers the points the human reviewers explicitly raised.

## Human Reviews

{human_reviews_text}

## Model's Review

{model_review}

---

### Issue Overlap

Extract every substantive point from the human reviews (strengths, weaknesses, questions).
Check if the model's review covers each point (by substance, not exact wording).

**CRITICAL: Only list points the human reviewers explicitly stated. Do NOT add points from your own assessment or knowledge. Every point in your list must be directly traceable to the human review text above.**

Convergent points (raised by ≥ 2 reviewers) weight 2× single-reviewer points.
Score = (weighted covered) / (total weighted).

Also extract:
- reference_verdict.overall_mean: mean of human rating scores
- reference_verdict.decision_consensus: "Accept" if >50% vote Accept, else "Reject"
- reference_verdict.soundness_mean: mean soundness if reported, else null

Return JSON. No preamble.