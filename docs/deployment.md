# Deployment

## Recommended local deployment

Hybrid mode keeps Search simple and runs it in a local Python environment. The
AI Reviewer and AI Scientist use Docker, which keeps their pinned runtime and
experiment dependencies isolated.

```bash
./tas setup search reviewer scientist --runtime docker
./tas up
./tas smoke
./tas run components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600 --gpus 1
```

The Scientist is an on-demand Compose tool rather than a daemon. Its runner
mounts the host Docker socket so Harbor can create sibling experiment containers.
Only do this with repository code you trust; Docker socket access is equivalent
to host-level control of Docker.

Search remains a native process unless `--dockerize-search` is supplied.

## Independent component machines

```bash
./tas setup search --runtime native
./tas up search

# On machine B
./tas setup reviewer --runtime docker
./tas up reviewer

# On machine C
./tas setup scientist --runtime docker
./tas run components/ai-scientist/user-ideas/my-paper.json
```

Set `REVIEWER_SEARCH_API_URL` on machine B. Set `SCIENTIST_SEARCH_API_URL` and
`SCIENTIST_REVIEW_API_URL` on machine C. Point them at machines A and B. No
shared filesystem is required.

Equivalent low-level commands:

```bash
docker compose --profile search --profile reviewer build search-api ai-reviewer
docker compose --profile search --profile reviewer up -d search-api ai-reviewer
docker compose --profile scientist run --rm ai-scientist \
  user-ideas/my-paper.json --env docker --gpus 1
```

## Complete Dockerless path

Native mode runs the APIs in isolated Python virtual environments and invokes
the pinned Claude Code binaries directly in per-job workspaces. Everything runs
on the current machine; there is no remote sandbox service.

```bash
./tas setup search reviewer scientist --runtime native
./tas up
./tas smoke
./tas run --runtime native components/ai-scientist/user-ideas/my-paper.json
```

With all components on one machine, localhost URLs work. For split deployments,
use reachable private/public URLs and appropriate firewall/authentication rules.

## Why there are separate environments

The components use separate pinned runtimes:

- Reviewer: Claude Code 2.1.101 with the latest structured review prompt. It
  runs directly in its service container or in a private native job directory.
- Scientist: Harbor commit `7ed1ffc` for the faithful local-Docker path and
  Claude Code 2.1.145. Native mode preserves the prompt and workspace contract.

Merging these into one Python environment makes one of the components wrong.
The CLI therefore creates `.venvs/search`, `.venvs/reviewer`, and
`.venvs/scientist` independently.

## Production operation

`./tas up` uses detached processes for the portable native path. On a permanent
server, place the same commands behind systemd, supervisor, or your platform's
process manager. Persist these paths:

- `data/search-index/`: approximately 13.3 GB, expensive to redownload.
- Review trajectory volume/directory: audit trail for generated reviews.
- `components/ai-scientist/jobs/`: papers, code, logs, and version snapshots.

Never publish `.env` or raw trajectories without sanitization.
