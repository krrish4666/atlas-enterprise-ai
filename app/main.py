"""app/main.py — FastAPI application entrypoint for Atlas Enterprise AI.

Responsibilities:
- Application lifespan: DB init, bucket init, Ollama readiness check.
- Mount the vanilla HTML/CSS/JS frontend at /.
- Register all API routers under /api.
- Expose GET /api/health (implemented here for Phase 0; moved to routes_ops in Phase 4).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
from app.api.routes_approvals import router as approvals_router  # noqa: E402
from app.api.routes_chat import router as chat_router  # noqa: E402
from app.api.routes_documents import router as documents_router  # noqa: E402
from app.api.routes_ops import router as ops_router  # noqa: E402

app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(approvals_router)
app.include_router(ops_router)


class LivenessResponse(BaseModel):
    """Lightweight liveness probe payload."""

    status: str = "ok"


@app.get(
    "/health",
    response_model=LivenessResponse,
    tags=["ops"],
    summary="Zero-IO container liveness probe",
)
async def root_health_check() -> LivenessResponse:
    """Return immediate HTTP 200 without probing external databases or object storage."""
    return LivenessResponse(status="ok")


# ── Static frontend (Phase 5 will populate frontend/) ─────────────────────────
# Mount last so API routes take precedence.
import os  # noqa: E402

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
