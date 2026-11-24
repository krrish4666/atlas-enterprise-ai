"""app/ingestion/pipeline.py — Document ingestion orchestrator for Atlas Enterprise AI.

Executes Flow A:
1. Fetch Document record from DB.
2. Transition status to 'processing'.
3. Download raw object bytes from RustFS via internal S3 client.
4. Extract text/pages using app.ingestion.parser.
5. Chunk text into overlapping segments with app.ingestion.chunker.
6. Generate 768-dim embeddings using host Ollama via app.ingestion.embedder.
7. Insert DocumentChunk records into pgvector.
8. Update Document status to 'indexed' and chunk_count.
9. Write audit log entry (document_indexed or document_failed).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Document, DocumentChunk
from app.db.session import async_session_maker
from app.ingestion.chunker import chunk_pages
from app.ingestion.embedder import embedder
from app.ingestion.parser import parse_document
from app.storage.s3_client import storage

logger = logging.getLogger(__name__)


async def run_ingestion_pipeline(document_id: uuid.UUID) -> None:
    """Run the complete ingestion pipeline for a given document.

    Designed to be run as a background task. Manages its own database session.
    """
    logger.info("Starting ingestion pipeline for document_id=%s", document_id)

    async with async_session_maker() as session:
        # 1. Fetch document
        stmt = select(Document).where(Document.id == document_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()

        if not doc:
            logger.error("Document not found for id=%s", document_id)
            return

        # 2. Mark processing
        doc.status = "processing"
        doc.updated_at = datetime.now(UTC)
        await session.commit()

        try:
            # 3. Download bytes from RustFS
            logger.info("Downloading object %s from RustFS", doc.object_key)
            content_bytes = storage.get_object_bytes(doc.object_key)

            # 4. Parse pages
            logger.info("Parsing document %s", doc.filename)
            pages = parse_document(content_bytes, doc.filename)

            # 5. Chunk pages
            logger.info("Chunking document %s", doc.filename)
            chunks = chunk_pages(pages, doc.filename)

            if not chunks:
                raise ValueError(f"No text chunks could be produced for '{doc.filename}'.")

            # 6. Generate embeddings via Ollama
            logger.info("Generating embeddings for %d chunks", len(chunks))
            chunk_texts = [c.content for c in chunks]
            embeddings = await embedder.embed_texts(chunk_texts)

            # 7. Insert chunks into pgvector
            chunk_records = [
                DocumentChunk(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    chunk_metadata=chunk.metadata,
                    embedding=embedding,
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
            session.add_all(chunk_records)

            # 8. Mark document as indexed
            doc.status = "indexed"
            doc.chunk_count = len(chunks)
            doc.error_message = None
            doc.updated_at = datetime.now(UTC)

            # 9. Record audit log
            audit = AuditLog(
                id=uuid.uuid4(),
                event_type="document_indexed",
                actor_role="system",
                resource_id=str(doc.id),
                details={
                    "filename": doc.filename,
                    "chunk_count": len(chunks),
                    "file_size": doc.file_size,
                },
            )
            session.add(audit)
            await session.commit()
            logger.info("Successfully indexed document_id=%s (%d chunks)", doc.id, len(chunks))

        except Exception as exc:
            logger.exception("Ingestion failed for document_id=%s: %s", doc.id, exc)
            await session.rollback()

            # Mark document as failed
            async with async_session_maker() as err_session:
                err_stmt = select(Document).where(Document.id == document_id)
                err_res = await err_session.execute(err_stmt)
                failed_doc = err_res.scalar_one_or_none()
                if failed_doc:
                    failed_doc.status = "failed"
                    failed_doc.error_message = str(exc)
                    failed_doc.updated_at = datetime.now(UTC)

                    err_audit = AuditLog(
                        id=uuid.uuid4(),
                        event_type="document_failed",
                        actor_role="system",
                        resource_id=str(failed_doc.id),
                        details={
                            "filename": failed_doc.filename,
                            "error": str(exc),
                        },
                    )
                    err_session.add(err_audit)
                    await err_session.commit()


async def ingest_document_direct(
    session: AsyncSession,
    document: Document,
    content_bytes: bytes,
) -> int:
    """Synchronously ingest a document using an existing DB session.

    Helpful for tests and scripts. Returns chunk_count.
    """
    pages = parse_document(content_bytes, document.filename)
    chunks = chunk_pages(pages, document.filename)
    if not chunks:
        raise ValueError(f"No text chunks could be produced for '{document.filename}'.")

    embeddings = await embedder.embed_texts([c.content for c in chunks])

    chunk_records = [
        DocumentChunk(
            id=uuid.uuid4(),
            document_id=document.id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            chunk_metadata=chunk.metadata,
            embedding=embedding,
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    session.add_all(chunk_records)

    document.status = "indexed"
    document.chunk_count = len(chunks)
    document.error_message = None
    document.updated_at = datetime.now(UTC)

    audit = AuditLog(
        id=uuid.uuid4(),
        event_type="document_indexed",
        actor_role="system",
        resource_id=str(document.id),
        details={
            "filename": document.filename,
            "chunk_count": len(chunks),
            "file_size": document.file_size,
        },
    )
    session.add(audit)
    await session.commit()
    return len(chunks)
