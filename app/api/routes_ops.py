"""app/api/routes_ops.py — Operational Health and Compliance Auditing Endpoints.

Implements Section 3.D of docs/SCHEMA_AND_API.md and Phase 4/6:
- GET /api/health: Check connectivity to PostgreSQL, S3 storage (RustFS/B2), Embeddings (Ollama/Gemini), and Langfuse.
- GET /api/audit-logs: Filter and list operational audit records with pagination.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import boto3
import httpx
import psycopg
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AuditLog
from app.db.session import get_db
from app.storage.s3_client import build_boto_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ops"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class HealthServices(BaseModel):
    """Health status per dependent service."""

    postgres: str
    rustfs: str
    ollama: str
    embed_provider: str = "ollama"
    langfuse_enabled: bool
    langfuse_host: str | None = None


class HealthResponse(BaseModel):
    """Overall system health payload."""

    status: str
    services: HealthServices


class AuditLogItem(BaseModel):
    """Individual operational audit trail entry."""

    id: uuid.UUID
    event_type: str
    actor_role: str
    session_id: str | None = None
    resource_id: str | None = None
    details: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    """List of recent audit log records."""

    audit_logs: list[AuditLogItem]


# ── Health Check Helpers ──────────────────────────────────────────────────────


async def check_postgres() -> bool:
    """Check connectivity to PostgreSQL (5s timeout handles Neon cold-wake latency)."""
    try:
        conn = await psycopg.AsyncConnection.connect(
            settings.database_url.replace("+psycopg", ""),
            connect_timeout=5,
        )
        await conn.execute("SELECT 1")
        await conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Postgres health check failed: %s", exc)
        return False


def check_rustfs() -> bool:
    """Check connectivity to S3-compatible object storage (RustFS or Backblaze B2)."""
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=build_boto_config(connect_timeout=3, max_attempts=0),
        )
        s3.head_bucket(Bucket=settings.s3_bucket_name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Object storage health check failed: %s", exc)
        return False


async def check_ollama() -> bool:
    """Check connectivity to host Ollama."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ollama health check failed: %s", exc)
        return False


def check_gemini_embeddings() -> bool:
    """Validate Gemini embedding configuration without consuming free-tier quota."""
    key = (settings.google_api_key or "").strip()
    model = (settings.gemini_embed_model or "").strip()
    return bool(key and key != "your_google_gemini_api_key_here" and model)


def _resolve_public_langfuse_url() -> str | None:
    """Return browser-accessible Langfuse URL when Langfuse is enabled."""
    if not settings.langfuse_enabled:
        return None
    host = (settings.langfuse_host or "").strip().rstrip("/")
    if not host or host.startswith("http://langfuse:"):
        return "http://localhost:3000"
    return host


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check connectivity to PostgreSQL, S3 storage, and Embeddings provider",
)
async def health_check() -> HealthResponse:
    """Check connectivity to PostgreSQL, S3 storage, and active embedding provider."""
    pg_ok = await check_postgres()
    storage_ok = check_rustfs()

    provider = settings.embed_provider.strip().lower()
    if provider == "gemini":
        embed_ok = check_gemini_embeddings()
    else:
        embed_ok = await check_ollama()

    services = HealthServices(
        postgres="up" if pg_ok else "down",
        rustfs="up" if storage_ok else "down",
        ollama="up" if embed_ok else "down",
        embed_provider=provider,
        langfuse_enabled=settings.langfuse_enabled,
        langfuse_host=_resolve_public_langfuse_url(),
    )

    all_up = pg_ok and storage_ok and embed_ok
    overall = "ok" if all_up else "degraded"

    return HealthResponse(
        status=overall,
        services=services,
    )


@router.get(
    "/audit-logs",
    response_model=AuditLogListResponse,
    status_code=status.HTTP_200_OK,
    summary="List operational audit records with optional event_type and session filters",
)
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    event_type: Annotated[str | None, Query()] = None,
    session_id: Annotated[str | None, Query()] = None,
) -> AuditLogListResponse:
    """Retrieve audit logs ordered chronologically descending."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    if session_id:
        stmt = stmt.where(AuditLog.session_id == session_id)

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    records = result.scalars().all()

    return AuditLogListResponse(audit_logs=[AuditLogItem.model_validate(r) for r in records])
