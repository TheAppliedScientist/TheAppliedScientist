"""Custom exceptions for arxiv_search_kit."""


class OpenArxivError(Exception):
    """Base exception for all arxiv_search_kit errors."""


class IndexNotFoundError(OpenArxivError):
    """Raised when the LanceDB index directory doesn't exist or is invalid."""


class IndexBuildError(OpenArxivError):
    """Raised when index building fails."""


class PaperNotFoundError(OpenArxivError):
    """Raised when a paper is not found in the index."""


class EmbeddingError(OpenArxivError):
    """Raised when SPECTER2 embedding computation fails."""


class EnrichmentError(OpenArxivError):
    """Raised when Semantic Scholar enrichment fails."""


class DownloadError(OpenArxivError):
    """Raised when metadata download fails."""


class RateLimitError(OpenArxivError):
    """Raised when an API rate limit is hit."""


class SummarizationError(OpenArxivError):
    """Raised when paper summarization fails."""