"""SPECTER2 embedding — allenai/specter2_base with proximity adapter."""

from __future__ import annotations

import logging
import threading
from typing import Generator

import numpy as np

from arxiv_search_kit.exceptions import EmbeddingError

logger = logging.getLogger(__name__)

SPECTER2_BASE = "allenai/specter2_base"
SPECTER2_PROXIMITY_ADAPTER = "allenai/specter2"


class Specter2Embedder:
    """SPECTER2 embedder with proximity adapter for paper similarity."""

    def __init__(
        self,
        device: str | None = None,
        batch_size: int = 64,
        max_length: int = 512,
    ):
        import torch
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.batch_size = batch_size
        self.max_length = max_length
        self._torch = torch

        self._tokenizer = None
        self._model = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        loaded = self._model is not None
        return f"Specter2Embedder(device='{self.device}', loaded={loaded})"

    def _load_model(self) -> None:
        """Lazy-load with double-checked locking for thread safety."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from adapters import AutoAdapterModel
            from transformers import AutoTokenizer
            logger.info("Loading SPECTER2 on %s", self.device)
            self._tokenizer = AutoTokenizer.from_pretrained(SPECTER2_BASE)
            model = AutoAdapterModel.from_pretrained(SPECTER2_BASE)
            model.load_adapter(SPECTER2_PROXIMITY_ADAPTER, load_as="proximity", set_active=True)
            model = model.to(self.device)
            model.eval()
            self._model = model
            logger.info("SPECTER2 loaded")

    def warmup(self) -> None:
        """Load model and run a dummy forward pass (e.g. to compile CUDA kernels)."""
        self._load_model()
        dummy = self._tokenizer(
            ["warmup"], padding=True, truncation=True, max_length=16, return_tensors="pt"
        ).to(self.device)
        with self._torch.no_grad():
            self._model(**dummy)

    @property
    def embedding_dim(self) -> int:
        return 768

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed text strings. Returns array of shape (len(texts), 768)."""
        self._load_model()
        all_embeddings = []

        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            try:
                inputs = self._tokenizer(
                    batch, padding=True, truncation=True,
                    max_length=self.max_length, return_tensors="pt",
                ).to(self.device)
                with self._torch.no_grad():
                    outputs = self._model(**inputs)
                all_embeddings.append(outputs.last_hidden_state[:, 0, :].cpu().numpy())
            except Exception as e:
                raise EmbeddingError(f"Embedding failed at index {start}: {e}") from e

        return np.concatenate(all_embeddings, axis=0).astype(np.float32)

    def embed_paper(self, title: str, abstract: str) -> np.ndarray:
        """Embed a single paper. Returns shape (768,)."""
        return self.embed_texts([format_paper_text(title, abstract)])[0]

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a search query. Returns shape (768,)."""
        return self.embed_texts([query])[0]

    def embed_papers_iter(
        self,
        papers: Generator[dict, None, None],
        total: int | None = None,
    ) -> Generator[tuple[list[dict], np.ndarray], None, None]:
        """Embed papers from an iterator, yielding (batch_dicts, embeddings) tuples."""
        self._load_model()
        batch_papers: list[dict] = []
        processed = 0

        for paper in papers:
            batch_papers.append(paper)
            if len(batch_papers) >= self.batch_size:
                texts = [format_paper_text(p["title"], p["abstract"]) for p in batch_papers]
                embeddings = self.embed_texts(texts)
                processed += len(batch_papers)
                if total and processed % (self.batch_size * 50) == 0:
                    logger.info("Embedded %s/%s papers (%.1f%%)", f"{processed:,}", f"{total:,}", 100 * processed / total)
                yield batch_papers, embeddings
                batch_papers = []

        if batch_papers:
            texts = [format_paper_text(p["title"], p["abstract"]) for p in batch_papers]
            embeddings = self.embed_texts(texts)
            processed += len(batch_papers)
            logger.info("Embedded %s papers total", f"{processed:,}")
            yield batch_papers, embeddings


def format_paper_text(title: str, abstract: str) -> str:
    """Format for SPECTER2 input: title [SEP] abstract."""
    title = title.strip()
    abstract = abstract.strip()
    return f"{title} [SEP] {abstract}" if abstract else title


class GeminiEmbedder:
    """Gemini embedding-2-preview embedder for query-time use.

    Uses the standard (non-batch) Gemini API to embed queries and papers.
    Requires a Gemini API key via ``api_key`` or ``GEMINI_API_KEY`` env var.
    """

    def __init__(self, api_key: str | None = None):
        import os
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Gemini API key required. Pass api_key= or set GEMINI_API_KEY env var."
            )
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from google import genai
                    self._client = genai.Client(api_key=self._api_key)
        return self._client

    @property
    def embedding_dim(self) -> int:
        return 3072

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed texts using Gemini API. Returns array of shape (len(texts), 3072)."""
        client = self._get_client()
        result = client.models.embed_content(
            model="gemini-embedding-2-preview",
            contents=texts,
        )
        return np.array([e.values for e in result.embeddings], dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a search query. Returns shape (3072,)."""
        text = f"task: search result | query: {query}"
        return self.embed_texts([text])[0]

    def embed_paper(self, title: str, abstract: str) -> np.ndarray:
        """Embed a paper by title+abstract. Returns shape (3072,)."""
        title = (title or "").strip() or "none"
        abstract = (abstract or "").strip()
        if len(abstract) > 2000:
            abstract = abstract[:2000]
        text = f"title: {title} | text: {abstract}"
        return self.embed_texts([text])[0]

    def warmup(self) -> None:
        self._get_client()