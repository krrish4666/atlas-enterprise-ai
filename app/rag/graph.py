"""app/rag/graph.py — LangGraph RAG workflow for Atlas Enterprise AI.

Phase 3 Initial Topology:
START -> load_memory -> retrieve -> generate -> persist_turn -> END

Instruments every run with optional Langfuse tracing and persists conversation turns
to PostgreSQL chat_messages with citations and audit logs.
"""

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AuditLog, ChatMessage
from app.db.session import async_session_maker
from app.ingestion.embedder import embedder
from app.rag.generator import generate_answer
from app.rag.retriever import retrieve_similar_chunks
from app.rag.state import RAGState
from app.rag.tracing import get_langfuse_callback

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
            .order_by(ChatMessage.created_at.asc())
            .limit(10)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
        # Keep only the last 2 messages for memory to prevent context explosion on Free Tier
        recent = messages[-2:] if len(messages) > 2 else messages
        return [{"role": m.role, "content": m.content} for m in recent]

    if session_db:
        history = await _query_history(session_db)
    else:
        async with async_session_maker() as db:
            history = await _query_history(db)

    return {"chat_history": history}


async def retrieve_node(state: RAGState, config: RunnableConfig | None = None) -> dict[str, Any]:
    """Embed the user query via Ollama and retrieve top-K grounded chunks from pgvector."""
    query = state.get("user_query", "")
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
    query = state.get("user_query", "")
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
        "pii_redacted": False,
        "requires_hitl": False,
        "hitl_approval_id": None,
    }


async def persist_turn_node(
    state: RAGState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """Persist user prompt and assistant response into chat_messages and audit_logs."""
    session_id = state.get("session_id", "default")
    user_query = state.get("user_query", "")
    final_response = state.get("final_response", "")
    citations = state.get("citations", [])
    max_score = state.get("max_similarity_score", 0.0)
    trace_id = state.get("trace_id")

    user_msg_id = uuid.uuid4()
    assistant_msg_id = uuid.uuid4()

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
        )
        assistant_msg = ChatMessage(
            id=assistant_msg_id,
            session_id=session_id,
            role="assistant",
            content=final_response,
            citations=citations,
            status="completed",
            trace_id=trace_id,
        )
        audit = AuditLog(
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
        db.add(user_msg)
        db.add(assistant_msg)
        db.add(audit)
        await db.commit()

    if session_db:
        await _do_persist(session_db)
    else:
        async with async_session_maker() as db:
            await _do_persist(db)

    return {
        "user_message_id": str(user_msg_id),
        "assistant_message_id": str(assistant_msg_id),
    }


# ── Workflow Compilation ──────────────────────────────────────────────────────


def create_rag_graph() -> Any:
    """Build and compile the Phase 3 LangGraph state machine."""
    workflow = StateGraph(RAGState)

    workflow.add_node("load_memory", load_memory_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("persist_turn", persist_turn_node)

    workflow.set_entry_point("load_memory")
    workflow.add_edge("load_memory", "retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", "persist_turn")
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
    """Execute the full RAG pipeline for a user query."""
    trace_id = f"lf-trace-{uuid.uuid4().hex[:12]}"
    langfuse_handler = get_langfuse_callback(session_id=session_id, trace_name="atlas-rag-query")
    callbacks = [langfuse_handler] if langfuse_handler else []

    initial_state: RAGState = {
        "session_id": session_id,
        "user_query": query,
        "chat_history": [],
        "retrieved_chunks": [],
        "max_similarity_score": 0.0,
        "draft_answer": "",
        "citations": [],
        "pii_redacted": False,
        "requires_hitl": False,
        "hitl_approval_id": None,
        "final_response": "",
        "trace_id": trace_id,
    }

    config: RunnableConfig = {
        "configurable": {
            "db": db,
            "callbacks": callbacks,
            "llm": llm,
        }
    }

    final_state = await rag_graph.ainvoke(initial_state, config=config)
    return final_state
