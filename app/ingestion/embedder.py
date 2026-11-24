"""app/ingestion/embedder.py — Ollama embedding client for Atlas Enterprise AI.

Calls Ollama's /api/embed endpoint with nomic-embed-text (768 dimensions).
Validates vector dimensionality on every batch.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

EXPECTED_EMBEDDING_DIM = 768


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
        """Generate 768-dimensional embeddings for a list of text strings.

        Args:
            texts: List of text chunks to embed.
            batch_size: Batch size sent to /api/embed (default: 32).

        Returns:
            List of 768-dimensional float vectors matching the input texts.

        Raises:
            ValueError: If texts list is empty or an embedding dimension != expected_dim.
            RuntimeError: If the Ollama API call fails or returns unexpected data.
        """
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

                if len(batch_embeddings) != len(batch):
                    raise RuntimeError(
                        f"Ollama returned {len(batch_embeddings)} embeddings for {len(batch)} inputs."
                    )

                for idx, embedding in enumerate(batch_embeddings):
                    if len(embedding) != self.expected_dim:
                        raise ValueError(
                            f"Vector dimension mismatch: expected {self.expected_dim}, "
                            f"got {len(embedding)} for chunk index {i + idx}."
                        )

                all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def embed_query(self, query: str) -> list[float]:
        """Generate a single 768-dimensional embedding for a query string."""
        if not query.strip():
            raise ValueError("Query string cannot be empty.")
        embeddings = await self.embed_texts([query])
        return embeddings[0]


# Shared embedder instance
embedder = OllamaEmbedder()
