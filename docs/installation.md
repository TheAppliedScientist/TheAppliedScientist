# Install prerequisites

[Back to the README](../README.md#run-the-complete-system-yourself)

Choose your operating system, then install the tools for **Docker** or **native** mode. Hosted MCP users can skip this guide entirely.

| Component | Docker mode | Native mode |
| --- | --- | --- |
| Search API | Python 3.12; Docker only if using `--dockerize-search` | Python 3.12 |
| AI Reviewer | Python 3.12 and Docker with Compose | Python 3.12 and Node.js 22 |
| AI Scientist | Python 3.12 and Docker with Compose | Python 3.12, Node.js 22, uv, Git, curl, jq, GNU timeout, and LaTeX |

`./tas setup` creates the component Python environments, installs dependencies, and installs the pinned Claude Code versions. You do not need to install Claude Code globally for these components. Native paper experiments may need additional packages specified by the paper's code.

## Linux

These commands are for **Ubuntu 22.04 or 24.04**. For other distributions, use their package manager for the tools listed above and the official [uv installer](https://docs.astral.sh/uv/getting-started/installation/).

### Common tools

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates

curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"
uv python install 3.12
export PATH="$(dirname "$(uv python find 3.12)"):$PATH"

python3 --version
python3.12 --version
```

The PATH command selects Python 3.12 for this terminal. Run it again in a new terminal before using the project if your system Python is older.

### Docker

Install **Docker Engine 27+ and Compose 2.30+** using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). For other Linux distributions, use the [Docker installation index](https://docs.docker.com/engine/install/).

To run Docker without `sudo`, follow Docker's [Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/) and open a new terminal. Then check:

```bash
docker --version
docker compose version
docker info
```

### Native

For Reviewer or Scientist, install Node.js 22:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version
npm --version
```

The NodeSource command configures the package repository. The following `apt-get install` command installs Node.js.

For the Scientist, also install the research and paper tools:

```bash
sudo apt-get install -y jq coreutils pandoc poppler-utils git-lfs \
  latexmk biber chktex texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-fonts-recommended texlive-science
git lfs install
timeout --version
pdflatex --version
```

## macOS

Install [Homebrew](https://brew.sh/) first if needed.

### Common tools

```bash
brew install python@3.12 git curl
export PATH="$(brew --prefix python@3.12)/libexec/bin:$PATH"
python3 --version
python3.12 --version
```

### Docker

Install and start [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/). Choose the download for your Mac's processor. Check:

```bash
docker --version
docker compose version
docker info
```

### Native

For Reviewer, install Node.js 22:

```bash
brew install node@22
export PATH="$(brew --prefix python@3.12)/libexec/bin:$(brew --prefix node@22)/bin:$PATH"
node --version
npm --version
```

For Scientist, also install the research and paper tools:

```bash
brew install uv jq coreutils pandoc poppler git-lfs
brew install --cask mactex-no-gui
export PATH="$(brew --prefix python@3.12)/libexec/bin:$(brew --prefix node@22)/bin:$(brew --prefix coreutils)/libexec/gnubin:/Library/TeX/texbin:$PATH"
git lfs install

node --version
npm --version
uv --version
timeout --version
pdflatex --version
```

The [GNU coreutils](https://formulae.brew.sh/formula/coreutils) PATH entry supplies the `timeout` command used by native Scientist runs. Keep the PATH setting in your shell configuration for future terminals.

Docker `--gpus` requires NVIDIA hardware and is not available on macOS. Native experiments can use the hardware supported by the paper's code, including CPU or Apple MPS.

## Windows

Run the project inside **WSL 2**, rather than Windows PowerShell. In an Administrator PowerShell window:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart if prompted, open Ubuntu from the Start menu, and follow the [Linux common tools](#common-tools) and native instructions above. Clone the project inside the WSL filesystem, such as `/home/your-user/TheAppliedScientist`.

For Docker mode, install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/), select the WSL 2 backend, and enable integration with Ubuntu under **Settings → Resources → WSL Integration**. Run the Docker checks from the Ubuntu terminal.

See Microsoft's [WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install) if WSL is already installed or needs updating.

## NVIDIA GPU

Search and Reviewer do not need a GPU. Use this section only for GPU experiments with the Scientist.

| Runtime | GPU setup |
| --- | --- |
| Docker on Linux | NVIDIA driver and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) configured for Docker |
| Docker on Windows/WSL | [Docker Desktop GPU support](https://docs.docker.com/desktop/features/gpu/) and a compatible Windows NVIDIA driver |
| Native | Host driver and the CUDA/PyTorch packages required by the experiment code; do not pass `--gpus` |

For Docker, verify the GPU before starting a paper:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

Then add `--gpus 1` to `./tas run`. In native mode the experiment code uses the host devices directly.

**Next:** return to [API keys and setup](../README.md#2-prepare-api-keys).
