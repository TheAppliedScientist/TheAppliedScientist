<p align="center">
  <img src="docs/assets/theappliedscientist-banner.webp" alt="TheAppliedScientist: Search. Experiment. Review. Revise." width="100%">
</p>

TheAppliedScientist searches literature, reviews papers, runs experiments, and
revises manuscripts.

## Table of contents

- [Use Search and Review with MCP](#use-search-and-review-with-mcp)
- [Run the complete system yourself](#run-the-complete-system-yourself)
  - [Docker](#option-a-docker)
  - [Native, without Docker](#option-b-native-without-docker)
- [Add your paper](#add-your-paper)
- [Run TheAppliedScientist](#run-theappliedscientist)
- [How the system works](#how-the-system-works)
- [Deployment and configuration](#deployment-and-configuration)
- [Commands](#commands)
- [Documentation](#documentation)
- [Security](#security)
- [License](#license)

## Use Search and Review with MCP

Connect both hosted tools and the skill from your paper's folder. No clone or
API key is needed.

### 1. Set up your agent

Run one block in Bash (WSL or Git Bash on Windows).

#### Claude Code

```bash
# Connect Search and Reviewer
claude mcp add --transport http appliedscientist-search https://search.eigenlabs.online/mcp
claude mcp add --transport http appliedscientist-review https://review.eigenlabs.online/mcp

# Install the skill in this paper folder
mkdir -p .claude/skills/appliedscientist
curl -fsSL \
  https://raw.githubusercontent.com/TheAppliedScientist/TheAppliedScientist/main/skills/appliedscientist/SKILL.md \
  -o .claude/skills/appliedscientist/SKILL.md
```

#### Codex

```bash
# Connect Search and Reviewer
codex mcp add appliedscientist-search --url https://search.eigenlabs.online/mcp
codex mcp add appliedscientist-review --url https://review.eigenlabs.online/mcp

# Install the skill in this paper folder
mkdir -p .agents/skills/appliedscientist
curl -fsSL \
  https://raw.githubusercontent.com/TheAppliedScientist/TheAppliedScientist/main/skills/appliedscientist/SKILL.md \
  -o .agents/skills/appliedscientist/SKILL.md
```

### 2. Try it

Open your agent in the same folder and try one prompt:

| Task | Example prompt |
| --- | --- |
| Review a PDF | `Use the AppliedScientist skill to review papers/draft.pdf.` |
| Review LaTeX source | `Use the AppliedScientist skill to review paper-source.zip.` |
| Search a topic | `Use the AppliedScientist skill to find papers on test-time adaptation for image classifiers.` |
| Find similar papers | `Use the AppliedScientist skill to find papers related to arXiv:1706.03762.` |

> [!NOTE]
> The Reviewer searches related work itself. Use Search separately for your own
> literature questions; you never need to search before submitting a review.

[Supported paper files](docs/hosted-tools.md) ·
[Search API docs](https://search.eigenlabs.online/docs) ·
[Review API docs](https://review.eigenlabs.online/docs)

---

## Run the complete system yourself

The full local setup runs Search, AI Reviewer, and AI Scientist. It can search
literature, run experiments on your code, revise your manuscript, and review
the revision. Docker and native mode are both supported. Neither uses a hosted
execution service.

### Clone

```bash
git clone https://github.com/TheAppliedScientist/TheAppliedScientist.git
cd TheAppliedScientist
```

### Option A: Docker

Use Docker Engine 27+ with Docker Compose 2.30+ and Python 3.12+. Keep at least
20 GB free for Search and Reviewer. A GPU Scientist run also needs about 60 GB
free because its CUDA, LaTeX, and experiment images are large.

Install Docker for your operating system:

- [Linux: Docker Engine and Compose](https://docs.docker.com/engine/install/)
- [macOS: Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/)
- [Windows: Docker Desktop with WSL 2](https://docs.docker.com/desktop/setup/install/windows-install/)

Check the installation:

```bash
python3 --version
docker --version
docker compose version
```

Set up and start all three components:

```bash
./tas setup search reviewer scientist --runtime docker
./tas up
./tas smoke
```

This setup runs Search with local Python and runs Reviewer and Scientist with
local Docker. The first run downloads a search index of about 13 GB.

To run Search in Docker too:

```bash
./tas setup search reviewer scientist --runtime docker --dockerize-search
./tas up
```

For `--gpus 1`, install the NVIDIA driver and NVIDIA Container Toolkit, then
verify Docker can see the GPU:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

Use the official [NVIDIA Container Toolkit installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

### Option B: Native, without Docker

Install Python 3.12, Node.js 22, Git, curl, Pandoc, and LaTeX.

<details>
<summary><strong>Ubuntu 22.04 or 24.04</strong></summary>

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates \
  pandoc latexmk texlive-latex-base texlive-latex-extra texlive-fonts-recommended

curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"
uv python install 3.12

curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

python3.12 --version
node --version
npm --version
```

The NodeSource command may report unrelated third-party repository warnings. A
final `Repository configured successfully` message means it worked.

</details>

<details>
<summary><strong>macOS with Homebrew</strong></summary>

```bash
brew install python@3.12 node@22 git curl pandoc mactex-no-gui
brew link --overwrite --force node@22

python3.12 --version
node --version
npm --version
```

</details>

<details>
<summary><strong>Windows</strong></summary>

Run native mode inside WSL 2. In an Administrator PowerShell window:

```powershell
wsl --install -d Ubuntu-24.04
```

Open Ubuntu from the Start menu, clone the repository there, then use the
Ubuntu commands above.

</details>

Now create the project environments and install all pinned dependencies:

```bash
./tas setup search reviewer scientist --runtime native
./tas up search reviewer --runtime native
./tas smoke search reviewer
```

`./tas setup` also installs the exact Claude Code versions used by Reviewer and
Scientist.

## Add your paper

Create `components/ai-scientist/user-ideas/my-paper.json`. The JSON can point directly to
your paper, reviews, and code anywhere on the machine. Relative paths start from
the JSON file. The runner copies those inputs into an isolated workspace and
does not modify the originals.

#### When public reviews are available

Use this JSON and replace the three paths. Code is optional, but strongly
recommended because it lets TheAppliedScientist reproduce and improve real
results.

```json
{
  "Name": "my_paper",
  "Title": "My Paper",
  "Task": "Read the paper, public reviews, and code. Reproduce the main result, address the reviewer concerns with real experiments, revise the paper, and continue iterating with the AI Reviewer.",
  "Paper": {
    "local_path": "/path/to/paper/main.tex",
    "code_path": "/path/to/code"
  },
  "Human_Reviews_From_OpenReview": {
    "source_file": "/path/to/reviews.md"
  },
  "What_NOT_To_Do": "Do not fabricate results, citations, or experiments.",
  "GPU_Needed": false
}
```

Reviews may instead be stored directly in the JSON as an object, list, or string.

#### When public reviews are not available

Set the review field to `false`:

```json
{
  "Name": "my_paper",
  "Title": "My Paper",
  "Task": "Read the paper and code. Reproduce the main result, use Search to find the most important gaps, run evidence-backed improvements, revise the paper, and iterate with the AI Reviewer.",
  "Paper": {
    "local_path": "/path/to/paper/main.tex",
    "code_path": "/path/to/code"
  },
  "Human_Reviews_From_OpenReview": false,
  "What_NOT_To_Do": "Do not fabricate results, citations, or experiments.",
  "GPU_Needed": false
}
```

You can also provide a public paper URL instead of local files. See
[Paper task files](docs/paper-task-files.md) for all supported input forms and complete examples.

## Run TheAppliedScientist

Docker without GPU:

```bash
./tas run components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600
```

Docker with GPU, after the verification command above:

```bash
./tas run components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600 --gpus 1
```

Native mode:

```bash
./tas run --runtime native components/ai-scientist/user-ideas/my-paper.json -- --timeout 21600
```

The paper JSON is checked automatically before every job.

To check it without starting a run:

```bash
./tas validate components/ai-scientist/user-ideas/my-paper.json
```

TheAppliedScientist saves each run under:

```text
TheAppliedScientist/components/ai-scientist/jobs/my-paper__<timestamp>/
```

The run directory contains the experiment code, results, figures, LaTeX
source, compiled PDF, review history, and agent trajectory.

## How the system works

<p align="center">
  <img src="docs/assets/figure-1-system.svg" alt="TheAppliedScientist system architecture" width="100%">
</p>

TheAppliedScientist combines three components. They can run together on one
machine or communicate over the network from separate machines.

| Component | Purpose | Default port |
|---|---|---:|
| Search API | Searches the paper index, follows citations, and reads full papers | 8081 |
| AI Reviewer | Independently reads, checks, and scores each manuscript | 8082 |
| AI Scientist | Runs experiments, writes the paper, and acts on reviews | On demand |

## Deployment and configuration

### Deploy selected components

Each component can be installed by itself:

```bash
./tas setup search --runtime native
./tas setup reviewer --runtime docker
./tas setup scientist --runtime docker
```

This is useful when Search, Reviewer, and Scientist run on different machines.
See [Deployment](docs/deployment.md) for complete machine-by-machine instructions.
To operate a public Search, Reviewer, and MCP server, see the
[hosted deployment guide](deploy/hosted/README.md).

### Configure LLM endpoints

Reviewer and the default Scientist use Claude Code. Their endpoint must implement
the Anthropic Messages API, including streaming and tool calls.

```dotenv
ANTHROPIC_BASE_URL=https://provider.example/anthropic
ANTHROPIC_API_KEY=your-key
ANTHROPIC_MODEL=provider-model-name
```

Direct Anthropic, DeepSeek's Anthropic endpoint, and other Anthropic-compatible
gateways use this same interface. An OpenAI-only endpoint needs an
OpenAI-to-Anthropic converter. OpenAI-format integrations use the separate
`OPENAI_*` settings.

Search requires `GEMINI_API_KEY` because the published vectors were created with
Gemini Embedding 2. See [LLM endpoints](docs/llm-providers.md) for provider-specific
configuration.

## Commands

```bash
./tas doctor              # check configuration
./tas status              # show service health
./tas smoke               # run a real search and health checks
./tas logs search         # follow Search logs
./tas logs reviewer       # follow Reviewer logs
./tas down                # stop services
```

## Documentation

| Guide | Contents |
|---|---|
| [Paper task files](docs/paper-task-files.md) | Paper, code, review, URL, and no-review inputs |
| [Deployment](docs/deployment.md) | Docker, native, and separate-machine setup |
| [LLM endpoints](docs/llm-providers.md) | Anthropic and OpenAI wire formats |
| [Experiment compatibility](docs/reproducing-experiments.md) | Reference prompts, versions, and locked files |

## Security

Keep `.env` private. Docker mode mounts the Docker socket for experiment
containers. Native mode executes research commands on the host. Review paper
tasks and input code before running them.

Sanitize trajectories before publishing them:

```bash
python components/ai-scientist/scripts/sanitize_secrets.py --help
```

## License

MIT. Vendored code keeps its original license notices.
