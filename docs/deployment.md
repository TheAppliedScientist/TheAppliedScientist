# Component deployment

[Back to the README](../README.md) · [Prerequisites](installation.md) · [Keys and models](llm-providers.md)

Use the [full setup](../README.md#run-the-complete-system-yourself) when all three components run on one machine. This guide covers smaller installations and separate machines.

## Individual components

Clone the repository and install the [prerequisites for your selected component](installation.md). Run commands from the repository root. Setup saves the selected components and runtime in `.env`.

### Search API only

```bash
./tas setup search --runtime native
./tas doctor search
./tas up search
./tas smoke search
```

Setup asks for `GEMINI_API_KEY` and downloads the published index, about 13 GB. The same key powers full-paper answers by default. To change the answer model, edit [`SEARCH_QUERY_*`](llm-providers.md#search) after setup and before startup.

Search listens on port **8081**. For an installation entirely in Docker, use `./tas setup search --runtime docker --dockerize-search` instead.

<details>
<summary><strong>Try a literature search over HTTP</strong></summary>

```bash
curl -fsS http://localhost:8081/batch_search \
  -H 'Content-Type: application/json' \
  -d '{"queries":["test-time adaptation","online adaptation under distribution shift"],"max_results":5}'
```

Use `POST /query_paper` with `arxiv_ids` and a `query` for questions over full papers. See the [Search endpoint reference](../components/search-api/README.md#endpoints).

</details>

### AI Reviewer only

The Reviewer needs an Anthropic-compatible endpoint and access to a running **Search API**. You can run Search on this machine with `setup search reviewer`, or use a separate Search machine as shown below.

```bash
./tas setup reviewer --runtime docker
```

Setup asks for the Reviewer endpoint, key, and model. Before starting, edit `.env` so `REVIEWER_SEARCH_API_URL` points to your Search server, for example:

```dotenv
REVIEWER_SEARCH_API_URL=http://192.168.1.10:8081
```

Then start and check the Reviewer:

```bash
./tas doctor reviewer
./tas up reviewer
./tas smoke reviewer
```

Reviewer listens on port **8082**. For native mode, use `--runtime native` during setup and set `SEARCH_PUBLIC_URL` instead of `REVIEWER_SEARCH_API_URL`.

<details>
<summary><strong>Submit a paper to your own Reviewer over HTTP</strong></summary>

The local Reviewer accepts complete paper text through `POST /review/start`. This example reads a self-contained TeX file and submits its contents:

```bash
python3 -c 'import json,pathlib; print(json.dumps({"latex_content":pathlib.Path("main.tex").read_text(),"title":"My paper","abstract":""}))' \
  | curl -fsS http://localhost:8082/review/start \
      -H 'Content-Type: application/json' --data-binary @-
```

It returns a `job_id`. Poll `http://localhost:8082/review/status/<job_id>` for the feedback. For a source project, the local multipart route is:

```bash
curl -fsS http://localhost:8082/review/start_source \
  -F 'paper=@paper-source.zip' -F 'title=My paper'
```

The hosted PDF/Word upload flow and MCPs are supplied by the separate [public gateway](../deploy/hosted/README.md); installing the local Reviewer alone does not install that gateway.

</details>

### AI Scientist only

The Scientist needs an Anthropic-compatible endpoint and reachable **Search and Review APIs**.

```bash
./tas setup scientist --runtime docker
```

Setup asks for the Scientist endpoint, key, and model. Before running a paper, edit `.env`:

```dotenv
SCIENTIST_SEARCH_API_URL=http://192.168.1.10:8081
SCIENTIST_REVIEW_API_URL=http://192.168.1.11:8082
```

Create [one paper JSON](paper-task-files.md), then run:

```bash
./tas doctor scientist
./tas run components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600
```

For native mode, install with `--runtime native`, set `SEARCH_PUBLIC_URL` and `REVIEW_API_URL`, and launch with `./tas run --runtime native ...`. Native runs use host devices directly; `--gpus` is a Docker option.

## Separate machines

For example, use **192.168.1.10 for Search**, **192.168.1.11 for Reviewer**, and a third machine for the Scientist. Replace these private network addresses with your machines' reachable addresses.

| Machine | Install | Set in that machine's `.env` before startup |
| --- | --- | --- |
| Search | `./tas setup search --runtime native` | `GEMINI_API_KEY`, entered by setup |
| Reviewer in Docker | `./tas setup reviewer --runtime docker` | `REVIEWER_SEARCH_API_URL=http://192.168.1.10:8081` |
| Scientist in Docker | `./tas setup scientist --runtime docker` | `SCIENTIST_SEARCH_API_URL=http://192.168.1.10:8081` and `SCIENTIST_REVIEW_API_URL=http://192.168.1.11:8082` |

Reviewer and Scientist also need their own [LLM settings](llm-providers.md), entered by setup. Start Search with `./tas up search`, start Reviewer with `./tas up reviewer`, then run the Scientist's paper JSON. Each machine needs its own checkout; no shared filesystem is required.

For **native** consumers, the setting names differ:

| Consumer | Native settings | Docker settings |
| --- | --- | --- |
| Reviewer → Search | `SEARCH_PUBLIC_URL` | `REVIEWER_SEARCH_API_URL` |
| Scientist → Search | `SEARCH_PUBLIC_URL` | `SCIENTIST_SEARCH_API_URL` |
| Scientist → Reviewer | `REVIEW_API_URL` | `SCIENTIST_REVIEW_API_URL` |

On one machine, native consumers use `http://localhost:8081` and `http://localhost:8082`. Docker consumers use `http://host.docker.internal:8081` and `http://host.docker.internal:8082` to reach the host. A container's `localhost` refers to that container.

Keep the internal APIs on a trusted network. To expose them to the internet with HTTPS, file uploads, MCP, request limits, and a persistent queue, follow the [public server deployment guide](../deploy/hosted/README.md).

## Configuration without prompts

For automated installs, copy `.env.example` to `.env` and fill in the [keys, models, and endpoint URLs](llm-providers.md) first:

```bash
cp .env.example .env
# Edit .env with your values, then:
./tas setup search reviewer scientist --runtime docker --non-interactive
./tas doctor
./tas up
./tas smoke
```

Use `--runtime native` for an automated native installation. Setup creates its own environments; you do not need to activate them manually.

## Service commands

| Command | Purpose |
| --- | --- |
| `./tas status` | Check the installed components' health |
| `./tas doctor` | Check credentials, runtime, and configuration |
| `./tas smoke` | Test real Search retrieval and Reviewer health |
| `./tas logs search` | Follow Search output |
| `./tas logs reviewer` | Follow Reviewer output |
| `./tas down` | Stop services on this machine |

You can pass a component to `status`, `doctor`, or `smoke`, for example `./tas smoke search`. A smoke test does not run an experiment or generate a full review.

Native services run as detached processes. For a permanent server, use systemd or your platform's process manager; the [hosted deployment](../deploy/hosted/README.md) includes systemd examples.

## Outputs and secrets

Keep these paths when moving or backing up an installation:

| Path | Contents |
| --- | --- |
| `data/search-index/` | Downloaded index |
| Docker volume `theappliedscientist-review-trajectories` | Reviewer trajectories in Docker mode |
| `components/ai-scientist/jobs/` | Experiments, manuscripts, reviews, and run logs |
| `.env` | Private configuration and credentials |

Docker Scientist runs mount the Docker socket so Harbor can create experiment containers. Native jobs execute commands on the host. Use a dedicated machine for research code.

Keep `.env` private and inspect trajectories before sharing them. The repository includes a sanitizer:

```bash
python3 components/ai-scientist/scripts/sanitize_secrets.py --help
```
