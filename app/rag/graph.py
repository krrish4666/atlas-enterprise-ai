"""app/rag/graph.py — Full LangGraph RAG Workflow for Atlas Enterprise AI.

Topology matching docs/ARCHITECTURE.md:
START
  ▼
[load_memory]          Load last N turns for session_id from PostgreSQL
  │
  ▼
[input_guardrail]      Deterministic check: prompt injection / blocked topics / sensitive actions / PII
  ├── (blocked) ──────► [blocked_response] ──► [persist_turn] ──► END
  │
  ▼ (allowed)
[retrieve]             Embed query via Ollama -> pgvector cosine similarity search (top-K chunks + scores)
  │
  ▼
[generate]             Send prompt + history + retrieved chunks to Gemini API -> answer + citations
  │
  ▼
[output_guardrail]     Deterministic PII scanner & redactor (emails, phones, SSNs, credit cards, keys)
  │
  ▼
[hitl_check]           Evaluate HITL triggers: sensitive flag OR max_score < HITL_CONFIDENCE_THRESHOLD
  ├── (needs_review) ─► [queue_hitl] (write pending approval to Postgres) ──► [persist_turn] ──► END
  │
  ▼ (approved)
[persist_turn]         Save user + assistant turns to chat_messages & log events to audit_logs
  │
  ▼
END
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AuditLog, ChatMessage, HITLApproval
from app.db.session import async_session_maker
from app.ingestion.embedder import embedder
from app.rag.generator import generate_answer
from app.rag.guardrails import evaluate_input, evaluate_output
from app.rag.retriever import retrieve_similar_chunks
from app.rag.state import RAGState
from app.rag.tracing import (
    build_langfuse_metadata,
    create_otel_trace_id,
    flush_langfuse,
    get_langfuse_callback,
)

logger = logging.getLogger(__name__)


# ── Graph Nodes ───────────────────────────────────────────────────────────────


async def load_memory_node(state: RAGState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Load the last N conversation turns for session_id from PostgreSQL."""
    session_id = state.get("session_id", "")
    if not session_id:
        return {"chat_history": []}

    configurable = (config or {}).get("configurable", {})
    session_db = configurable.get("db")

    async def _query_history(db: AsyncSession) -> list[dict[str, str]]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(
                ChatMessage.created_at.desc(),
                case((ChatMessage.role == "user", 1), else_=0),
                ChatMessage.id.desc(),
            )
            .limit(2)
        )
        result = await db.execute(stmt)
        messages = list(result.scalars().all())
        # Keep only the last 2 messages in chronological order (user before assistant on ties)
        messages.sort(
            key=lambda m: (
                m.created_at or datetime.min.replace(tzinfo=UTC),
                0 if m.role == "user" else 1,
            )
        )
        recent = messages[-2:] if len(messages) > 2 else messages
        return [{"role": m.role, "content": m.content} for m in recent]

    if session_db:
        history = await _query_history(session_db)
    else:
        async with async_session_maker() as db:
            history = await _query_history(db)

    return {"chat_history": history}


async def input_guardrail_node(
    state: RAGState, _config: RunnableConfig | None = None
) -> dict[str, Any]:
    """Deterministic check: prompt injection / blocked topics / sensitive actions / PII."""
    raw_query = state.get("user_query", "")
    res = evaluate_input(raw_query)

    if res.is_blocked:
        return {
            "input_blocked": True,
            "block_reason": res.block_reason,
            "is_sensitive_action": False,
            "sanitized_query": res.sanitized_query,
            "pii_redacted": res.pii_redacted,
            "final_response": res.policy_response or "Request blocked by safety guardrails.",
            "status": "blocked",
        }

    return {
        "input_blocked": False,
        "block_reason": None,
        "is_sensitive_action": res.is_sensitive_action,
        "sanitized_query": res.sanitized_query,
        "pii_redacted": res.pii_redacted,
    }


async def blocked_response_node(
    state: RAGState, _config: RunnableConfig | None = None
) -> dict[str, Any]:
    """Format safe message when request is blocked by input guardrails."""
    reason = state.get("block_reason") or "unspecified_safety_violation"
    existing_resp = state.get("final_response")
    msg = (
        existing_resp
        if existing_resp
        else f"I cannot process this request because it violates safety policies: {reason}."
    )
    return {
        "final_response": msg,
        "status": "blocked",
        "requires_hitl": False,
        "citations": [],
        "draft_answer": msg,
    }


async def retrieve_node(state: RAGState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Embed the user query via Ollama and retrieve top-K grounded chunks from pgvector."""
    query = state.get("sanitized_query") or state.get("user_query", "")
    if not query.strip():
        return {"retrieved_chunks": [], "max_similarity_score": 0.0}

    # Generate 768-dim query embedding using native Ollama
    query_vector = await embedder.embed_query(query)

    configurable = (config or {}).get("configurable", {})
    session_db = configurable.get("db")

    async def _do_retrieve(db: AsyncSession) -> list[dict[str, Any]]:
        chunks = await retrieve_similar_chunks(
            db=db,
            query_vector=query_vector,
            top_k=settings.retrieval_top_k,
        )
        return [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "filename": c.filename,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "metadata": c.metadata,
                "score": c.score,
                "snippet": c.snippet,
            }
            for c in chunks
        ]

    if session_db:
        retrieved = await _do_retrieve(session_db)
    else:
        async with async_session_maker() as db:
            retrieved = await _do_retrieve(db)

    max_score = max([c["score"] for c in retrieved], default=0.0)
    return {
        "retrieved_chunks": retrieved,
        "max_similarity_score": round(max_score, 4),
    }


async def generate_node(state: RAGState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Synthesize a grounded answer using Google Gemini API with inline citations."""
    query = state.get("sanitized_query") or state.get("user_query", "")
    retrieved = state.get("retrieved_chunks", [])
    history = state.get("chat_history", [])

    configurable = (config or {}).get("configurable", {})
    callbacks = configurable.get("callbacks")
    custom_llm = configurable.get("llm")

    answer, citations = await generate_answer(
        query=query,
        retrieved_chunks=retrieved,
        chat_history=history,
        callbacks=callbacks,
        llm=custom_llm,
    )

    return {
        "draft_answer": answer,
        "final_response": answer,
        "citations": citations,
    }


async def output_guardrail_node(
    state: RAGState, _config: RunnableConfig | None = None
) -> dict[str, Any]:
    """Scan and redact accidental PII in generated answer."""
    draft = state.get("draft_answer", "")
    sanitized, was_redacted = evaluate_output(draft)
    prior_redacted = state.get("pii_redacted", False)

    return {
        "draft_answer": sanitized,
        "final_response": sanitized,
        "pii_redacted": prior_redacted or was_redacted,
    }


async def hitl_check_node(state: RAGState, _config: RunnableConfig | None = None) -> dict[str, Any]:
    """Evaluate HITL triggers: sensitive action flag OR retrieval score < threshold."""
    is_sensitive = state.get("is_sensitive_action", False)
    max_score = state.get("max_similarity_score", 0.0)
    threshold = settings.hitl_confidence_threshold

    requires_hitl = False
    trigger_reason = None

    if is_sensitive:
        requires_hitl = True
        trigger_reason = "sensitive_action_request"
    elif max_score < threshold:
        requires_hitl = True
        trigger_reason = f"low_retrieval_confidence ({max_score:.2f} < {threshold:.2f})"

    return {
        "requires_hitl": requires_hitl,
        "hitl_trigger_reason": trigger_reason,
    }


async def queue_hitl_node(state: RAGState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Insert pending approval ticket into hitl_approvals table in PostgreSQL."""
    session_id = state.get("session_id", "default")
    user_query = state.get("user_query", "")
    draft_answer = state.get("draft_answer", "")
    citations = state.get("citations", [])
    trigger_reason = state.get("hitl_trigger_reason") or "manual_review"
    max_score = state.get("max_similarity_score", 0.0)
    is_sensitive = (
        state.get("is_sensitive_action", False) or trigger_reason == "sensitive_action_request"
    )

    # For sensitive action requests (fund transfers, admin changes, termination) where
    # document retrieval returned a generic "I could not find information..." fallback,
    # supply a clear action authorization draft so the reviewer and user see a meaningful response.
    if is_sensitive and (
        not draft_answer
        or "could not find information" in draft_answer.lower()
        or draft_answer == "No relevant documents found."
    ):
        draft_answer = (
            "This action request involves high-stakes operations (e.g. fund disbursement, "
            "credential modification, or personnel decision) and cannot be executed autonomously. "
            "It has been queued for human reviewer authorization."
        )

    approval_id = uuid.uuid4()

    configurable = (config or {}).get("configurable", {})
    session_db = configurable.get("db")

    async def _insert_hitl(db: AsyncSession) -> None:
        rec = HITLApproval(
            id=approval_id,
            session_id=session_id,
            message_id=None,  # Linked upon assistant message creation in persist_turn_node
            user_query=user_query,
            draft_answer=draft_answer,
            citations=citations,
            trigger_reason=trigger_reason,
            max_similarity_score=max_score,
            status="pending",
        )
        db.add(rec)
        await db.flush()

    if session_db:
        await _insert_hitl(session_db)
    else:
        async with async_session_maker() as db:
            await _insert_hitl(db)
            await db.commit()

    return {
        "requires_hitl": True,
        "hitl_approval_id": str(approval_id),
        "status": "pending_approval",
        "draft_answer": draft_answer,
        "final_response": "Your request has been routed to a human reviewer for approval before execution.",
    }


async def persist_turn_node(
    state: RAGState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """Persist user prompt and assistant response into chat_messages and audit_logs."""
    session_id = state.get("session_id", "default")
    user_query = state.get("user_query", "")
    citations = state.get("citations", [])
    max_score = state.get("max_similarity_score", 0.0)
    trace_id = state.get("trace_id")
    input_blocked = state.get("input_blocked", False)
    requires_hitl = state.get("requires_hitl", False)
    pii_redacted = state.get("pii_redacted", False)
    hitl_approval_id = state.get("hitl_approval_id")

    # Determine status and final response text
    if input_blocked:
        msg_status = "blocked"
        final_response = state.get("final_response") or "Request blocked by safety guardrails."
    elif requires_hitl:
        msg_status = "pending_approval"
        final_response = (
            state.get("final_response")
            or "Your request has been routed to a human reviewer for approval before execution."
        )
    else:
        msg_status = "completed"
        final_response = state.get("draft_answer") or state.get("final_response", "")

    user_msg_id = uuid.uuid4()
    assistant_msg_id = uuid.uuid4()
    # Set explicit timestamps so user always strictly precedes assistant inside a single DB tx
    user_created_at = datetime.now(UTC)
    assistant_created_at = user_created_at + timedelta(milliseconds=1)

    configurable = (config or {}).get("configurable", {})
    session_db = configurable.get("db")

    async def _do_persist(db: AsyncSession) -> None:
        user_msg = ChatMessage(
            id=user_msg_id,
            session_id=session_id,
            role="user",
            content=user_query,
            citations=[],
            status="completed",
            trace_id=trace_id,
            created_at=user_created_at,
        )
        assistant_msg = ChatMessage(
            id=assistant_msg_id,
            session_id=session_id,
            role="assistant",
            content=final_response,
            citations=citations,
            status=msg_status,
            trace_id=trace_id,
            created_at=assistant_created_at,
        )
        db.add(user_msg)
        db.add(assistant_msg)
        await db.flush()

        # If HITL was queued, link assistant_msg_id to the approval record
        if hitl_approval_id:
            try:
                appr_uuid = uuid.UUID(hitl_approval_id)
                stmt = (
                    update(HITLApproval)
                    .where(HITLApproval.id == appr_uuid)
                    .values(message_id=assistant_msg_id)
                )
                await db.execute(stmt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not link message_id to HITLApproval %s: %s", hitl_approval_id, exc
                )

        # Write corresponding audit log entries
        audit_entries: list[AuditLog] = []

        if input_blocked:
            audit_entries.append(
                AuditLog(
                    id=uuid.uuid4(),
                    event_type="guardrail_blocked",
                    actor_role="employee",
                    session_id=session_id,
                    resource_id=str(user_msg_id),
                    details={
                        "block_reason": state.get("block_reason"),
                        "query_snippet": user_query[:150],
                    },
                )
            )

        if pii_redacted:
            audit_entries.append(
                AuditLog(
                    id=uuid.uuid4(),
                    event_type="pii_redacted",
                    actor_role="employee",
                    session_id=session_id,
                    resource_id=str(assistant_msg_id),
                    details={
                        "action": "redacted_sensitive_pii",
                        "session_id": session_id,
                    },
                )
            )

        if requires_hitl:
            audit_entries.append(
                AuditLog(
                    id=uuid.uuid4(),
                    event_type="hitl_queued",
                    actor_role="employee",
                    session_id=session_id,
                    resource_id=hitl_approval_id,
                    details={
                        "trigger_reason": state.get("hitl_trigger_reason"),
                        "max_similarity_score": max_score,
                    },
                )
            )

        if not input_blocked and not requires_hitl:
            audit_entries.append(
                AuditLog(
                    id=uuid.uuid4(),
                    event_type="chat_completed",
                    actor_role="employee",
                    session_id=session_id,
                    resource_id=str(assistant_msg_id),
                    details={
                        "user_query": user_query[:200],
                        "citations_count": len(citations),
                        "max_similarity_score": max_score,
                    },
                )
            )

        for a in audit_entries:
            db.add(a)

        await db.commit()

    if session_db:
        await _do_persist(session_db)
    else:
        async with async_session_maker() as db:
            await _do_persist(db)

    return {
        "user_message_id": str(user_msg_id),
        "assistant_message_id": str(assistant_msg_id),
        "final_response": final_response,
        "status": msg_status,
        "hitl_approval_id": hitl_approval_id,
    }


# ── Routing Functions ─────────────────────────────────────────────────────────


def route_input_guardrail(state: RAGState) -> str:
    """Route blocked requests directly to blocked_response, otherwise to retrieve."""
    if state.get("input_blocked"):
        return "blocked_response"
    return "retrieve"


def route_hitl_check(state: RAGState) -> str:
    """Route requests requiring human review to queue_hitl, otherwise persist_turn."""
    if state.get("requires_hitl"):
        return "queue_hitl"
    return "persist_turn"


# ── Workflow Compilation ──────────────────────────────────────────────────────


def create_rag_graph() -> Any:
    """Build and compile the Phase 4 LangGraph state machine with guardrails & HITL."""
    workflow = StateGraph(RAGState)

    workflow.add_node("load_memory", load_memory_node)
    workflow.add_node("input_guardrail", input_guardrail_node)
    workflow.add_node("blocked_response", blocked_response_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("output_guardrail", output_guardrail_node)
    workflow.add_node("hitl_check", hitl_check_node)
    workflow.add_node("queue_hitl", queue_hitl_node)
    workflow.add_node("persist_turn", persist_turn_node)

    # Topology connections
    workflow.set_entry_point("load_memory")
    workflow.add_edge("load_memory", "input_guardrail")

    workflow.add_conditional_edges(
        "input_guardrail",
        route_input_guardrail,
        {
            "blocked_response": "blocked_response",
            "retrieve": "retrieve",
        },
    )

    workflow.add_edge("blocked_response", "persist_turn")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", "output_guardrail")
    workflow.add_edge("output_guardrail", "hitl_check")

    workflow.add_conditional_edges(
        "hitl_check",
        route_hitl_check,
        {
            "queue_hitl": "queue_hitl",
            "persist_turn": "persist_turn",
        },
    )

    workflow.add_edge("queue_hitl", "persist_turn")
    workflow.add_edge("persist_turn", END)

    return workflow.compile()


rag_graph = create_rag_graph()


# ── High-Level Runner ─────────────────────────────────────────────────────────


async def run_rag_pipeline(
    session_id: str,
    query: str,
    db: AsyncSession | None = None,
    llm: Any | None = None,
) -> RAGState:
    """Execute the full RAG pipeline for a user query.

    Args:
        session_id: Unique chat session identifier.
        query: User prompt.
        db: Optional active AsyncSession.
        llm: Optional custom LLM instance for testing.

    Returns:
        Final RAGState containing answer, status, citations, approval id, and flags.
    """
    trace_id = create_otel_trace_id()
    langfuse_handler = get_langfuse_callback(
        trace_id=trace_id,
        session_id=session_id,
        trace_name="atlas-rag-query",
    )
    callbacks = [langfuse_handler] if langfuse_handler else []
    langfuse_metadata = build_langfuse_metadata(
        session_id=session_id,
        trace_name="atlas-rag-query",
    )

    initial_state: RAGState = {
        "session_id": session_id,
        "user_query": query,
        "sanitized_query": query,
        "chat_history": [],
        "input_blocked": False,
        "block_reason": None,
        "is_sensitive_action": False,
        "retrieved_chunks": [],
        "max_similarity_score": 0.0,
        "draft_answer": "",
        "citations": [],
        "pii_redacted": False,
        "requires_hitl": False,
        "hitl_trigger_reason": None,
        "hitl_approval_id": None,
        "status": "completed",
        "final_response": "",
        "trace_id": trace_id,
    }

    config: RunnableConfig = {
        "callbacks": callbacks,
        "metadata": langfuse_metadata,
        "run_name": "atlas-rag-query",
        "configurable": {
            "db": db,
            "llm": llm,
        },
    }

    final_state: RAGState = await rag_graph.ainvoke(initial_state, config=config)
    if langfuse_handler:
        flush_langfuse()
    return final_state
