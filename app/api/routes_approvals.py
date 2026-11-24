"""app/api/routes_approvals.py — Human-in-the-Loop (HITL) Review Endpoints.

Implements Section 3.C of docs/SCHEMA_AND_API.md and Phase 4 of docs/PLAN.md:
- GET /api/approvals?status=pending: List approval tickets filtered by status.
- POST /api/approvals/{approval_id}/decision: Finalize pending approval (approve, edit_and_approve, reject)
  with role enforcement (X-User-Role: reviewer | admin), idempotent state machine (409 on duplicate),
  chat_messages update, and audit log tracking.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, ChatMessage, HITLApproval
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

VALID_DECISIONS = {"approve", "edit_and_approve", "reject"}
ALLOWED_ROLES = {"reviewer", "admin"}


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class ApprovalItem(BaseModel):
    """Single approval ticket representation."""

    id: uuid.UUID
    session_id: str
    message_id: uuid.UUID | None
    user_query: str
    draft_answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    trigger_reason: str
    max_similarity_score: float
    status: str
    final_answer: str | None = None
    reviewer_notes: str | None = None
    reviewed_by: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ApprovalListResponse(BaseModel):
    """List of approval tickets."""

    approvals: list[ApprovalItem]


class DecisionRequest(BaseModel):
    """Reviewer decision payload."""

    decision: str = Field(..., description="'approve', 'edit_and_approve', or 'reject'")
    edited_answer: str | None = Field(
        None, description="Required or optional edited answer for edit_and_approve"
    )
    reviewer_notes: str | None = Field(
        None, max_length=1000, description="Notes from the reviewer explaining the decision"
    )


class DecisionResponse(BaseModel):
    """Reviewer decision confirmation."""

    id: uuid.UUID
    status: str
    final_answer: str
    reviewed_at: datetime


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=ApprovalListResponse,
    status_code=status.HTTP_200_OK,
    summary="List approval tickets filtered by status",
)
async def list_approvals(
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> ApprovalListResponse:
    """Retrieve HITL approval tickets ordered chronologically descending."""
    stmt = select(HITLApproval).order_by(HITLApproval.created_at.desc())
    if status_filter:
        stmt = stmt.where(HITLApproval.status == status_filter)

    result = await db.execute(stmt)
    records = result.scalars().all()

    return ApprovalListResponse(approvals=[ApprovalItem.model_validate(r) for r in records])


@router.post(
    "/{approval_id}/decision",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit human decision for a pending approval ticket",
)
async def submit_decision(
    approval_id: uuid.UUID,
    req: DecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
) -> DecisionResponse:
    """Resolve an approval ticket with reviewer or admin permissions.

    Enforces:
    - Role authorization (X-User-Role: reviewer or admin) -> 403 Forbidden.
    - Valid decision type ('approve', 'edit_and_approve', 'reject') -> 400 Bad Request.
    - Existence of ticket -> 404 Not Found.
    - Idempotent state transitions: only 'pending' tickets can be resolved -> 409 Conflict.
    - Updates linked chat_messages record and writes a structured hitl_decided audit log.
    """
    # 1. Role enforcement
    role = (x_user_role or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reviewer or admin role required via X-User-Role header.",
        )

    # 2. Decision validation
    decision = req.decision.strip().lower()
    if decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid decision '{req.decision}'. Allowed decisions: {sorted(VALID_DECISIONS)}.",
        )

    # 3. Lookup ticket
    stmt = select(HITLApproval).where(HITLApproval.id == approval_id)
    result = await db.execute(stmt)
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval ticket '{approval_id}' not found.",
        )

    # 4. Idempotency enforcement
    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval ticket '{approval_id}' is already resolved with status '{approval.status}'.",
        )

    # 5. Resolve decision & determine final answer
    if decision == "approve":
        new_status = "approved"
        final_answer = approval.draft_answer
    elif decision == "edit_and_approve":
        new_status = "edited_and_approved"
        final_answer = req.edited_answer if req.edited_answer else approval.draft_answer
    else:  # reject
        new_status = "rejected"
        final_answer = "This response was reviewed and rejected by an administrator."

    now = datetime.now(UTC)
    approval.status = new_status
    approval.final_answer = final_answer
    approval.reviewer_notes = req.reviewer_notes
    approval.reviewed_by = role
    approval.reviewed_at = now

    # 6. Update linked ChatMessage (if linked)
    if approval.message_id:
        msg_stmt = select(ChatMessage).where(ChatMessage.id == approval.message_id)
        msg_res = await db.execute(msg_stmt)
        chat_msg = msg_res.scalar_one_or_none()
        if chat_msg:
            chat_msg.content = final_answer
            chat_msg.status = "completed" if new_status != "rejected" else "rejected"

    # 7. Write audit log
    audit = AuditLog(
        id=uuid.uuid4(),
        event_type="hitl_decided",
        actor_role=role,
        session_id=approval.session_id,
        resource_id=str(approval.id),
        details={
            "decision": decision,
            "reviewer_notes": req.reviewer_notes,
            "previous_status": "pending",
            "new_status": new_status,
            "message_id": str(approval.message_id) if approval.message_id else None,
        },
    )
    db.add(audit)
    await db.commit()

    return DecisionResponse(
        id=approval.id,
        status=new_status,
        final_answer=final_answer,
        reviewed_at=now,
    )
