# AI Reviewer

The AI Reviewer is TheAppliedScientist's independent paper review system. It
reads the manuscript, checks its claims, searches related work through the
Search API, and returns detailed feedback and numeric scores. The review API is
the interface used by the AI Scientist and by direct callers.

## Run

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...  SEARCH_PUBLIC_URL=http://localhost:8081
python review_api.py --port 8082
```

## Endpoints

```
POST /review/start          {latex_content, title, abstract} -> {job_id, status}
GET  /review/status/{id}    -> {status, review_text, error, submitted_at, finished_at}
POST /review               same body, blocks until done (kept for direct callers)
GET  /health
```

`status` moves `pending` -> `running` -> `success` | `error` | `timeout`.
Server-side limit is 30 minutes per review.

```bash
JOB=$(curl -s -X POST http://localhost:8082/review/start -H 'Content-Type: application/json' \
  -d '{"latex_content":"...","title":"...","abstract":"..."}' | jq -r .job_id)
curl -s "http://localhost:8082/review/status/$JOB" | jq .
```

## How a review is produced

`review_api.py` builds an isolated task directory containing the paper, metadata,
and publication cutoff. `benchmark_pass_at_k.py` then runs the AI Reviewer with
the manuscript and Search tool. The final structured review and complete agent
trajectory are retained.

The reviewer is a full agent, not a single model call: it reads the LaTeX, runs several
literature searches, and checks claims against the paper text before scoring.

## Prompts

```
prompts/
  paper_reviewer_instruction_template.md   Reviewer instructions: procedure, output
                                           format, scoring scale, and the verification
                                           rules that require every numeric and
                                           comparative claim to be traced back to the
                                           paper text before it may be written
  llm_judge_instruction.md                 Judge: scores a review against human reviews
  judge_prompt_overlap.md                  Judge: overlap with human-identified weaknesses
  judge_prompt_fabrication.md              Judge: detects unsupported claims in a review
  judge_prompt_rest.md                     Judge: remaining review-quality dimensions
```

The judge prompts are used for evaluating review quality; they are not part of the
review path itself. Pass `--skip-judge` (the default for the API) to omit them.
