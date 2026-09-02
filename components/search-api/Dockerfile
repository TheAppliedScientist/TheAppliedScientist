FROM python:3.12-slim

# System deps: lancedb needs libgomp, httpx needs SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libgomp1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The published service uses Gemini embeddings. SPECTER2 dependencies are loaded
# lazily only when rebuilding an index, so the serving image stays small.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY arxiv_search_kit/ /app/arxiv_search_kit/
COPY search_api.py /app/search_api.py

# The index lives in a volume so the ~10 GB download persists across restarts.
ENV ARXIV_SEARCH_KIT_CACHE=/data/arxiv_search_kit \
    HF_HOME=/data/hf

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8081

# /health is reachable as soon as the server binds; long start_period covers first-run index download.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=900s \
    CMD curl -fsS http://localhost:8081/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
