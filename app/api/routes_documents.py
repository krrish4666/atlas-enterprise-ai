"""app/api/routes_documents.py — Document management API endpoints.

Implements Flow A and Flow D from docs/ARCHITECTURE.md and docs/SCHEMA_AND_API.md:
- POST /api/documents/upload-url: Generate direct RustFS presigned PUT URL
- POST /api/documents/{document_id}/ingest: Trigger background ingestion
- GET /api/documents: List all documents
- GET /api/documents/{document_id}: Retrieve single document details
- DELETE /api/documents/{document_id}: Atomic deletion of RustFS object + DB chunks
"""

import os
import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Document
from app.db.session import get_db
from app.ingestion.parser import SUPPORTED_EXTENSIONS
from app.ingestion.pipeline import run_ingestion_pipeline
from app.storage.s3_client import storage

router = APIRouter(prefix="/api/documents", tags=["documents"])


# ── Pydantic Request & Response Models ────────────────────────────────────────


class UploadURLRequest(BaseModel):
    """Payload to request a presigned PUT upload URL."""

    filename: str = Field(..., min_length=1, max_length=255, description="Original filename")
    content_type: str = Field(..., min_length=1, max_length=128, description="MIME type")
    file_size: int = Field(..., gt=0, description="File size in bytes")


class UploadURLResponse(BaseModel):
    """Response containing the presigned upload URL and metadata."""

    document_id: uuid.UUID
    object_key: str
    upload_url: str
    expires_in: int = 300


class IngestResponse(BaseModel):
    """Response when ingestion is accepted into the background queue."""

    document_id: uuid.UUID
    status: str = "processing"


class DocumentItem(BaseModel):
    """Document summary item for list response."""

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    """List of documents response."""

    documents: list[DocumentItem]


class DocumentDetailResponse(BaseModel):
    """Detailed single document response."""

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    object_key: str
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentDeleteResponse(BaseModel):
    """Response confirming document deletion."""

    deleted: bool
    document_id: uuid.UUID


# ── Helper Utilities ─────────────────────────────────────────────────────────


def _sanitize_filename(filename: str) -> str:
    """Strip dangerous path elements and normalize filename."""
    base = os.path.basename(filename)
    sanitized = re.sub(r"[^\w\s\.-]", "_", base).strip()
    return sanitized or "document"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/upload-url",
    response_model=UploadURLResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a direct RustFS presigned PUT URL",
)
async def request_upload_url(
    req: UploadURLRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadURLResponse:
    """Validate file type, create pending Document record, and sign direct upload URL."""
    ext = os.path.splitext(req.filename.lower())[1]
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file format '{ext}'. Allowed extensions: "
                f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    doc_id = uuid.uuid4()
    clean_name = _sanitize_filename(req.filename)
    object_key = f"uploads/{doc_id}/{clean_name}"

    # Generate presigned PUT URL using the public client reachable by browser
    upload_url = storage.generate_presigned_put_url(
        object_key=object_key,
        content_type=req.content_type,
        expires_in=300,
    )

    # Persist pending record in Postgres
    doc = Document(
        id=doc_id,
        filename=clean_name,
        content_type=req.content_type,
        file_size=req.file_size,
        object_key=object_key,
        status="pending_upload",
        chunk_count=0,
    )
    db.add(doc)

    # Record operational audit trail
    audit = AuditLog(
        id=uuid.uuid4(),
        event_type="document_upload_requested",
        actor_role="employee",
        resource_id=str(doc_id),
        details={
            "filename": clean_name,
            "content_type": req.content_type,
            "file_size": req.file_size,
        },
    )
    db.add(audit)
    await db.commit()

    return UploadURLResponse(
        document_id=doc_id,
        object_key=object_key,
        upload_url=upload_url,
        expires_in=300,
    )


@router.post(
    "/{document_id}/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger ingestion pipeline for an uploaded document",
)
async def trigger_ingestion(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IngestResponse:
    """Verify document exists and queue background extraction, chunking, and embedding."""
    stmt = select(Document).where(Document.id == document_id)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    # Mark status as processing
    doc.status = "processing"
    await db.commit()

    # Enqueue background pipeline task
    background_tasks.add_task(run_ingestion_pipeline, doc.id)

    return IngestResponse(
        document_id=doc.id,
        status="processing",
    )


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all documents ordered by creation date",
)
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentListResponse:
    """Return all uploaded documents ordered newest first."""
    stmt = select(Document).order_by(Document.created_at.desc())
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return DocumentListResponse(documents=[DocumentItem.model_validate(doc) for doc in docs])


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Retrieve single document status and metadata",
)
async def get_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentDetailResponse:
    """Return metadata and current status for a single document (for UI polling)."""
    stmt = select(Document).where(Document.id == document_id)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    return DocumentDetailResponse.model_validate(doc)


@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    summary="Atomically delete a document, its RustFS object, and all vector chunks",
)
async def delete_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentDeleteResponse:
    """Delete raw file from RustFS and remove Document row (atomically cascades chunks)."""
    stmt = select(Document).where(Document.id == document_id)
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    # 1. Delete object from RustFS (idempotent; ignore 404)
    storage.delete_object(doc.object_key)

    # 2. Record audit log before deleting record
    audit = AuditLog(
        id=uuid.uuid4(),
        event_type="document_deleted",
        actor_role="employee",
        resource_id=str(document_id),
        details={"filename": doc.filename},
    )
    db.add(audit)

    # 3. Delete DB record (PostgreSQL ON DELETE CASCADE deletes document_chunks)
    await db.delete(doc)
    await db.commit()

    return DocumentDeleteResponse(
        deleted=True,
        document_id=document_id,
    )
