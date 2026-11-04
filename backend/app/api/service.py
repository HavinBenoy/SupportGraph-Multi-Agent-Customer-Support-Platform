"""API-side glue between HTTP and the LangGraph workflows: mapping graph state to what the UI shows, and recording chat events."""

import logging
import time
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage

from app.api.schemas import ChatOut, Citation
from app.db.models import ChatEvent
from app.db.session import session_scope
from app.graph.review import converse, pending_approval

log = logging.getLogger(__name__)


def status_of(out: dict) -> str:
    if out.get("awaiting") == "staff":
        return "awaiting_staff"
    if out.get("awaiting") == "student":
        return "awaiting_confirmation"
    if out.get("needs_human"):
        return "escalated"
    route = out.get("route")
    if route == "clarify" or out.get("qa_blocked") or (route == "knowledge" and out.get("abstained")):
        return "needs_detail"
    if route == "write":
        return "done" if out.get("outcome") in ("approved", "rejected") else "needs_detail"
    return "answered"


def run_turn(graph, *, tenant_id: str, student_id: str, ticket_id: str, text: str, today: date | None = None) -> tuple[ChatOut, dict]:
    extra = {"today": today.isoformat()} if today else {}
    started = time.perf_counter()
    out = converse(graph, text, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id, **extra)
    latency = int((time.perf_counter() - started) * 1000)
    citations = [Citation(id=s["source_id"], heading=s.get("heading", "")) for s in out.get("sources", [])
                 if out.get("route") == "knowledge" and not out.get("abstained") and not out.get("qa_blocked")]
    resp = ChatOut(ticket_id=ticket_id, reply=out["reply"], status=status_of(out), citations=citations,
                   escalation_id=out.get("escalation_id"), signed_in=student_id != "anonymous")
    return resp, {**out, "_latency_ms": latency}


def record_event(*, tenant_id: str, ticket_id: str, signed_in: bool, out: dict) -> None:
    """Analytics must never break a chat: failures are logged and swallowed."""
    try:
        with session_scope() as db:
            db.add(ChatEvent(tenant_id=tenant_id, ticket_id=ticket_id, signed_in=signed_in, route=out.get("route") or "waiting",
                             route_reason=(out.get("route_reason") or "")[:64], abstained=bool(out.get("abstained")),
                             qa_blocked=bool(out.get("qa_blocked")), needs_human=bool(out.get("needs_human")),
                             awaiting=out.get("awaiting") or "", latency_ms=out.get("_latency_ms", 0)))
    except Exception as e:  # noqa: BLE001
        log.warning("chat event not recorded (%s)", type(e).__name__)


def history(graph, *, tenant_id: str, student_id: str, ticket_id: str) -> tuple[list[dict], str | None]:
    from app.graph.support import support_config

    cfg = {"configurable": support_config(tenant_id, student_id=student_id, ticket_id=ticket_id)["configurable"]}
    state = graph.get_state(cfg)
    msgs = []
    for m in (state.values or {}).get("messages", []):
        if isinstance(m, HumanMessage):
            msgs.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AIMessage):
            msgs.append({"role": "assistant", "content": str(m.content)})
    pending = pending_approval(graph, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id)
    awaiting = pending["role"] if pending else None
    if pending and not msgs[-1:] == [] and msgs[-1]["role"] == "user":
        msgs.append({"role": "assistant", "content": pending["student_message"]})  # the paused turn's reply is not yet in the thread
    return msgs, awaiting
