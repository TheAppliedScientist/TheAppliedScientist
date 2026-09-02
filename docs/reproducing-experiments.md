# Matching the original run behavior

The current setup keeps the prompts, tools, and review loop used in the original
runs while making installation and deployment simpler.

## Settings that affect behavior

- Scientist: Claude Code 2.1.145 with the pinned Harbor commit.
- Reviewer: the locked structured review prompt, Claude Code 2.1.101, and high reasoning effort.
- Review flow: asynchronous `/review/start` requests with status polling.
- Search: Gemini Embedding 2, LanceDB, BM25, reciprocal-rank fusion, deduplication, and the published `Vidushee/arxiv-gemini-index` dataset.
- Literature cutoff: only papers available at least three months before the reviewed paper's submission date.
- Scientist state: code, drafts, experiments, and feedback carry into each revision. Each reviewer sees only the current manuscript.

Provider URLs, credentials, process supervision, and packaging are configuration.
The behavior-critical prompts, instructions, and review script are checked
against `experiment-compatibility/locked-files.json` in the test suite.

## Run a paper task

After the services are configured, run the paper JSON described in the README:

```bash
./tas run components/ai-scientist/user-ideas/my-paper.json -- \
  --timeout 32400 \
  --gpus 1
```

A complete multi-review run is expensive and can take hours. Smoke tests check
the service contracts and launch path. They cannot guarantee identical numbers
because models, external repositories, datasets, and package registries change.
