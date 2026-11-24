"""app/rag/retriever.py — pgvector similarity search for Atlas Enterprise AI.

Executes index-accelerated cosine distance queries over document_chunks
using the HNSW cosine index (vector_cosine_ops).
Returns top-K chunks with similarity scores in [-1, 1], filenames, and metadata.
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Document, DocumentChunk

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """Represents a grounded chunk retrieved from pgvector."""

    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    score: float
    snippet: str


async def retrieve_similar_chunks(
    db: AsyncSession,
    query_vector: list[float],
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Retrieve top-K most similar document chunks using pgvector cosine distance.

    Args:
        db: Active SQLAlchemy AsyncSession.
        query_vector: 768-dimensional float embedding of the user query.
        top_k: Number of chunks to retrieve (defaults to settings.retrieval_top_k).

    Returns:
        List of RetrievedChunk objects ordered by cosine similarity descending.
    """
    k = top_k or settings.retrieval_top_k

    # Cosine similarity in pgvector: 1.0 - (embedding <=> :query_vector)
    # Cosine distance: DocumentChunk.embedding.cosine_distance(query_vector)
    score_expr = (1.0 - DocumentChunk.embedding.cosine_distance(query_vector)).label("score")

    stmt = (
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.filename,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            DocumentChunk.chunk_metadata,
            score_expr,
        )
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(Document.status == "indexed")
        .order_by(DocumentChunk.embedding.cosine_distance(query_vector))
        .limit(k)
    )

    result = await db.execute(stmt)
    rows = result.all()

    chunks: list[RetrievedChunk] = []
    for row in rows:
        content_text = row.content or ""
        # Create a snippet for citations (first ~200 characters)
        snippet = content_text[:200].strip()
        if len(content_text) > 200:
            snippet += "..."

        score_val = float(row.score) if row.score is not None else 0.0

        chunks.append(
            RetrievedChunk(
                chunk_id=str(row.id),
                document_id=str(row.document_id),
                filename=row.filename,
                chunk_index=row.chunk_index,
                content=content_text,
                metadata=row.chunk_metadata or {},
                score=round(score_val, 4),
                snippet=snippet,
            )
        )

    logger.info("Retrieved %d chunks (top_k=%d)", len(chunks), k)
    return chunks
