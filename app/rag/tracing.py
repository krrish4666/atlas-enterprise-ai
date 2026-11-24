"""app/rag/tracing.py — Langfuse v2 observability integration for Atlas Enterprise AI.

Creates optional Langfuse CallbackHandler for LangGraph / LangChain execution tracing.
Fails open: if Langfuse is disabled or unreachable, operations proceed without errors (ADR-006).
"""

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def get_langfuse_callback(
    session_id: str | None = None,
    trace_name: str = "atlas-rag-query",
    user_id: str | None = None,
    tags: list[str] | None = None,
) -> Any | None:
    """Return an active Langfuse CallbackHandler or None if disabled/unconfigured.

    Args:
        session_id: Associated chat session identifier.
        trace_name: Name of the Langfuse execution trace.
        user_id: Optional user identifier (e.g., employee role).
        tags: Optional list of tags for trace filtering.

    Returns:
        CallbackHandler instance or None.
    """
    if not settings.langfuse_enabled:
        return None

    # Check if credentials exist (or dummy placeholder from .env.example)
    if (
        not settings.langfuse_public_key
        or settings.langfuse_public_key == "your_langfuse_public_key"
    ):
        logger.debug("Langfuse public key not configured; skipping callback handler.")
        return None

    try:
        from langfuse.callback import CallbackHandler

        handler = CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            session_id=session_id,
            trace_name=trace_name,
            user_id=user_id,
            tags=tags or ["atlas-rag"],
        )
        return handler
    except Exception as exc:
        logger.warning("Could not initialize Langfuse CallbackHandler: %s", exc)
        return None
