"""app/api/routes_ops.py — Operational Health and Compliance Auditing Endpoints.

Implements Section 3.D of docs/SCHEMA_AND_API.md and Phase 4 of docs/PLAN.md:
- GET /api/health: Check connectivity to PostgreSQL, RustFS, Ollama, and Langfuse.
- GET /api/audit-logs: Filter and list operational audit records with pagination.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import boto3
import httpx
import psycopg
from botocore.config import Config
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AuditLog
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ops"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class HealthServices(BaseModel):
    """Health status per dependent service."""

    postgres: str
    rustfs: str
    ollama: str
    langfuse_enabled: bool


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
    """Check connectivity to PostgreSQL."""
    try:
        conn = await psycopg.AsyncConnection.connect(
            settings.database_url.replace("+psycopg", ""),
            connect_timeout=2,
        )
        await conn.execute("SELECT 1")
        await conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Postgres health check failed: %s", exc)
        return False


def check_rustfs() -> bool:
    """Check connectivity to RustFS."""
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(connect_timeout=2, retries={"max_attempts": 0}),
        )
        s3.list_buckets()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("RustFS health check failed: %s", exc)
        return False


async def check_ollama() -> bool:
    """Check connectivity to Ollama."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ollama health check failed: %s", exc)
        return False


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check connectivity to PostgreSQL, RustFS, and Ollama",
)
async def health_check() -> HealthResponse:
    """Check connectivity to PostgreSQL, RustFS, and Ollama."""
    pg_ok = await check_postgres()
    rustfs_ok = check_rustfs()
    ollama_ok = await check_ollama()

    services = HealthServices(
        postgres="up" if pg_ok else "down",
        rustfs="up" if rustfs_ok else "down",
        ollama="up" if ollama_ok else "down",
        langfuse_enabled=settings.langfuse_enabled,
    )

    all_up = pg_ok and rustfs_ok and ollama_ok
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
