#!/bin/bash
# Run an AI Scientist experiment in Harbor
#
# Usage: ./run.sh <ideas/idea_*.json> [OPTIONS]
#
# Examples:
#   ./run.sh ideas/idea_tabulartransformer.json                              # Local Docker, CPU
#   ./run.sh ideas/idea_tabulartransformer.json --gpus 1                     # Local Docker, GPU
#   ./run.sh ideas/idea_tabulartransformer.json --model anthropic/claude-sonnet-4-5-20250929
#   ./run.sh ideas/idea_tabulartransformer.json --resume-from jobs/tabulartransformer__2026-02-22__12-00-00/
#   ./run.sh user-ideas/my-paper.json --agent gemini-cli                     # Gemini CLI agent

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load provider/runtime configuration before resolving agent and model defaults.
if [[ "${TAS_ENV_LOADED:-0}" != "1" ]]; then
    for env_file in "$SCRIPT_DIR/.env" "$SCRIPT_DIR/../.env"; do
        if [[ -f "$env_file" ]] && bash -n "$env_file" 2>/dev/null; then
            echo "Loading env from $env_file"
            set -a
            source "$env_file"
            set +a
            break
        fi
    done
fi

IDEA_JSON=""
MODEL=""           # empty = auto-select based on agent type
TIMEOUT="14400"
RESUME_FROM=""
RESUME_BRANCH=""   # auto-detected from job dir, or set via gitlab_setup.py
ENV_TYPE=""        # empty = docker (default)
GPUS="0"
# The published experiment runs used Harbor's upstream Claude Code agent.
USE_UPSTREAM_AGENT="1"
ARTIFACT_SYNC_INTERVAL="180"
AGENT_TYPE="claude-code"
PATCHED_AGENT_IMPORT_PATH=""  # set after arg parsing based on AGENT_TYPE
FEEDBACK=""
# Optional vars referenced under `set -u`; declare here so missing-env doesn't
# abort runs that don't use GitLab / resume / a custom JOB_NAME.
GITLAB_RESUME_BRANCH="${GITLAB_RESUME_BRANCH:-}"
GITLAB_BRANCH="${GITLAB_BRANCH:-}"
GITLAB_REPO_URL="${GITLAB_REPO_URL:-}"
GITLAB_WEB_URL="${GITLAB_WEB_URL:-}"
PREV_ARTIFACTS="${PREV_ARTIFACTS:-}"
JOB_NAME="${JOB_NAME:-}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --model)
            MODEL="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --resume-from)
            RESUME_FROM="$2"
            shift 2
            ;;
        --env)
            ENV_TYPE="$2"
            shift 2
            ;;
        --gpus)
            GPUS="$2"
            shift 2
            ;;
        --use-upstream-agent)
            USE_UPSTREAM_AGENT="1"
            shift
            ;;
        --artifact-sync-interval)
            ARTIFACT_SYNC_INTERVAL="$2"
            shift 2
            ;;
        --agent)
            AGENT_TYPE="$2"
            shift 2
            ;;
        --feedback)
            FEEDBACK="$2"
            shift 2
            ;;
        --feedback-file)
            if [[ ! -f "$2" ]]; then
                echo "Error: feedback file '$2' not found" >&2
                exit 1
            fi
            FEEDBACK="$(cat "$2")"
            shift 2
            ;;
        -h|--help)
            echo "Usage: ./run.sh <idea.json> [OPTIONS]"
            echo ""
            echo "Arguments:"
            echo "  idea.json                  Path to research idea JSON file"
            echo ""
            echo "Options:"
            echo "  --agent TYPE               Agent: claude-code (default), gemini-cli, or codex"
            echo "  --model MODEL              LLM model (auto-selected per agent if omitted)"
            echo "  --timeout SECS             Agent timeout in seconds (default: 7200)"
            echo "  --resume-from JOB_PATH     Resume from a previous run's artifacts"
            echo "  --env ENV                  Environment: docker (default); remote sandboxes are unsupported"
            echo "  --gpus N                   Number of GPUs (default: 0; Docker runtime only)"
            echo "  --use-upstream-agent       Use Harbor's built-in agent (no artifact sync)"
            echo "  --artifact-sync-interval S Artifact sync interval in seconds (default: 180)"
            echo "  --feedback TEXT            Feedback/notes to include in the instruction"
            echo "  --feedback-file FILE      Read feedback from a file (avoids shell quoting issues)"
            exit 0
            ;;
        *)
            if [[ -z "$IDEA_JSON" ]]; then
                IDEA_JSON="$1"
            else
                echo "Error: unexpected argument '$1'" >&2
                exit 1
            fi
            shift
            ;;
    esac
done

if [[ -n "$ENV_TYPE" && "$ENV_TYPE" != "docker" && "$ENV_TYPE" != "native" ]]; then
    echo "Error: environment must be 'docker' or 'native' (got '$ENV_TYPE')." >&2
    exit 2
fi

if [[ -z "$IDEA_JSON" ]]; then
    echo "Error: idea.json path required" >&2
    echo "Usage: ./run.sh <idea.json> [OPTIONS]" >&2
    exit 1
fi

if [[ ! -f "$IDEA_JSON" ]]; then
    echo "Error: $IDEA_JSON not found" >&2
    exit 1
fi

# Optional legacy sidecar inputs live next to the idea file:
#   user-ideas/my-study.json + user-ideas/my-study.inputs/*
# They are exposed read-only-by-convention to the agent at /app/inputs/.
IDEA_INPUT_DIR="${IDEA_JSON%.json}.inputs"

# --- Extract idea name for job naming ---
# Strip path and extension: idea_tabulartransformer.json -> tabulartransformer
IDEA_BASENAME="$(basename "$IDEA_JSON" .json)"
IDEA_NAME="${IDEA_BASENAME#idea_}"       # remove "idea_" prefix if present
IDEA_NAME="${IDEA_NAME#idea}"            # remove "idea" prefix if present (no underscore)
IDEA_NAME="${IDEA_NAME:-unknown}"        # fallback

# --- Resolve agent type defaults ---
case "$AGENT_TYPE" in
    claude-code)
        [[ -z "$MODEL" ]] && MODEL="${ANTHROPIC_MODEL:-claude-opus-4-8[1m]}"
        export MAX_THINKING_TOKENS="${MAX_THINKING_TOKENS:-31999}"
        PATCHED_AGENT_IMPORT_PATH="local_harbor_agents.patched_claude_code:PatchedClaudeCode"
        UPSTREAM_AGENT_FLAG="claude-code"
        ;;
    gemini-cli)
        [[ -z "$MODEL" ]] && MODEL="google/gemini-3.1-pro-preview"
        PATCHED_AGENT_IMPORT_PATH="local_harbor_agents.patched_gemini_cli:PatchedGeminiCli"
        UPSTREAM_AGENT_FLAG="gemini-cli"
        ;;
    codex)
        [[ -z "$MODEL" ]] && MODEL="openai/gpt-5.4"
        PATCHED_AGENT_IMPORT_PATH="local_harbor_agents.patched_codex:PatchedCodex"
        UPSTREAM_AGENT_FLAG="codex"
        ;;
    *)
        echo "Error: unknown agent type '$AGENT_TYPE' (use claude-code, gemini-cli, or codex)" >&2
        exit 1
        ;;
esac

# Validate and setup local GPU support
if [[ "$GPUS" != "0" && "$ENV_TYPE" == "native" ]]; then
    echo "Error: --gpus applies to the Docker runtime. Native mode uses the host environment." >&2
    exit 2
elif [[ "$GPUS" != "0" ]]; then
    # Check if nvidia runtime is available for local Docker
    if ! docker info 2>/dev/null | grep -q "nvidia"; then
        echo "Error: --gpus requires NVIDIA Container Toolkit (nvidia-docker)" >&2
        echo "Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html" >&2
        exit 1
    fi
    # Patch Harbor for local GPU support (idempotent)
    python3 -c "from local_harbor_agents import ensure_gpu_support; ensure_gpu_support(quiet=True)" 2>/dev/null || \
        PYTHONPATH="$SCRIPT_DIR" python3 -c "from local_harbor_agents import ensure_gpu_support; ensure_gpu_support(quiet=True)"
fi

if ! [[ "$ARTIFACT_SYNC_INTERVAL" =~ ^[0-9]+$ ]] || [[ "$ARTIFACT_SYNC_INTERVAL" -lt 30 ]]; then
    echo "Error: --artifact-sync-interval must be an integer >= 30" >&2
    exit 1
fi

# --- Ensure 'harbor' CLI is on PATH (auto-activate a venv if needed) ---
if [[ "$ENV_TYPE" != "native" ]] && ! command -v harbor >/dev/null 2>&1; then
    for _venv in "$SCRIPT_DIR/.venv" "/root/venv"; do
        if [[ -x "$_venv/bin/harbor" ]]; then
            # shellcheck disable=SC1091
            source "$_venv/bin/activate"
            echo "Activated venv at $_venv (harbor found)"
            break
        fi
    done
    if ! command -v harbor >/dev/null 2>&1; then
        echo "ERROR: 'harbor' CLI not found on PATH and no venv with it at \$SCRIPT_DIR/.venv or /root/venv. Run 'pip install -e .' inside an activated venv first." >&2
        exit 1
    fi
fi

# Persistent data directory (datasets, models, etc.) — mounted into container at /data
# Must come AFTER .env sourcing so DATA_DIR from .env takes priority
DATA_DIR="${DATA_DIR:-$SCRIPT_DIR/data}"
export DATA_DIR
mkdir -p "$DATA_DIR"

# Create per-job temp copy of harbor-task so concurrent jobs don't share staging dirs
TASK_DIR_TEMPLATE="$SCRIPT_DIR/harbor-task"
# mktemp generates mixed-case suffixes but Docker requires lowercase image names,
# and Harbor derives image names from the task directory name.
TASK_TMP_ROOT="${TAS_TASK_TMPDIR:-/tmp}"
mkdir -p "$TASK_TMP_ROOT"
TASK_DIR=$(mktemp -d "$TASK_TMP_ROOT/harbor-task-XXXXXX")
TASK_DIR_PARENT=$(dirname "$TASK_DIR")
TASK_DIR_BASENAME=$(basename "$TASK_DIR")
TASK_DIR_LC="$TASK_DIR_PARENT/$(echo "$TASK_DIR_BASENAME" | tr '[:upper:]' '[:lower:]')"
[ "$TASK_DIR" != "$TASK_DIR_LC" ] && mv "$TASK_DIR" "$TASK_DIR_LC"
TASK_DIR="$TASK_DIR_LC"
cp -r "$TASK_DIR_TEMPLATE"/* "$TASK_DIR/"

ENV_DIR="$TASK_DIR/environment"
INSTRUCTION_TEMPLATE="$TASK_DIR/instruction.md.template"
INSTRUCTION_OUT="$TASK_DIR/instruction.md"

# --- Resolve previous artifacts if resuming ---
PREV_ARTIFACTS=""
if [[ -n "$RESUME_FROM" ]]; then
    # Find the artifacts directory (support both job dir and trial dir)
    # Check all possible artifact locations (agent/, verifier/ are Harbor-mounted paths)
    for artifacts_subdir in "agent/artifacts" "verifier/artifacts" "artifacts"; do
        if [[ -d "$RESUME_FROM/$artifacts_subdir" ]]; then
            PREV_ARTIFACTS="$RESUME_FROM/$artifacts_subdir"
            break
        fi
    done

    # If not found directly, look inside trial subdirectory (job dir case)
    if [[ -z "$PREV_ARTIFACTS" ]]; then
        TRIAL_DIR=$(find "$RESUME_FROM" -maxdepth 1 -type d -name "harbor-task*" | head -1)
        if [[ -n "$TRIAL_DIR" ]]; then
            for artifacts_subdir in "agent/artifacts" "verifier/artifacts" "artifacts"; do
                if [[ -d "$TRIAL_DIR/$artifacts_subdir" ]]; then
                    PREV_ARTIFACTS="$TRIAL_DIR/$artifacts_subdir"
                    break
                fi
            done
        fi
    fi

    if [[ -z "$PREV_ARTIFACTS" || ! -d "$PREV_ARTIFACTS" ]]; then
        echo "Error: no artifacts found in $RESUME_FROM" >&2
        echo "Expected artifacts/ directory with previous run outputs" >&2
        exit 1
    fi

    echo "Resuming from: $PREV_ARTIFACTS"
    ls "$PREV_ARTIFACTS/" 2>/dev/null | sed 's/^/  /'
    echo ""

    # Auto-detect previous run's GitLab branch from saved metadata
    for branch_file in "$RESUME_FROM/gitlab_branch" "$RESUME_FROM"/harbor-task-*/gitlab_branch; do
        if [[ -f "$branch_file" ]]; then
            RESUME_BRANCH="$(cat "$branch_file")"
            echo "Detected previous GitLab branch: $RESUME_BRANCH"
            break
        fi
    done
fi

# --- Copy .env into the build context so docker-compose env_file picks it up ---
# Uses same order as shell sourcing: project dir first, then parent dir
for env_file in "$SCRIPT_DIR/.env" "$SCRIPT_DIR/../.env"; do
    if [[ -f "$env_file" ]]; then
        cp "$env_file" "$ENV_DIR/.env"
        break
    fi
done

# Pass AGENT_TYPE to the container so submit_for_review.sh knows which CLI to use
echo "AGENT_TYPE=$AGENT_TYPE" >> "$ENV_DIR/.env"

# Default reviewer mode: api-external (calls the configured review HTTP API).
# Honors any REVIEWER_MODE the user sets in their environment; otherwise defaults
# to api-external so a fresh clone works out of the box without a CLI reviewer.
echo "REVIEWER_MODE=${REVIEWER_MODE:-api-external}" >> "$ENV_DIR/.env"
echo "REVIEW_API_URL=${REVIEW_API_URL:-http://localhost:8082}" >> "$ENV_DIR/.env"

# --- GitLab repo setup (if GITLAB_KEY is set) ---
GITLAB_REPO_URL=""
GITLAB_BRANCH=""
GITLAB_BRANCHES=""
GITLAB_WEB_URL=""
if [[ -n "${GITLAB_KEY:-}" ]]; then
    GITLAB_TS="$(date -u +%Y-%m-%d-%H-%M)"
    GITLAB_SETUP_ARGS=(--idea-name "$IDEA_NAME" --agent "$AGENT_TYPE" --timestamp "$GITLAB_TS")
    if [[ -n "$RESUME_BRANCH" ]]; then
        GITLAB_SETUP_ARGS+=(--resume-branch "$RESUME_BRANCH")
    fi
    GITLAB_JSON=$(python3 "$SCRIPT_DIR/scripts/gitlab_setup.py" \
        "${GITLAB_SETUP_ARGS[@]}" 2>/dev/null || echo "")
    if [[ -n "$GITLAB_JSON" ]]; then
        GITLAB_REPO_URL=$(echo "$GITLAB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('repo_url',''))")
        GITLAB_BRANCH=$(echo "$GITLAB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('branch',''))")
        GITLAB_WEB_URL=$(echo "$GITLAB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('web_url',''))")
        GITLAB_RESUME_BRANCH=$(echo "$GITLAB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('resume_branch',''))")
        GITLAB_BRANCHES=$(echo "$GITLAB_JSON" | python3 -c "
import sys, json
branches = json.load(sys.stdin).get('sibling_branches', [])
if branches:
    print('\n'.join(f'- \`{b}\`' for b in branches))
else:
    print('(none — this is the first run)')
")
        # Append GitLab vars to the container's .env (used by local Docker via docker-compose)
        {
            echo ""
            echo "GITLAB_REPO_URL=$GITLAB_REPO_URL"
            echo "GITLAB_BRANCH=$GITLAB_BRANCH"
            [[ -n "$GITLAB_RESUME_BRANCH" ]] && echo "GITLAB_RESUME_BRANCH=$GITLAB_RESUME_BRANCH"
        } >> "$ENV_DIR/.env"
        # Also write to scripts/.gitlab_env for the inner experiment image.
        {
            echo "GITLAB_REPO_URL=$GITLAB_REPO_URL"
            echo "GITLAB_BRANCH=$GITLAB_BRANCH"
            [[ -n "$GITLAB_RESUME_BRANCH" ]] && echo "GITLAB_RESUME_BRANCH=$GITLAB_RESUME_BRANCH"
        } > "$SCRIPT_DIR/scripts/.gitlab_env"
        echo "GitLab repo: $GITLAB_WEB_URL (branch: $GITLAB_BRANCH)"
        if [[ -n "$GITLAB_RESUME_BRANCH" ]]; then
            echo "  Branching off: $GITLAB_RESUME_BRANCH"
        fi
    else
        echo "Warning: GitLab setup failed (continuing without git push)" >&2
    fi
fi

# --- Stage repo files into the Docker build context ---
echo "Staging build context..."
cp -rL "$SCRIPT_DIR/paper_template" "$ENV_DIR/paper_template"
cp -rL "$SCRIPT_DIR/scripts"            "$ENV_DIR/scripts"
cp -rL "$SCRIPT_DIR/.claude"            "$ENV_DIR/.claude"
mkdir -p "$ENV_DIR/inputs"
if [[ -d "$IDEA_INPUT_DIR" ]]; then
    cp -rL "$IDEA_INPUT_DIR/." "$ENV_DIR/inputs/"
    echo "Staged idea inputs from: $IDEA_INPUT_DIR"
fi
RESOLVED_IDEA_JSON="$TASK_DIR/paper-task.resolved.json"
python3 "$SCRIPT_DIR/scripts/prepare_paper_inputs.py" \
    "$IDEA_JSON" "$ENV_DIR/inputs" "$RESOLVED_IDEA_JSON"

# Write search API URL into the build context. The /app/search CLI inside the
# sandbox reads this file. The default is the local Search API.
echo "${SEARCH_PUBLIC_URL:-http://localhost:8081}" > "$ENV_DIR/search_api_url.txt"

# Stage Codex OAuth auth for Docker build context (if available)
mkdir -p "$ENV_DIR/.codex_auth"
touch "$ENV_DIR/.codex_auth/.keep"
if [[ -f "$HOME/.codex/auth.json" ]]; then
    cp "$HOME/.codex/auth.json" "$ENV_DIR/.codex_auth/auth.json"
    echo "Staged Codex OAuth auth.json"
fi
if [[ -f "$HOME/.codex/config.toml" ]]; then
    cp "$HOME/.codex/config.toml" "$ENV_DIR/.codex_auth/config.toml"
    echo "Staged Codex config.toml"
fi

# Always create a fresh non-empty prev_artifacts directory because Docker COPY
# requires its source path to exist even on a first run.
mkdir -p "$ENV_DIR/prev_artifacts"
touch "$ENV_DIR/prev_artifacts/.keep"
if [[ -n "${GITLAB_RESUME_BRANCH:-}" ]]; then
    # Git-based resume: workspace will be populated by branching off the previous
    # GitLab branch at container runtime — no need to stage artifacts into Docker.
    echo "Skipping artifact staging (git-based resume from branch $GITLAB_RESUME_BRANCH)"
elif [[ -n "${PREV_ARTIFACTS:-}" ]]; then
    cp -r "$PREV_ARTIFACTS"/* "$ENV_DIR/prev_artifacts/" 2>/dev/null || true
fi

# --- Generate Dockerfile from CPU or GPU source ---
if [[ "$GPUS" != "0" ]]; then
    echo "Using GPU image (pytorch + CUDA)"
    cp "$ENV_DIR/Dockerfile.gpu" "$ENV_DIR/Dockerfile"
else
    cp "$ENV_DIR/Dockerfile.cpu" "$ENV_DIR/Dockerfile"
fi

cleanup() {
    # Save GitLab branch name for future --resume-from auto-detection
    local _JOB_DIR="$SCRIPT_DIR/jobs/$JOB_NAME"
    if [[ -n "$GITLAB_BRANCH" ]] && [[ -d "$_JOB_DIR" ]]; then
        echo "$GITLAB_BRANCH" > "$_JOB_DIR/gitlab_branch" 2>/dev/null || true
    fi
    # Push artifacts to GitLab in background (non-blocking).
    if [[ -n "${GITLAB_KEY:-}" ]] && [[ -d "$_JOB_DIR" ]]; then
        echo ""
        echo "=== Pushing artifacts to GitLab ==="
        local _PUSH_ARGS=(--job-dir "$_JOB_DIR")
        if [[ -n "$GITLAB_BRANCH" ]]; then
            _PUSH_ARGS+=(--branch "$GITLAB_BRANCH")
        fi
        nohup python3 "$SCRIPT_DIR/scripts/push_to_gitlab.py" \
            "${_PUSH_ARGS[@]}" \
            >> "$_JOB_DIR/gitlab_push.log" 2>&1 &
        echo "  GitLab push started (PID: $!, log: $_JOB_DIR/gitlab_push.log)"
    fi
    rm -rf "$TASK_DIR"
}
trap cleanup EXIT

# --- Generate instruction.md from template ---
RESUME_NOTE=""
if [[ -n "$GITLAB_RESUME_BRANCH" ]]; then
    RESUME_NOTE="
## Resumed Session

This run continues from a previous session. Your workspace has been initialized
from the previous run's GitLab branch (\`$GITLAB_RESUME_BRANCH\`) — all code,
results, figures, paper drafts, and submissions are already in place.

Review what's already done before continuing. Focus on completing the missing
pieces rather than redoing work. Check the quality of existing artifacts and
improve them if needed. Inspect previous reviewer feedback in \`submissions/\`
to understand what needs fixing.
"
elif [[ -n "$PREV_ARTIFACTS" ]]; then
    # Build a summary of what already exists
    EXISTING=""
    [[ -d "$PREV_ARTIFACTS/experiment_codebase" ]] && \
        EXISTING="$EXISTING\n- experiment_codebase/ ($(ls "$PREV_ARTIFACTS/experiment_codebase/" 2>/dev/null | wc -l | tr -d ' ') files)"
    [[ -d "$PREV_ARTIFACTS/figures" ]] && \
        EXISTING="$EXISTING\n- figures/ ($(ls "$PREV_ARTIFACTS/figures/" 2>/dev/null | wc -l | tr -d ' ') files)"
    [[ -d "$PREV_ARTIFACTS/literature" ]] && \
        EXISTING="$EXISTING\n- literature/ ($(ls "$PREV_ARTIFACTS/literature/" 2>/dev/null | wc -l | tr -d ' ') files)"
    [[ -f "$PREV_ARTIFACTS/paper.pdf" ]] && EXISTING="$EXISTING\n- paper.pdf"
    [[ -f "$PREV_ARTIFACTS/paper.tex" ]] && EXISTING="$EXISTING\n- paper.tex"
    [[ -f "$PREV_ARTIFACTS/review.json" ]] && EXISTING="$EXISTING\n- review.json"
    [[ -d "$PREV_ARTIFACTS/submissions" ]] && \
        EXISTING="$EXISTING\n- submissions/ ($(ls "$PREV_ARTIFACTS/submissions/" 2>/dev/null | grep -c '^v' || true) versions)"

    RESUME_NOTE="
## Resumed Session

This run continues from a previous session that timed out. Previous artifacts
have been pre-loaded into your workspace:
$(echo -e "$EXISTING")

Review what's already done before continuing. Focus on completing the missing
pieces rather than redoing work. Check the quality of existing artifacts and
improve them if needed.
"
fi

if [[ -n "$FEEDBACK" ]]; then
    RESUME_NOTE="$RESUME_NOTE
## Feedback from Previous Run

$FEEDBACK
"
fi

python3 -c "
import sys, os
template = open(sys.argv[1]).read()
idea = open(sys.argv[2]).read()
resume = sys.argv[3] if len(sys.argv) > 3 else ''
gitlab_branches = sys.argv[4] if len(sys.argv) > 4 else ''
result = (template
    .replace('{{IDEA_CONTENT}}', idea)
    .replace('{{RESUME_CONTEXT}}', resume)
    .replace('{{GITLAB_BRANCHES}}', gitlab_branches))
open(sys.argv[5], 'w').write(result)
" "$INSTRUCTION_TEMPLATE" "$RESOLVED_IDEA_JSON" "$RESUME_NOTE" "$GITLAB_BRANCHES" "$INSTRUCTION_OUT"

# --- Build harbor run command ---
TIMESTAMP="$(date +%Y-%m-%d__%H-%M-%S)"
JOB_NAME="${IDEA_NAME}__${TIMESTAMP}"

if [[ "$ENV_TYPE" == "native" ]]; then
    NATIVE_JOB_DIR="$SCRIPT_DIR/jobs/$JOB_NAME"
    NATIVE_WORKSPACE="$NATIVE_JOB_DIR/workspace"
    NATIVE_AGENT_DIR="$NATIVE_JOB_DIR/agent"
    mkdir -p "$NATIVE_WORKSPACE" "$NATIVE_AGENT_DIR/artifacts" "$NATIVE_JOB_DIR/verifier"
    cp -r "$ENV_DIR/paper_template" "$NATIVE_WORKSPACE/paper_template"
    cp -r "$ENV_DIR/scripts" "$NATIVE_WORKSPACE/scripts"
    cp -r "$ENV_DIR/.claude" "$NATIVE_WORKSPACE/.claude"
    mkdir -p "$NATIVE_WORKSPACE/inputs"
    [[ -d "$IDEA_INPUT_DIR" ]] && cp -rL "$IDEA_INPUT_DIR/." "$NATIVE_WORKSPACE/inputs/"
    mkdir -p "$NATIVE_WORKSPACE/figures" "$NATIVE_WORKSPACE/latex" "$NATIVE_WORKSPACE/literature" \
        "$NATIVE_WORKSPACE/submissions"
    cp -r "$ENV_DIR/paper_template/." "$NATIVE_WORKSPACE/latex/"
    cp "$ENV_DIR/.claude/skills/search-papers/search" "$NATIVE_WORKSPACE/search"
    chmod +x "$NATIVE_WORKSPACE/search" "$NATIVE_WORKSPACE/scripts/submit_for_review.sh"
    echo "${SEARCH_PUBLIC_URL:-http://localhost:8081}" > "$NATIVE_WORKSPACE/search_api_url.txt"
    if [[ -n "${PREV_ARTIFACTS:-}" ]]; then
        cp -r "$PREV_ARTIFACTS/." "$NATIVE_WORKSPACE/" 2>/dev/null || true
    fi
    cp "$INSTRUCTION_OUT" "$NATIVE_WORKSPACE/instruction.md"
    NATIVE_DATA_DIR="$(cd "$DATA_DIR" && pwd)"
    python3 - "$NATIVE_WORKSPACE" "$NATIVE_DATA_DIR" "$NATIVE_AGENT_DIR" <<'PYEOF'
import pathlib, sys
root, data, agent = map(pathlib.Path, sys.argv[1:])
for path in (p for p in root.rglob('*') if p.is_file() and p.stat().st_size < 2_000_000):
    try:
        content = path.read_text()
    except UnicodeDecodeError:
        continue
    path.write_text(content.replace('/app', str(root)).replace('/data', str(data)).replace('/logs/agent', str(agent)))
PYEOF
    git -C "$NATIVE_WORKSPACE" init -q
    git -C "$NATIVE_WORKSPACE" config user.email "ai-scientist-agent@noreply.local"
    git -C "$NATIVE_WORKSPACE" config user.name "AI Scientist Agent"
    git -C "$NATIVE_WORKSPACE" add -A && git -C "$NATIVE_WORKSPACE" commit -qm "Initialize AI Scientist workspace" || true
    CLAUDE_BIN_VALUE="${CLAUDE_SCIENTIST_BIN:-claude}"
    if [[ "$CLAUDE_BIN_VALUE" != /* ]]; then
        CLAUDE_BIN_VALUE="$(command -v "$CLAUDE_BIN_VALUE" || true)"
    fi
    if [[ -z "$CLAUDE_BIN_VALUE" || ! -x "$CLAUDE_BIN_VALUE" ]]; then
        echo "Error: Claude Code executable not found; run ../tas setup scientist --runtime native" >&2
        exit 1
    fi
    echo "Starting direct-host Claude Code run in $NATIVE_WORKSPACE"
    NATIVE_CLAUDE_PREFIX=()
    if [[ "$(id -u)" == "0" ]]; then
        NATIVE_AGENT_USER="${TAS_AGENT_USER:-nobody}"
        chown -R "$NATIVE_AGENT_USER" "$NATIVE_WORKSPACE" "$NATIVE_AGENT_DIR"
        chown -R "$NATIVE_AGENT_USER" "$DATA_DIR"
        NATIVE_CLAUDE_PREFIX=(runuser -u "$NATIVE_AGENT_USER" -- env "HOME=$NATIVE_WORKSPACE/.home")
        mkdir -p "$NATIVE_WORKSPACE/.home"
        chown "$NATIVE_AGENT_USER" "$NATIVE_WORKSPACE/.home"
    fi
    set +e
    timeout "$TIMEOUT" "${NATIVE_CLAUDE_PREFIX[@]}" "$CLAUDE_BIN_VALUE" --print --verbose --output-format stream-json \
        --permission-mode bypassPermissions --model "$MODEL" "$(cat "$NATIVE_WORKSPACE/instruction.md")" \
        > "$NATIVE_AGENT_DIR/session.jsonl" 2> "$NATIVE_AGENT_DIR/stderr.log"
    NATIVE_STATUS=$?
    set -e
    for item in experiment_codebase figures literature latex submissions; do
        [[ -e "$NATIVE_WORKSPACE/$item" ]] && cp -r "$NATIVE_WORKSPACE/$item" "$NATIVE_AGENT_DIR/artifacts/"
    done
    [[ -f "$NATIVE_WORKSPACE/latex/template.pdf" ]] && cp "$NATIVE_WORKSPACE/latex/template.pdf" "$NATIVE_AGENT_DIR/artifacts/paper.pdf"
    [[ -f "$NATIVE_WORKSPACE/latex/template.tex" ]] && cp "$NATIVE_WORKSPACE/latex/template.tex" "$NATIVE_AGENT_DIR/artifacts/paper.tex"
    echo "Native run artifacts: $NATIVE_AGENT_DIR/artifacts"
    exit "$NATIVE_STATUS"
fi

# Patch task.toml agent timeout to match the user's --timeout value.
# Harbor uses task.toml timeout_sec * --timeout-multiplier for both the agent
# and setup timeouts.  By writing the desired timeout directly into task.toml
# and keeping the multiplier at 1.0, the setup timeout stays at its default
# (360s) — enough for Docker build + agent install.
sed -i.bak "s/^timeout_sec = .*/timeout_sec = $TIMEOUT/" "$TASK_DIR/task.toml"
rm -f "$TASK_DIR/task.toml.bak"

HARBOR_ARGS=(
    harbor run
    -p "$TASK_DIR/"
    -m "$MODEL"
    --timeout-multiplier 1.0
    --agent-setup-timeout-multiplier 3.0
    -n 1
    -o "$SCRIPT_DIR/jobs/"
    --job-name "$JOB_NAME"
)

if [[ "$USE_UPSTREAM_AGENT" == "1" ]]; then
    HARBOR_ARGS+=(-a "$UPSTREAM_AGENT_FLAG")
    # Pin claude-code to the version verified end-to-end with harbor 0.1.45.
    # Newer claude-code (>= 2.1.158) hangs harbor's stream-json reader; harbor's
    # async client never drains the response and the run wedges in ep_poll on a
    # CLOSE-WAIT socket. 2.1.145 is the version captured in gold trajectories.
    if [[ "$AGENT_TYPE" == "claude-code" ]]; then
        HARBOR_ARGS+=(--ak "version=${SCIENTIST_CLAUDE_CODE_VERSION:-2.1.145}")
    fi
    # Pass LLM endpoint + keys into the sandbox so claude-code can reach the API.
    # Without --ae, harbor 0.1.45's claude-code agent reads os.environ but the
    # env vars don't flow through to the sandbox commands — the agent hangs.
    [[ -n "${ANTHROPIC_BASE_URL:-}" ]]   && HARBOR_ARGS+=(--ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL")
    [[ -n "${ANTHROPIC_API_KEY:-}" ]]    && HARBOR_ARGS+=(--ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY")
    [[ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]] && HARBOR_ARGS+=(--ae "ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN")
    [[ -n "${ANTHROPIC_MODEL:-}" ]]       && HARBOR_ARGS+=(--ae "ANTHROPIC_MODEL=$ANTHROPIC_MODEL")
    [[ -n "${ANTHROPIC_DEFAULT_OPUS_MODEL:-}" ]]   && HARBOR_ARGS+=(--ae "ANTHROPIC_DEFAULT_OPUS_MODEL=$ANTHROPIC_DEFAULT_OPUS_MODEL")
    [[ -n "${ANTHROPIC_DEFAULT_SONNET_MODEL:-}" ]] && HARBOR_ARGS+=(--ae "ANTHROPIC_DEFAULT_SONNET_MODEL=$ANTHROPIC_DEFAULT_SONNET_MODEL")
    [[ -n "${ANTHROPIC_DEFAULT_HAIKU_MODEL:-}" ]]  && HARBOR_ARGS+=(--ae "ANTHROPIC_DEFAULT_HAIKU_MODEL=$ANTHROPIC_DEFAULT_HAIKU_MODEL")
    [[ -n "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]]     && HARBOR_ARGS+=(--ae "CLAUDE_CODE_SUBAGENT_MODEL=$CLAUDE_CODE_SUBAGENT_MODEL")
    [[ -n "${CLAUDE_CODE_EFFORT_LEVEL:-}" ]]       && HARBOR_ARGS+=(--ae "CLAUDE_CODE_EFFORT_LEVEL=$CLAUDE_CODE_EFFORT_LEVEL")
    [[ -n "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-}" ]] && HARBOR_ARGS+=(--ae "CLAUDE_CODE_AUTO_COMPACT_WINDOW=$CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    [[ -n "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-}" ]] && HARBOR_ARGS+=(--ae "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=$CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
    [[ -n "${CLAUDE_CODE_ATTRIBUTION_HEADER:-}" ]] && HARBOR_ARGS+=(--ae "CLAUDE_CODE_ATTRIBUTION_HEADER=$CLAUDE_CODE_ATTRIBUTION_HEADER")
    [[ -n "${GEMINI_API_KEY:-}" ]]       && HARBOR_ARGS+=(--ae "GEMINI_API_KEY=$GEMINI_API_KEY")
    [[ -n "${REVIEWER_MODE:-}" ]]        && HARBOR_ARGS+=(--ae "REVIEWER_MODE=$REVIEWER_MODE")
else
    HARBOR_ARGS+=(--agent-import-path "$PATCHED_AGENT_IMPORT_PATH")
    HARBOR_ARGS+=(--ak "artifact_sync_interval_sec=$ARTIFACT_SYNC_INTERVAL")
fi

# Environment type
if [[ -n "$ENV_TYPE" ]]; then
    HARBOR_ARGS+=(--env "$ENV_TYPE")
fi

# GPU support for local Docker.
if [[ "$GPUS" != "0" ]]; then
    HARBOR_ARGS+=(--override-gpus "$GPUS")
fi

echo "Starting Harbor run..."
echo "  Idea:    $IDEA_JSON"
echo "  Model:   $MODEL"
echo "  Timeout: ${TIMEOUT}s"
echo "  Env:     ${ENV_TYPE:-docker}"
if [[ "$USE_UPSTREAM_AGENT" == "1" ]]; then
    echo "  Agent:   $AGENT_TYPE (upstream)"
else
    echo "  Agent:   $AGENT_TYPE (patched, local import)"
    echo "  Sync:    ${ARTIFACT_SYNC_INTERVAL}s"
fi
if [[ "$GPUS" != "0" ]]; then
    echo "  GPUs:    $GPUS"
fi
if [[ -n "$GITLAB_RESUME_BRANCH" ]]; then
    echo "  Resume:  git branch-off $GITLAB_RESUME_BRANCH"
elif [[ -n "$PREV_ARTIFACTS" ]]; then
    echo "  Resume:  $PREV_ARTIFACTS (artifact-based)"
fi
if [[ -n "$FEEDBACK" ]]; then
    echo "  Feedback: (included)"
fi
if [[ -n "$GITLAB_WEB_URL" ]]; then
    echo "  GitLab:  $GITLAB_WEB_URL"
    echo "  Branch:  $GITLAB_BRANCH"
fi
echo ""

PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" "${HARBOR_ARGS[@]}"
