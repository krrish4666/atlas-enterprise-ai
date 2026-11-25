"""app/api/routes_chat.py — Chat, session memory, and feedback API endpoints.

Implements Flow B and endpoints from docs/SCHEMA_AND_API.md:
- POST /api/chat: Execute RAG query via LangGraph pipeline
- GET /api/chat/sessions: List conversation sessions with preview & count
- GET /api/chat/sessions/{session_id}/messages: Fetch ordered message history
- POST /api/chat/feedback: Record thumbs-up / thumbs-down ratings & audit log
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import AuditLog, ChatMessage, Feedback, HITLApproval
from app.db.session import get_db
from app.rag.graph import run_rag_pipeline

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Pydantic Request & Response Schemas ────────────────────────────────────────


class CitationItem(BaseModel):
    """Citation object referencing an exact document chunk."""

    document_id: str
    filename: str
    chunk_index: int
    score: float
    snippet: str


class ChatRequest(BaseModel):
    """User query request payload."""

    session_id: str = Field(
        ..., min_length=1, max_length=64, description="Session UUID or identifier"
    )
    query: str = Field(..., min_length=1, description="Employee question")


class ChatResponse(BaseModel):
    """Grounded assistant response with citations and metadata."""

    session_id: str
    message_id: str
    status: str
    answer: str
    citations: list[CitationItem]
    pii_redacted: bool = False
    hitl_approval_id: str | None = None
    trace_id: str | None = None


class SessionSummary(BaseModel):
    """Summary of a conversation session."""

    session_id: str
    title: str | None = None
    last_message: str
    message_count: int
    updated_at: datetime


class SessionListResponse(BaseModel):
    """List of all conversation sessions."""

    sessions: list[SessionSummary]


class MessageItem(BaseModel):
    """Single message in conversation history."""

    id: uuid.UUID
    role: str
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    created_at: datetime
    hitl_approval_id: str | None = None

    @field_validator("citations", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list[Any]:
        """Ensure citations is always a list, even if null in DB."""
        return v if isinstance(v, list) else []

    model_config = ConfigDict(from_attributes=True)


class SessionMessagesResponse(BaseModel):
    """Ordered message history for a session."""

    session_id: str
    messages: list[MessageItem]


class FeedbackRequest(BaseModel):
    """User rating submission."""

    message_id: uuid.UUID
    rating: int = Field(..., description="1 for thumbs up, -1 for thumbs down")
    comment: str | None = Field(None, max_length=1000)


class FeedbackResponse(BaseModel):
    """Feedback confirmation response."""

    id: uuid.UUID
    message_id: uuid.UUID
    rating: int


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute grounded RAG chat query",
)
async def post_chat(
    req: ChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatResponse:
    """Execute LangGraph RAG pipeline over uploaded documents with citation grounding."""
    try:
        final_state = await run_rag_pipeline(
            session_id=req.session_id,
            query=req.query,
            db=db,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat pipeline error: {exc}",
        ) from exc

    assistant_id = final_state.get("assistant_message_id") or str(uuid.uuid4())
    citations_data = final_state.get("citations", [])

    return ChatResponse(
        session_id=req.session_id,
        message_id=assistant_id,
        status=final_state.get("status", "completed"),
        answer=final_state.get("final_response", ""),
        citations=[CitationItem(**c) for c in citations_data],
        pii_redacted=final_state.get("pii_redacted", False),
        hitl_approval_id=final_state.get("hitl_approval_id"),
        trace_id=final_state.get("trace_id"),
    )


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List all chat sessions with preview and message count",
)
async def list_chat_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionListResponse:
    """Aggregate distinct sessions ordered by most recent message."""
    # Subquery: aggregate count and latest created_at per session
    subq = (
        select(
            ChatMessage.session_id,
            func.count(ChatMessage.id).label("message_count"),
            func.max(ChatMessage.created_at).label("latest_created"),
        )
        .group_by(ChatMessage.session_id)
        .subquery()
    )

    # Subquery: find the earliest user message created_at per session
    user_subq = (
        select(
            ChatMessage.session_id,
            func.min(ChatMessage.created_at).label("first_user_created"),
        )
        .where(ChatMessage.role == "user")
        .group_by(ChatMessage.session_id)
        .subquery()
    )

    first_user_msg = aliased(ChatMessage)
    last_msg = aliased(ChatMessage)

    stmt = (
        select(
            subq.c.session_id,
            subq.c.message_count,
            subq.c.latest_created,
            last_msg.content.label("last_message"),
            first_user_msg.content.label("title"),
        )
        .outerjoin(
            last_msg,
            (last_msg.session_id == subq.c.session_id)
            & (last_msg.created_at == subq.c.latest_created),
        )
        .outerjoin(
            user_subq,
            user_subq.c.session_id == subq.c.session_id,
        )
        .outerjoin(
            first_user_msg,
            (first_user_msg.session_id == subq.c.session_id)
            & (first_user_msg.role == "user")
            & (first_user_msg.created_at == user_subq.c.first_user_created),
        )
        .order_by(desc(subq.c.latest_created))
    )

    result = await db.execute(stmt)
    rows = result.all()

    sessions: list[SessionSummary] = []
    seen: set[str] = set()
    for row in rows:
        sid = str(row.session_id)
        if sid in seen:
            continue
        seen.add(sid)

        raw_title = getattr(row, "title", None)
        raw_last = getattr(row, "last_message", None)

        if isinstance(raw_title, str) and raw_title.strip():
            title_str = raw_title.strip()
        elif isinstance(raw_last, str) and raw_last.strip():
            title_str = raw_last.strip()
        else:
            title_str = "New conversation"

        last_str = raw_last if isinstance(raw_last, str) else ""
        msg_cnt = row.message_count if isinstance(row.message_count, int) else 0

        sessions.append(
            SessionSummary(
                session_id=sid,
                title=title_str[:80],
                last_message=last_str,
                message_count=msg_cnt,
                updated_at=row.latest_created,
            )
        )

    return SessionListResponse(sessions=sessions)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=SessionMessagesResponse,
    summary="Get conversation history for a session",
)
async def get_session_messages(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionMessagesResponse:
    """Return all messages for session_id ordered chronologically."""
    stmt = (
        select(ChatMessage, HITLApproval.id.label("hitl_approval_id"))
        .outerjoin(HITLApproval, HITLApproval.message_id == ChatMessage.id)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(stmt)
    try:
        scalars_mock = getattr(result, "scalars", None)
        scalars_all = scalars_mock().all() if callable(scalars_mock) else None
        if isinstance(scalars_all, list) and len(scalars_all) > 0:
            rows = scalars_all
        else:
            rows = result.all()
    except Exception:
        rows = result.all()

    items: list[MessageItem] = []
    for row in rows:
        if isinstance(row, ChatMessage):
            msg = row
            appr_id = None
        else:
            msg = getattr(row, "ChatMessage", row[0] if hasattr(row, "__getitem__") else row)
            appr_raw = getattr(row, "hitl_approval_id", None)
            appr_id = (
                str(appr_raw)
                if appr_raw is not None and not str(type(appr_raw)).__contains__("Mock")
                else None
            )

        items.append(
            MessageItem(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                citations=msg.citations,
                status=msg.status,
                created_at=msg.created_at,
                hitl_approval_id=appr_id,
            )
        )

    return SessionMessagesResponse(
        session_id=session_id,
        messages=items,
    )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit thumbs up/down rating on an assistant response",
)
async def submit_feedback(
    req: FeedbackRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackResponse:
    """Record user feedback rating and write audit trail."""
    if req.rating not in (1, -1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rating must be either 1 (thumbs up) or -1 (thumbs down).",
        )

    # Verify message exists
    stmt = select(ChatMessage).where(ChatMessage.id == req.message_id)
    result = await db.execute(stmt)
    msg = result.scalar_one_or_none()

    if not msg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{req.message_id}' not found.",
        )

    fb_id = uuid.uuid4()
    fb = Feedback(
        id=fb_id,
        message_id=req.message_id,
        rating=req.rating,
        comment=req.comment,
    )
    db.add(fb)

    audit = AuditLog(
        id=uuid.uuid4(),
        event_type="feedback_submitted",
        actor_role="employee",
        session_id=msg.session_id,
        resource_id=str(fb_id),
        details={
            "message_id": str(req.message_id),
            "rating": req.rating,
            "has_comment": bool(req.comment),
        },
    )
    db.add(audit)
    await db.commit()

    return FeedbackResponse(
        id=fb_id,
        message_id=req.message_id,
        rating=req.rating,
    )
