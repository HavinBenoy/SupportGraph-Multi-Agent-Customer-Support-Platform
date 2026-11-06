"""Resuming paused runs: the staff/student side of the approval gate, and chat turns that may be answers to a pause.

Authorization is layered: (1) this module checks the ticket belongs to the reviewer's tenant (and, for a student, to that
student) and that the right role is being asked; (2) the approval node and `executor.authorize_decision` check again before
anything is written. A ticket of another tenant looks exactly like a ticket that does not exist.
"""

import re
from typing import Any

from langgraph.types import Command

from app.conversation.intents import GOODBYE, detect_intent
from app.db.models import Ticket
from app.db.session import session_scope
from app.graph.nodes import replies
from app.graph.support import _with_pause, ask_support, support_config
from app.tenants.scope import Reviewer
from app.tools.scope import ANONYMOUS

_YES = re.compile(r"^\s*(yes|y|yeah|yep|confirm|confirmed|ok|okay|sure|please do)\W*$", re.I)
_NO = re.compile(r"^\s*(no|n|nope|cancel|stop|don'?t|do not)\W*$", re.I)


class ReviewError(RuntimeError):
    """Nothing to decide (unknown ticket, wrong tenant, not waiting, wrong role). One message for all, so ids cannot be probed."""


_NOTHING = "no pending approval for that ticket"


def _owner(tenant_id: str, ticket_id: str) -> str | None:
    with session_scope() as db:
        t = db.get(Ticket, (tenant_id, ticket_id))
        return t.student_id if t else None


def pending_approval(graph, *, tenant_id: str, student_id: str, ticket_id: str) -> dict | None:
    """The payload of the approval this thread is waiting on, or None."""
    state = graph.get_state({"configurable": support_config(tenant_id, student_id=student_id, ticket_id=ticket_id)["configurable"]})
    for task in state.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def resume_ticket(graph, reviewer: Reviewer, ticket_id: str, *, approve: bool, **extra: Any) -> dict:
    """Answer the approval a ticket is paused on, and let the run finish. Returns the final state."""
    owner = _owner(reviewer.tenant_id, ticket_id)
    if owner is None or (reviewer.role == "student" and reviewer.reviewer_id != owner):
        raise ReviewError(_NOTHING)
    config = support_config(reviewer.tenant_id, student_id=owner, ticket_id=ticket_id, **extra)
    pending = pending_approval(graph, tenant_id=reviewer.tenant_id, student_id=owner, ticket_id=ticket_id)
    if pending is None or pending.get("role") != reviewer.role:
        raise ReviewError(_NOTHING)
    answer = {"decision": "approve" if approve else "reject", "reviewer_id": reviewer.reviewer_id, "role": reviewer.role,
              "tenant_id": reviewer.tenant_id}
    return _with_pause(graph, graph.invoke(Command(resume=answer), config), config)


def converse(graph, text: str, *, tenant_id: str, student_id: str = ANONYMOUS, ticket_id: str, **extra: Any) -> dict:
    """One chat turn. If the thread is waiting for THIS student's yes/no, the text is that answer; if it is waiting for staff,
    the student is told so (a new message must not orphan the pending approval); otherwise it is a normal new message."""
    pending = pending_approval(graph, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id)
    if pending is None:
        return ask_support(graph, text, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id, **extra)
    if pending["role"] == "staff":
        if detect_intent(text) == GOODBYE:  # the caller may leave; the request stays pending and the outcome shows in the conversation
            return {"reply": replies.GOODBYE_WHILE_PENDING, "awaiting": "staff", "pending": pending, "end_call": True}
        return {"reply": replies.AWAITING_STAFF, "awaiting": "staff", "pending": pending}
    answer = True if _YES.match(text) else False if _NO.match(text) else None
    if answer is None:
        return {"reply": replies.AWAITING_CONFIRM, "awaiting": "student", "pending": pending}
    return resume_ticket(graph, Reviewer(tenant_id=tenant_id, reviewer_id=student_id, role="student"), ticket_id, approve=answer, **extra)
