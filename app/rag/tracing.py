"""app/rag/tracing.py — Langfuse SDK v4 (OpenTelemetry) observability integration.

Creates optional Langfuse v4 CallbackHandler for LangGraph / LangChain OTel tracing.
Supports both Langfuse Cloud and self-hosted Langfuse v3.63+/v4 via LANGFUSE_HOST.
Fails open: if Langfuse is disabled, unconfigured, or unreachable, requests proceed normally.
"""

import logging
import uuid
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Cache initialized SDK v4 client keys to avoid re-creating OTel TracerProviders per request
_INITIALIZED_CLIENTS: dict[tuple[str, str, str], Any] = {}


def _is_langfuse_configured() -> bool:
    """Return True when Langfuse is enabled and non-placeholder credentials exist."""
    if not settings.langfuse_enabled:
        return False

    pk = (settings.langfuse_public_key or "").strip()
    sk = (settings.langfuse_secret_key or "").strip()
    if not pk or not sk:
        return False
    return not (pk.startswith("your_langfuse_") or sk.startswith("your_langfuse_"))


def create_otel_trace_id() -> str:
    """Generate a valid 32-character lowercase hex OpenTelemetry trace ID."""
    try:
        from langfuse import Langfuse

        return Langfuse.create_trace_id()
    except Exception:  # noqa: BLE001
        return uuid.uuid4().hex


def _ensure_langfuse_client() -> Any | None:
    """Initialize and register the Langfuse SDK v4 singleton client from settings."""
    if not _is_langfuse_configured():
        return None

    pk = settings.langfuse_public_key.strip()
    sk = settings.langfuse_secret_key.strip()
    host = (settings.langfuse_host or "https://cloud.langfuse.com").strip().rstrip("/")
    cache_key = (pk, sk, host)

    if cache_key in _INITIALIZED_CLIENTS:
        return _INITIALIZED_CLIENTS[cache_key]

    from langfuse import Langfuse

    client = Langfuse(
        public_key=pk,
        secret_key=sk,
        base_url=host,
    )
    _INITIALIZED_CLIENTS[cache_key] = client
    return client


def build_langfuse_metadata(
    session_id: str | None = None,
    trace_name: str = "atlas-rag-query",
    user_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build LangChain RunnableConfig metadata parsed by Langfuse v4 CallbackHandler."""
    meta: dict[str, Any] = {
        "langfuse_trace_name": trace_name,
        "langfuse_tags": tags or ["atlas-rag"],
    }
    if session_id:
        meta["langfuse_session_id"] = session_id
    if user_id:
        meta["langfuse_user_id"] = user_id
    return meta


def get_langfuse_callback(
    session_id: str | None = None,
    trace_name: str = "atlas-rag-query",
    user_id: str | None = None,
    tags: list[str] | None = None,
    trace_id: str | None = None,
) -> Any | None:
    """Return an active Langfuse v4 OTel CallbackHandler or None if disabled/unconfigured.

    Note: Session, user, trace name, and tag attributes are attached via RunnableConfig
    `metadata` (see `build_langfuse_metadata`) per Langfuse SDK v4 conventions.
    """
    if not _is_langfuse_configured():
        logger.debug("Langfuse disabled or credentials unconfigured; skipping callback handler.")
        return None

    try:
        from langfuse.langchain import CallbackHandler

        _ensure_langfuse_client()
        trace_context = {"trace_id": trace_id} if trace_id else None
        handler = CallbackHandler(
            public_key=settings.langfuse_public_key.strip(),
            trace_context=trace_context,
        )
        return handler
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not initialize Langfuse v4 CallbackHandler: %s", exc)
        return None


def flush_langfuse() -> None:
    """Flush pending OTel spans to Langfuse Cloud without failing the caller."""
    if not _is_langfuse_configured():
        return

    try:
        from langfuse import get_client

        client = get_client(public_key=settings.langfuse_public_key.strip())
        client.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to flush Langfuse spans: %s", exc)
