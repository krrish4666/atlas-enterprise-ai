"""app/main.py — FastAPI application entrypoint for Atlas Enterprise AI.

Responsibilities:
- Application lifespan: DB init, bucket init, Ollama readiness check.
- Mount the vanilla HTML/CSS/JS frontend at /.
- Register all API routers under /api.
- Expose GET /api/health (implemented here for Phase 0; moved to routes_ops in Phase 4).
"""

import logging
from contextlib import asynccontextmanager

import boto3
import httpx
import psycopg
from botocore.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """Startup and shutdown logic."""
    logger.info("Atlas Enterprise AI starting up (env=%s)…", settings.app_env)

    if settings.app_env != "test":
        # Initialize PostgreSQL schema and pgvector extension
        try:
            from app.db.init_db import init_db

            await init_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not initialize DB on startup: %s", exc)

        # Initialize RustFS bucket and CORS
        try:
            from app.storage.s3_client import storage

            storage.init_storage()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not initialize RustFS storage on startup: %s", exc)

    yield

    logger.info("Atlas Enterprise AI shutting down.")


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Atlas Enterprise AI",
    description="Zero-cost, self-hosted Enterprise RAG assistant.",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
from app.api.routes_chat import router as chat_router  # noqa: E402
from app.api.routes_documents import router as documents_router  # noqa: E402

app.include_router(documents_router)
app.include_router(chat_router)


# ── Health check helpers ──────────────────────────────────────────────────────


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


# ── Health endpoint (Phase 0 skeleton) ────────────────────────────────────────


@app.get("/api/health", tags=["ops"])
async def health_check() -> JSONResponse:
    """Check connectivity to PostgreSQL, RustFS, and Ollama."""
    pg_ok = await check_postgres()
    rustfs_ok = check_rustfs()
    ollama_ok = await check_ollama()

    services: dict[str, str | bool] = {
        "postgres": "up" if pg_ok else "down",
        "rustfs": "up" if rustfs_ok else "down",
        "ollama": "up" if ollama_ok else "down",
        "langfuse_enabled": settings.langfuse_enabled,
    }

    all_up = pg_ok and rustfs_ok and ollama_ok
    overall = "ok" if all_up else "degraded"

    return JSONResponse(
        content={
            "status": overall,
            "services": services,
        },
        status_code=200,
    )


# ── Static frontend (Phase 5 will populate frontend/) ─────────────────────────
# Mount last so API routes take precedence.
import os  # noqa: E402

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
