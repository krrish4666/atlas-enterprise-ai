"""tests/test_rag.py — Unit tests for Phase 3 Core RAG engine, memory, and chat API.

Tests:
1. RAGState dictionary structure.
2. Context formatting and citation building in generator.
3. Generator fallback behavior when documents are absent.
4. Generator LLM invocation with grounded context (mocking ChatGoogleGenerativeAI).
5. Tracing fallback behavior when Langfuse is disabled or unconfigured.
6. LangGraph workflow nodes (load_memory, retrieve, generate, persist_turn).
7. Chat API routes: POST /api/chat, GET /api/chat/sessions, GET /messages, POST /feedback.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.db.models import ChatMessage
from app.db.session import get_db
from app.main import app
from app.rag.generator import (
    FALLBACK_NO_DOCS,
    build_citations,
    format_retrieved_context,
    generate_answer,
)
from app.rag.graph import (
    generate_node,
    load_memory_node,
    persist_turn_node,
    retrieve_node,
    run_rag_pipeline,
)
from app.rag.state import RAGState
from app.rag.tracing import (
    _INITIALIZED_CLIENTS,
    build_langfuse_metadata,
    create_otel_trace_id,
    get_langfuse_callback,
)

# ── 1. Generator & Citations Tests ───────────────────────────────────────────


def test_format_retrieved_context_numbered_blocks() -> None:
    """Retrieved chunks are formatted into numbered blocks [1], [2] with metadata."""
    chunks = [
        {
            "filename": "policy.pdf",
            "metadata": {"page": 2},
            "content": "Employees may work remotely 3 days a week.",
        },
        {
            "filename": "benefits.docx",
            "metadata": {"page": 1},
            "content": "Health insurance starts on day one.",
        },
    ]
    formatted = format_retrieved_context(chunks)
    assert "[1] (Document: policy.pdf, Page: 2):" in formatted
    assert "Employees may work remotely 3 days a week." in formatted
    assert "[2] (Document: benefits.docx, Page: 1):" in formatted
    assert "Health insurance starts on day one." in formatted


def test_format_retrieved_context_empty() -> None:
    """Empty chunks list returns 'No relevant documents found.'"""
    assert format_retrieved_context([]) == "No relevant documents found."


def test_build_citations_structure() -> None:
    """build_citations returns structured objects matching SCHEMA_AND_API.md."""
    doc_id = uuid.uuid4()
    chunks = [
        {
            "document_id": str(doc_id),
            "filename": "handbook.pdf",
            "chunk_index": 5,
            "score": 0.825,
            "content": "Short text for snippet testing.",
        }
    ]
    citations = build_citations(chunks)
    assert len(citations) == 1
    assert citations[0]["document_id"] == str(doc_id)
    assert citations[0]["filename"] == "handbook.pdf"
    assert citations[0]["chunk_index"] == 5
    assert citations[0]["score"] == 0.825
    assert "Short text" in citations[0]["snippet"]


@pytest.mark.asyncio
async def test_generate_answer_fallback_when_no_chunks() -> None:
    """When retrieved_chunks is empty, returns standard fallback without calling LLM."""
    answer, citations = await generate_answer(
        query="What is the reimbursement?",
        retrieved_chunks=[],
    )
    assert answer == FALLBACK_NO_DOCS
    assert citations == []


@pytest.mark.asyncio
async def test_generate_answer_invokes_llm_with_context() -> None:
    """When chunks exist, invokes LLM and returns response with citations."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(
        content="Employees are eligible for $500 reimbursement [1]."
    )

    chunks = [
        {
            "document_id": str(uuid.uuid4()),
            "filename": "finance.pdf",
            "chunk_index": 0,
            "score": 0.91,
            "content": "Up to $500 reimbursement available annually.",
            "metadata": {"page": 1},
        }
    ]

    answer, citations = await generate_answer(
        query="How much can I expense?",
        retrieved_chunks=chunks,
        llm=mock_llm,
    )

    assert "eligible for $500" in answer
    assert len(citations) == 1
    assert citations[0]["filename"] == "finance.pdf"
    assert mock_llm.ainvoke.called


# ── 2. Tracing Tests ──────────────────────────────────────────────────────────


def test_get_langfuse_callback_disabled() -> None:
    """When langfuse_enabled is False, get_langfuse_callback returns None."""
    with patch("app.rag.tracing.settings.langfuse_enabled", False):
        cb = get_langfuse_callback()
        assert cb is None


def test_get_langfuse_callback_placeholder_keys() -> None:
    """When public key is default placeholder, returns None without error."""
    with patch("app.rag.tracing.settings.langfuse_public_key", "your_langfuse_public_key"):
        cb = get_langfuse_callback()
        assert cb is None


def test_create_otel_trace_id_format() -> None:
    """create_otel_trace_id generates valid 32-character lowercase hex OTel trace IDs."""
    trace_id = create_otel_trace_id()
    assert len(trace_id) == 32
    assert all(ch in "0123456789abcdef" for ch in trace_id)


def test_build_langfuse_metadata_fields() -> None:
    """build_langfuse_metadata formats SDK v4 RunnableConfig attribute keys."""
    meta = build_langfuse_metadata(
        session_id="sess-42",
        trace_name="atlas-rag-query",
        user_id="emp-1",
        tags=["prod"],
    )
    assert meta == {
        "langfuse_trace_name": "atlas-rag-query",
        "langfuse_tags": ["prod"],
        "langfuse_session_id": "sess-42",
        "langfuse_user_id": "emp-1",
    }


def test_get_langfuse_callback_v4_initializes_client_and_trace_context() -> None:
    """SDK v4 initializes Langfuse(base_url=...) and returns CallbackHandler with trace_context."""
    mock_client = MagicMock()
    mock_handler = MagicMock()
    _INITIALIZED_CLIENTS.clear()

    with (
        patch("app.rag.tracing.settings.langfuse_enabled", True),
        patch("app.rag.tracing.settings.langfuse_public_key", "pk-lf-test-123"),
        patch("app.rag.tracing.settings.langfuse_secret_key", "sk-lf-test-456"),
        patch("app.rag.tracing.settings.langfuse_host", "https://cloud.langfuse.com"),
        patch("langfuse.Langfuse", return_value=mock_client) as mock_lf_cls,
        patch("langfuse.langchain.CallbackHandler", return_value=mock_handler) as mock_cb_cls,
    ):
        trace_id = "0123456789abcdef0123456789abcdef"
        cb = get_langfuse_callback(trace_id=trace_id, session_id="sess-1")

    assert cb is mock_handler
    mock_lf_cls.assert_called_once_with(
        public_key="pk-lf-test-123",
        secret_key="sk-lf-test-456",
        base_url="https://cloud.langfuse.com",
    )
    mock_cb_cls.assert_called_once_with(
        public_key="pk-lf-test-123",
        trace_context={"trace_id": trace_id},
    )
    _INITIALIZED_CLIENTS.clear()


def test_get_langfuse_callback_fails_open_on_error() -> None:
    """Initialization failures log a warning and return None without raising."""
    _INITIALIZED_CLIENTS.clear()
    with (
        patch("app.rag.tracing.settings.langfuse_enabled", True),
        patch("app.rag.tracing.settings.langfuse_public_key", "pk-lf-err"),
        patch("app.rag.tracing.settings.langfuse_secret_key", "sk-lf-err"),
        patch("langfuse.Langfuse", side_effect=RuntimeError("OTel setup failed")),
    ):
        cb = get_langfuse_callback(trace_id="0123456789abcdef0123456789abcdef")
    assert cb is None
    _INITIALIZED_CLIENTS.clear()


@pytest.mark.asyncio
async def test_run_rag_pipeline_wires_otel_config_and_flushes() -> None:
    """run_rag_pipeline passes 32-char trace_id, top-level callbacks/metadata, and flushes."""
    mock_handler = MagicMock()
    mock_state = {"status": "completed", "final_response": "Ok", "trace_id": "a" * 32}

    with (
        patch("app.rag.graph.get_langfuse_callback", return_value=mock_handler) as mock_get_cb,
        patch("app.rag.graph.flush_langfuse") as mock_flush,
        patch("app.rag.graph.rag_graph.ainvoke", new_callable=AsyncMock) as mock_invoke,
    ):
        mock_invoke.return_value = mock_state
        await run_rag_pipeline(session_id="sess-otel", query="Leave policy?")

    called_trace_id = mock_get_cb.call_args.kwargs["trace_id"]
    assert len(called_trace_id) == 32
    assert all(c in "0123456789abcdef" for c in called_trace_id)

    invoke_state, invoke_kwargs = (
        mock_invoke.call_args.args[0],
        mock_invoke.call_args.kwargs,
    )
    assert invoke_state["trace_id"] == called_trace_id
    cfg = invoke_kwargs["config"]
    assert cfg["callbacks"] == [mock_handler]
    assert cfg["metadata"]["langfuse_session_id"] == "sess-otel"
    assert cfg["metadata"]["langfuse_trace_name"] == "atlas-rag-query"
    mock_flush.assert_called_once()


# ── 3. LangGraph Workflow Node Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_memory_node() -> None:
    """load_memory_node retrieves previous turns for session_id."""
    session_id = "test-session-123"
    mock_db = AsyncMock()
    msg1 = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role="user",
        content="Hello",
        status="completed",
        created_at=datetime.now(UTC),
    )
    msg2 = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role="assistant",
        content="Hi there! How can I help?",
        status="completed",
        created_at=datetime.now(UTC),
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [msg1, msg2]
    mock_db.execute.return_value = mock_result

    state: RAGState = {"session_id": session_id}
    config = {"configurable": {"db": mock_db}}

    res = await load_memory_node(state, config=config)
    assert len(res["chat_history"]) == 2
    assert res["chat_history"][0] == {"role": "user", "content": "Hello"}
    assert res["chat_history"][1] == {
        "role": "assistant",
        "content": "Hi there! How can I help?",
    }


@pytest.mark.asyncio
async def test_retrieve_node() -> None:
    """retrieve_node embeds query and queries pgvector retriever."""
    mock_db = AsyncMock()
    mock_chunk = MagicMock()
    mock_chunk.id = uuid.uuid4()
    mock_chunk.document_id = uuid.uuid4()
    mock_chunk.filename = "policy.pdf"
    mock_chunk.chunk_index = 0
    mock_chunk.content = "Sample policy content."
    mock_chunk.chunk_metadata = {"page": 1}
    mock_chunk.score = 0.88
    mock_chunk.snippet = "Sample policy..."

    with (
        patch("app.rag.graph.embedder.embed_query", new_callable=AsyncMock) as mock_embed,
        patch("app.rag.graph.retrieve_similar_chunks", new_callable=AsyncMock) as mock_retrieve,
    ):
        mock_embed.return_value = [0.01] * 768
        mock_retrieve.return_value = [mock_chunk]

        state: RAGState = {"user_query": "What is the policy?"}
        config = {"configurable": {"db": mock_db}}

        res = await retrieve_node(state, config=config)
        assert len(res["retrieved_chunks"]) == 1
        assert res["max_similarity_score"] == 0.88


@pytest.mark.asyncio
async def test_generate_node() -> None:
    """generate_node produces draft_answer and citations."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(content="Grounded answer text [1].")

    state: RAGState = {
        "user_query": "Question?",
        "retrieved_chunks": [
            {
                "document_id": str(uuid.uuid4()),
                "filename": "doc.pdf",
                "chunk_index": 0,
                "score": 0.85,
                "content": "Fact content.",
            }
        ],
        "chat_history": [],
    }
    config = {"configurable": {"llm": mock_llm}}

    res = await generate_node(state, config=config)
    assert res["draft_answer"] == "Grounded answer text [1]."
    assert res["final_response"] == "Grounded answer text [1]."
    assert len(res["citations"]) == 1


@pytest.mark.asyncio
async def test_persist_turn_node() -> None:
    """persist_turn_node writes user and assistant turns to database."""
    mock_db = AsyncMock()
    state: RAGState = {
        "session_id": "sess-xyz",
        "user_query": "User question",
        "final_response": "Assistant reply",
        "citations": [],
        "max_similarity_score": 0.75,
    }
    config = {"configurable": {"db": mock_db}}

    res = await persist_turn_node(state, config=config)
    assert "user_message_id" in res
    assert "assistant_message_id" in res
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_run_rag_pipeline_end_to_end() -> None:
    """Full RAG state machine executes across nodes and returns completed state."""
    mock_db = AsyncMock()
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(content="Synthesized policy answer [1].")

    # Mock history query (empty)
    mock_history_res = MagicMock()
    mock_history_res.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_history_res

    mock_chunk = MagicMock()
    mock_chunk.id = uuid.uuid4()
    mock_chunk.document_id = uuid.uuid4()
    mock_chunk.filename = "rules.pdf"
    mock_chunk.chunk_index = 0
    mock_chunk.content = "All employees get 25 days leave."
    mock_chunk.chunk_metadata = {"page": 3}
    mock_chunk.score = 0.92
    mock_chunk.snippet = "All employees get 25 days leave."

    with (
        patch("app.rag.graph.embedder.embed_query", new_callable=AsyncMock) as mock_embed,
        patch("app.rag.graph.retrieve_similar_chunks", new_callable=AsyncMock) as mock_retrieve,
    ):
        mock_embed.return_value = [0.05] * 768
        mock_retrieve.return_value = [mock_chunk]

        final_state = await run_rag_pipeline(
            session_id="integration-sess",
            query="How many days leave do I get?",
            db=mock_db,
            llm=mock_llm,
        )

        assert final_state["session_id"] == "integration-sess"
        assert "Synthesized policy answer" in final_state["final_response"]
        assert len(final_state["citations"]) == 1
        assert final_state["citations"][0]["filename"] == "rules.pdf"
        assert final_state["user_message_id"] is not None
        assert final_state["assistant_message_id"] is not None


# ── 4. Chat API Routes Tests ──────────────────────────────────────────────────


def test_post_chat_endpoint_success() -> None:
    """POST /api/chat triggers RAG pipeline and returns 200 with citations."""
    client = TestClient(app)
    mock_session = AsyncMock()

    mock_state: RAGState = {
        "session_id": "sess-456",
        "final_response": "Grounded answer from policy [1].",
        "assistant_message_id": str(uuid.uuid4()),
        "citations": [
            {
                "document_id": str(uuid.uuid4()),
                "filename": "policy.pdf",
                "chunk_index": 2,
                "score": 0.89,
                "snippet": "Policy snippet text.",
            }
        ],
        "pii_redacted": False,
        "hitl_approval_id": None,
        "trace_id": "lf-test-123",
    }

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        with patch("app.api.routes_chat.run_rag_pipeline", new_callable=AsyncMock) as mock_pipeline:
            mock_pipeline.return_value = mock_state

            payload = {
                "session_id": "sess-456",
                "query": "What is the policy on equipment?",
            }
            resp = client.post("/api/chat", json=payload)

            assert resp.status_code == 200
            data = resp.json()
            assert data["session_id"] == "sess-456"
            assert data["status"] == "completed"
            assert data["answer"] == "Grounded answer from policy [1]."
            assert len(data["citations"]) == 1
            assert data["citations"][0]["filename"] == "policy.pdf"
            assert data["pii_redacted"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_chat_sessions() -> None:
    """GET /api/chat/sessions returns aggregated session list."""
    client = TestClient(app)
    mock_session = AsyncMock()

    mock_row = MagicMock()
    mock_row.session_id = "sess-001"
    mock_row.message_count = 4
    mock_row.latest_created = datetime.now(UTC)
    mock_row.last_message = "What is remote work policy?"

    mock_res = MagicMock()
    mock_res.all.return_value = [mock_row]
    mock_session.execute.return_value = mock_res

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = client.get("/api/chat/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "sess-001"
        assert data["sessions"][0]["message_count"] == 4
        assert data["sessions"][0]["last_message"] == "What is remote work policy?"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_get_session_messages() -> None:
    """GET /api/chat/sessions/{session_id}/messages returns message list."""
    client = TestClient(app)
    mock_session = AsyncMock()

    m1 = ChatMessage(
        id=uuid.uuid4(),
        session_id="sess-001",
        role="user",
        content="Question?",
        status="completed",
        created_at=datetime.now(UTC),
    )
    m2 = ChatMessage(
        id=uuid.uuid4(),
        session_id="sess-001",
        role="assistant",
        content="Answer.",
        status="completed",
        created_at=datetime.now(UTC),
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [m1, m2]
    mock_session.execute.return_value = mock_res

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = client.get("/api/chat/sessions/sess-001/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-001"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_post_chat_feedback_success() -> None:
    """POST /api/chat/feedback records thumbs-up rating and audit log."""
    client = TestClient(app)
    mock_session = AsyncMock()

    msg_id = uuid.uuid4()
    mock_msg = ChatMessage(
        id=msg_id,
        session_id="sess-001",
        role="assistant",
        content="Helpful answer.",
        status="completed",
        created_at=datetime.now(UTC),
    )

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_msg
    mock_session.execute.return_value = mock_res

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        payload = {
            "message_id": str(msg_id),
            "rating": 1,
            "comment": "Accurate response with good citations.",
        }
        resp = client.post("/api/chat/feedback", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert data["message_id"] == str(msg_id)
        assert data["rating"] == 1
        assert mock_session.commit.called
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_post_chat_feedback_invalid_rating() -> None:
    """POST /api/chat/feedback rejects ratings other than 1 or -1 with 400."""
    client = TestClient(app)
    payload = {
        "message_id": str(uuid.uuid4()),
        "rating": 5,  # Invalid
        "comment": "Nice",
    }
    resp = client.post("/api/chat/feedback", json=payload)
    assert resp.status_code == 400
    assert "1" in resp.json()["detail"]
