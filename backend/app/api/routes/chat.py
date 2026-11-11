"""The customer chat: anonymous visitors (prospects) and signed-in students, both through the same LangGraph workflow."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import client_key, optional_principal
from app.api.routes.auth import tenant_or_404
from app.api.schemas import ChatIn, ChatOut, HistoryOut, MessageOut
from app.api.service import history, record_event, run_turn
from app.auth.tokens import (
    Principal, check_anonymous_ticket, check_student_ticket, new_anonymous_ticket, new_student_ticket,
)
from app.tools.scope import ANONYMOUS

router = APIRouter(prefix="/api", tags=["chat"])


def _identity(request: Request, principal: Principal | None, tenant: str | None, *, members: bool = False) -> tuple[str, str]:
    """(tenant_id, student_id). Signed in: both come from the token. Anonymous: the tenant from the request, student 'anonymous'."""
    if principal is not None:
        if members and principal.role in ("staff", "admin"):
            # Staff and administrators may use the customer-support chat of THEIR OWN tenant, exactly as a visitor would: no student identity, so no student's
            # records are reachable through it (that stays the student's own token). The tenant comes from the token, never from the request.
            return principal.tenant_id, ANONYMOUS
        if principal.role != "student":
            raise HTTPException(403, "this line is for students and visitors; staff use the console")
        return principal.tenant_id, principal.student_id  # type: ignore[return-value]
    return tenant_or_404(tenant, request.app.state.settings.default_tenant).tenant_id, ANONYMOUS


def _ticket(request: Request, tenant_id: str, student_id: str, ticket_id: str | None) -> str:
    secret = request.app.state.secret
    if ticket_id is None:
        return new_anonymous_ticket(secret, tenant_id) if student_id == ANONYMOUS else new_student_ticket(secret, tenant_id, student_id)
    ok = (check_anonymous_ticket(secret, tenant_id, ticket_id) if student_id == ANONYMOUS
          else check_student_ticket(secret, tenant_id, student_id, ticket_id))
    if not ok:  # forged, from another tenant, or another person's: all look the same
        raise HTTPException(404, "unknown conversation")
    return ticket_id


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request, principal: Principal | None = Depends(optional_principal)) -> ChatOut:
    tenant_id, student_id = _identity(request, principal, body.tenant, members=True)
    ticket_id = _ticket(request, tenant_id, student_id, body.ticket_id)
    who = student_id if student_id != ANONYMOUS else client_key(request)
    if not request.app.state.chat_limiter.allow(f"chat:{tenant_id}:{who}"):
        raise HTTPException(429, "you are sending messages too fast, wait a moment", headers={"Retry-After": "60"})
    today: date | None = getattr(request.app.state, "today", None)
    resp, out = run_turn(request.app.state.graph, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id, text=body.text, today=today)
    record_event(tenant_id=tenant_id, ticket_id=ticket_id, signed_in=student_id != ANONYMOUS, out=out)
    return resp


@router.get("/chat/{ticket_id}", response_model=HistoryOut)
def chat_history(ticket_id: str, request: Request, tenant: str | None = None,
                 principal: Principal | None = Depends(optional_principal)) -> HistoryOut:
    """The conversation so far, so a refreshed page (or a student coming back after staff decided) sees the new messages."""
    tenant_id, student_id = _identity(request, principal, tenant, members=True)
    _ticket(request, tenant_id, student_id, ticket_id)
    msgs, awaiting = history(request.app.state.graph, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id)
    return HistoryOut(ticket_id=ticket_id, messages=[MessageOut(**m) for m in msgs], awaiting=awaiting)
