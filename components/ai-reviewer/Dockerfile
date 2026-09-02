FROM node:20-slim AS claude
RUN npm install -g @anthropic-ai/claude-code@2.1.101

FROM python:3.12-slim

# pandoc: latex_to_markdown.py shells out to it
# curl: healthcheck
# git: pip install harbor (pulled from git in some deps)
# build-essential, libffi-dev: occasionally needed by pinned wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl pandoc git \
        build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Preserve the Claude Code version used by the latest reviewer runs without
# carrying npm and its build toolchain in the final image.
COPY --from=claude /usr/local/bin/node /usr/local/bin/node
COPY --from=claude /usr/local/lib/node_modules/@anthropic-ai/claude-code /opt/claude-reviewer/package
RUN ln -s /opt/claude-reviewer/package/cli.js /opt/claude-reviewer/claude && \
    chmod +x /opt/claude-reviewer/package/cli.js

# App code + skills + prompts
COPY review_api.py            /app/review_api.py
COPY benchmark_pass_at_k.py   /app/benchmark_pass_at_k.py
COPY native_reviewer.py       /app/native_reviewer.py
COPY latex_to_markdown.py     /app/latex_to_markdown.py
COPY skills/                  /app/skills/
COPY prompts/                 /app/prompts/

# search CLI must be executable inside the sandbox after upload
RUN chmod +x /app/skills/search-papers/search

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
    CMD curl -fsS http://localhost:8082/health || exit 1

# review_api.py loads .env from /app/.env if present, but env vars from the host take precedence.
ENTRYPOINT ["python", "-u", "/app/review_api.py", "--port", "8082"]
