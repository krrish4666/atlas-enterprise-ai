"""app/ingestion/embedder.py — Pluggable embedding clients (Ollama & Gemini).

Supports two profiles while keeping a single 768-dimensional pgvector contract:
- Local Dev (`EMBED_PROVIDER=ollama`): Host Ollama `/api/embed` with `nomic-embed-text`.
- Cloud Demo (`EMBED_PROVIDER=gemini`): Gemini API `gemini-embedding-001` with `output_dimensionality=768`.
"""

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

EXPECTED_EMBEDDING_DIM = 768
GEMINI_DEFAULT_BATCH_SIZE = 50
GEMINI_MAX_BATCH_SIZE = 100
GEMINI_MAX_RETRY_ATTEMPTS = 4
GEMINI_FALLBACK_RETRY_SECONDS = 30
GEMINI_MAX_RETRY_SECONDS = 65

_RETRY_DELAY_RE = re.compile(
    r"(?:retry\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*s|seconds:\s*(\d+(?:\.\d+)?))",
    re.IGNORECASE,
)

T = TypeVar("T")


class EmbedderProtocol(Protocol):
    """Common async embedding interface used by ingestion and retrieval."""

    async def embed_texts(self, texts: list[str], batch_size: int = 32) -> list[list[float]]: ...

    async def embed_query(self, query: str) -> list[float]: ...


def _validate_vectors(
    embeddings: list[list[float]],
    expected_count: int,
    expected_dim: int,
    provider_name: str,
    offset: int = 0,
) -> None:
    """Validate count and vector dimensions for a batch of embeddings."""
    if len(embeddings) != expected_count:
        raise RuntimeError(
            f"{provider_name} returned {len(embeddings)} embeddings for {expected_count} inputs."
        )
    for idx, vec in enumerate(embeddings):
        if len(vec) != expected_dim:
            raise ValueError(
                f"Vector dimension mismatch: expected {expected_dim}, "
                f"got {len(vec)} for chunk index {offset + idx}."
            )


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect Gemini 429 / ResourceExhausted / GoogleGenerativeAIError quota errors."""
    if isinstance(exc, ValueError):
        return False

    exc_type = type(exc).__name__
    msg = str(exc).lower()
    rate_limit_markers = (
        "429",
        "resource_exhausted",
        "resourceexhausted",
        "rate limit",
        "rate_limit",
        "too many requests",
        "quota",
        "retry in",
    )
    if exc_type in ("ResourceExhausted", "TooManyRequests"):
        return True
    if exc_type == "GoogleGenerativeAIError":
        return any(m in msg for m in rate_limit_markers) or "embed" in msg
    return any(m in msg for m in rate_limit_markers)


def _parse_retry_delay_seconds(message: str) -> int:
    """Parse retry delay from Gemini error text: ceil(delay)+2, fallback 30s, max 65s."""
    match = _RETRY_DELAY_RE.search(message)
    if match:
        raw_val = match.group(1) or match.group(2)
        delay = math.ceil(float(raw_val)) + 2
    else:
        delay = GEMINI_FALLBACK_RETRY_SECONDS
    return max(1, min(delay, GEMINI_MAX_RETRY_SECONDS))


async def _retry_on_rate_limit(
    fn: Callable[[], Awaitable[T]],
    context: str,
    max_attempts: int = GEMINI_MAX_RETRY_ATTEMPTS,
) -> T:
    """Execute an async Gemini embedding call with retry-after sleep on 429 rate limits."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if not _is_rate_limit_error(exc) or attempt >= max_attempts:
                raise
            sleep_seconds = _parse_retry_delay_seconds(str(exc))
            logger.warning(
                "Gemini embedding rate limit hit (%s, attempt %d/%d); sleeping %ds before retry: %s",
                context,
                attempt,
                max_attempts,
                sleep_seconds,
                exc,
            )
            await asyncio.sleep(sleep_seconds)
    raise RuntimeError("Unreachable retry loop exit")


class OllamaEmbedder:
    """Embedder client for host Ollama nomic-embed-text."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        expected_dim: int = EXPECTED_EMBEDDING_DIM,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.embed_model
        self.expected_dim = expected_dim or settings.embed_dim
        self.timeout = timeout_seconds

    async def embed_texts(
        self,
        texts: list[str],
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Generate 768-dimensional embeddings via Ollama /api/embed."""
        if not texts:
            return []

        all_embeddings: list[list[float]] = []

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                payload = {
                    "model": self.model,
                    "input": batch,
                }

                try:
                    resp = await client.post(
                        f"{self.base_url}/api/embed",
                        json=payload,
                    )
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.error("Ollama /api/embed request failed: %s", exc)
                    raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc

                data = resp.json()
                batch_embeddings = data.get("embeddings", [])
                _validate_vectors(
                    batch_embeddings,
                    expected_count=len(batch),
                    expected_dim=self.expected_dim,
                    provider_name="Ollama",
                    offset=i,
                )
                all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def embed_query(self, query: str) -> list[float]:
        """Generate a single 768-dimensional embedding for a query string."""
        if not query.strip():
            raise ValueError("Query string cannot be empty.")
        embeddings = await self.embed_texts([query])
        return embeddings[0]


class GeminiEmbedder:
    """Embedder client for Google Gemini API (`gemini-embedding-001` @ 768 dims)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        expected_dim: int = EXPECTED_EMBEDDING_DIM,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.google_api_key
        self.model = model or settings.gemini_embed_model
        self.expected_dim = expected_dim or settings.embed_dim
        self._client = None

    def _get_client(self):  # noqa: ANN202
        if self._client is None:
            if not self.api_key or not self.api_key.strip():
                raise RuntimeError("GOOGLE_API_KEY is required when EMBED_PROVIDER=gemini.")
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            self._client = GoogleGenerativeAIEmbeddings(
                model=self.model,
                google_api_key=self.api_key,
            )
        return self._client

    async def embed_texts(
        self,
        texts: list[str],
        batch_size: int = GEMINI_DEFAULT_BATCH_SIZE,
    ) -> list[list[float]]:
        """Generate 768-dimensional embeddings via Gemini API in bounded, retryable batches."""
        if not texts:
            return []

        effective_batch_size = max(1, min(batch_size, GEMINI_MAX_BATCH_SIZE))
        client = self._get_client()
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), effective_batch_size):
            batch = texts[i : i + effective_batch_size]
            batch_embeddings = await _retry_on_rate_limit(
                lambda b=batch: client.aembed_documents(
                    b,
                    batch_size=effective_batch_size,
                    output_dimensionality=self.expected_dim,
                ),
                context=f"batch {i}-{i + len(batch)}",
            )
            _validate_vectors(
                batch_embeddings,
                expected_count=len(batch),
                expected_dim=self.expected_dim,
                provider_name="Gemini",
                offset=i,
            )
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def embed_query(self, query: str) -> list[float]:
        """Generate a single 768-dimensional query embedding via Gemini API."""
        if not query.strip():
            raise ValueError("Query string cannot be empty.")

        client = self._get_client()
        vector = await _retry_on_rate_limit(
            lambda: client.aembed_query(
                query,
                output_dimensionality=self.expected_dim,
            ),
            context="query",
        )
        _validate_vectors(
            [vector],
            expected_count=1,
            expected_dim=self.expected_dim,
            provider_name="Gemini",
        )
        return vector


def get_embedder(provider: str | None = None) -> OllamaEmbedder | GeminiEmbedder:
    """Return the configured embedding client based on EMBED_PROVIDER."""
    selected = (provider or settings.embed_provider).strip().lower()
    if selected == "ollama":
        return OllamaEmbedder()
    if selected == "gemini":
        return GeminiEmbedder()
    raise ValueError(f"Unsupported EMBED_PROVIDER '{selected}'. Expected 'ollama' or 'gemini'.")


class ConfiguredEmbedder:
    """Delegating proxy that dynamically routes calls to the active provider."""

    async def embed_texts(
        self,
        texts: list[str],
        batch_size: int | None = None,
    ) -> list[list[float]]:
        """Embed text chunks using the currently configured EMBED_PROVIDER."""
        instance = get_embedder()
        if batch_size is None:
            return await instance.embed_texts(texts)
        return await instance.embed_texts(texts, batch_size=batch_size)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a query string using the currently configured EMBED_PROVIDER."""
        return await get_embedder().embed_query(query)


# Shared embedder proxy imported by ingestion pipeline & RAG graph
embedder = ConfiguredEmbedder()
