# AI Scientist

The AI Scientist is the research component inside TheAppliedScientist. It receives a paper task JSON,
creates an isolated workspace, searches the literature, runs experiments,
writes a LaTeX paper, and iterates through the Review API.

Use the repository CLI from the project root:

```bash
./tas validate components/ai-scientist/user-ideas/my-study.json
./tas run components/ai-scientist/user-ideas/my-study.json -- --timeout 21600 --gpus 1
```

For a host-only run:

```bash
./tas run --runtime native components/ai-scientist/user-ideas/my-study.json -- --timeout 21600
```

See the main [README](../../README.md) for setup and component deployment, and
[Paper task files](../../docs/paper-task-files.md) for the input format.

## Outputs

Each run creates `jobs/<idea>__<timestamp>/`. Its `agent/artifacts/` directory
contains the files copied out of the research workspace:

```text
experiment_codebase/   code, commands, and experimental results
figures/               paper figures
latex/                 working LaTeX tree
literature/            papers and notes used during the run
submissions/           frozen paper versions and reviewer feedback
paper.tex              latest paper source
paper.pdf              latest compiled paper
```

The agent event stream is stored in `agent/session.jsonl`.

## Runtime notes

- Docker is the default and the closest path to the reference runs. Harbor launches
  the inner experiment container and syncs artifacts during the run.
- Native mode uses the same prompt and workspace layout but executes commands on
  the host. It requires the pinned Claude Code binary installed by `tas setup`.
- `REVIEWER_MODE=api-external` is the compatibility path. It calls the configured
  `REVIEW_API_URL`; it does not use a hosted execution service.
- `--gpus N` applies to Docker. Native jobs use the host devices directly.
- Use `--resume-from jobs/<job>` to continue from a previous artifact set.

## Behavior-critical files

```text
.claude/CLAUDE.md                 research process and quality rules
harbor-task/instruction.md.template  per-run instruction wrapper
scripts/submit_for_review.sh      review loop and version snapshots
paper_template/                   initial LaTeX workspace
local_harbor_agents/              Harbor integration and artifact syncing
```

These files are covered by compatibility tests. Keep them unchanged when
comparing a new run with the original setup.
