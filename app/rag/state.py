"""app/rag/state.py — LangGraph state contract for Atlas Enterprise AI.

Matches the state contract defined in docs/ARCHITECTURE.md.
"""

from typing import Any, TypedDict


class RAGState(TypedDict, total=False):
    """Unified state dictionary passed through LangGraph workflow nodes."""

    session_id: str
    user_query: str
    sanitized_query: str
    chat_history: list[dict[str, str]]  # list of {"role": "user"|"assistant", "content": str}
    input_blocked: bool
    block_reason: str | None
    is_sensitive_action: bool
    retrieved_chunks: list[dict[str, Any]]
    max_similarity_score: float
    draft_answer: str
    citations: list[dict[str, Any]]
    pii_redacted: bool
    requires_hitl: bool
    hitl_trigger_reason: str | None
    hitl_approval_id: str | None
    status: str
    final_response: str
    user_message_id: str | None
    assistant_message_id: str | None
    trace_id: str | None
