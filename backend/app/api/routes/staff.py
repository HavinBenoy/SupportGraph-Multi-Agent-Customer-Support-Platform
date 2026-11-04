"""Staff console API: escalations and approvals of the staff member's OWN tenant (taken from the signed token)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.api.deps import require_staff
from app.api.schemas import ApprovalRow, DecisionIn, DecisionOut, EscalationDetail, EscalationRow, ResolveIn
from app.auth.tokens import Principal
from app.db import write_repo
from app.db.models import Escalation, MakeupRequest, Student
from app.db.session import session_scope
from app.escalation import queue
from app.graph.review import ReviewError, resume_ticket
from app.tenants.scope import Reviewer

router = APIRouter(prefix="/api/staff", tags=["staff"])


def _row(e: Escalation) -> dict:
    return dict(id=e.id, ticket_id=e.ticket_id, team=e.team, priority=e.priority, reason=e.reason, status=e.status,
                claimed_by=e.claimed_by, created_at=e.created_at)


def _detail(e: Escalation) -> EscalationDetail:
    return EscalationDetail(**_row(e), brief=e.brief, resolution_note=e.resolution_note, resolved_at=e.resolved_at)


@router.get("/escalations", response_model=list[EscalationRow])
def list_escalations(status: Literal["open", "claimed", "resolved"] = "open", team: str | None = None,
                     p: Principal = Depends(require_staff)) -> list[EscalationRow]:
    with session_scope() as db:
        return [EscalationRow(**_row(e)) for e in queue.list_escalations(db, p.tenant_id, team=team, status=status)]


@router.get("/escalations/{escalation_id}", response_model=EscalationDetail)
def get_escalation(escalation_id: str, p: Principal = Depends(require_staff)) -> EscalationDetail:
    with session_scope() as db:
        e = db.get(Escalation, (p.tenant_id, escalation_id))  # tenant in the key: another tenant's id is simply not found
        if e is None:
            raise HTTPException(404, "escalation not found")
        return _detail(e)


@router.post("/escalations/{escalation_id}/claim", response_model=EscalationDetail)
def claim(escalation_id: str, p: Principal = Depends(require_staff)) -> EscalationDetail:
    try:
        with session_scope() as db:
            return _detail(queue.claim(db, p.tenant_id, escalation_id, p.subject))
    except write_repo.WriteError as e:
        raise HTTPException(409 if "already" in str(e) else 404, str(e)) from None


@router.post("/escalations/{escalation_id}/resolve", response_model=EscalationDetail)
def resolve(escalation_id: str, body: ResolveIn, p: Principal = Depends(require_staff)) -> EscalationDetail:
    try:
        with session_scope() as db:
            return _detail(queue.resolve(db, p.tenant_id, escalation_id, p.subject, body.note))
    except write_repo.WriteError as e:
        raise HTTPException(409 if ("already" in str(e) or "claimed" in str(e)) else 404, str(e)) from None


@router.get("/approvals", response_model=list[ApprovalRow])
def list_approvals(p: Principal = Depends(require_staff)) -> list[ApprovalRow]:
    with session_scope() as db:
        names = dict(db.execute(select(Student.id, Student.name).where(Student.tenant_id == p.tenant_id)).all())
        return [ApprovalRow(request_id=m.id, ticket_id=m.ticket_id, student_id=m.student_id, student_name=names.get(m.student_id),
                            batch_id=m.batch_id, session_date=m.session_date.isoformat(), reason=m.reason, created_at=m.created_at)
                for m in queue.list_pending_makeups(db, p.tenant_id)]


@router.post("/approvals/{request_id}/decision", response_model=DecisionOut)
def decide(request_id: str, body: DecisionIn, request: Request, p: Principal = Depends(require_staff)) -> DecisionOut:
    """Resumes the paused LangGraph run behind the request. The approver identity is the signed-in account, never a request field."""
    with session_scope() as db:
        m = db.get(MakeupRequest, (p.tenant_id, request_id))
        ticket_id = m.ticket_id if m else None
    if m is None or ticket_id is None:
        raise HTTPException(404, "request not found")
    today = getattr(request.app.state, "today", None)
    try:
        out = resume_ticket(request.app.state.graph, Reviewer(p.tenant_id, p.subject, "staff"), ticket_id, approve=body.approve,
                            **({"today": today.isoformat()} if today else {}))
    except ReviewError:
        raise HTTPException(409, "this request is no longer waiting for a decision (already decided, or its paused run was lost)") from None
    return DecisionOut(outcome=out.get("outcome", ""), message=out.get("reply", ""))
