"""The complete conversation of a call, for the staff member who opens it (M4.1).

ONE source of truth, no second transcript: what the customer and the assistant said is read from the conversation history the system already keeps
(the LangGraph checkpoint thread of the call's ticket: the same history the text chat shows). The checkpoints are also what gives each message a
time (the first checkpoint that contains it). The system lines (staff took over, the spoken hand-over notice, the end of the call) come from the call's
audit events, so they can never be mistaken for something the assistant said.

Honest limits (reported in `notes`, never papered over):
  * The human staff member's SPEECH is not transcribed (no speech-to-text runs on the staff microphone, deliberately: nothing the staff say is stored).
    The transcript shows when they spoke, not what they said.
  * The thread belongs to the TICKET. If the caller reused a ticket, earlier messages appear first and are marked `earlier`.
  * A reply the assistant had already produced but that the staff member cut off with the takeover is part of the history and is shown.
Nothing here is logged. The caller of `build` decides who may see the result (staff/admin of the call's tenant)."""

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage

from app.db.models import Call, CallEvent
from app.tools.scope import ANONYMOUS

log = logging.getLogger(__name__)
MAX_ENTRIES = 1000
MAX_SNAPSHOTS = 600


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse(ts) -> datetime | None:
    if isinstance(ts, datetime):
        return _aware(ts)
    if isinstance(ts, str):
        try:
            return _aware(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _thread_messages(graph, call: Call) -> tuple[list[dict], bool]:
    """(messages oldest first with their times, times_known). Reads the existing conversation thread; empty if there is none yet."""
    from app.graph.review import pending_approval
    from app.graph.support import support_config

    student = call.student_id or ANONYMOUS
    cfg = {"configurable": support_config(call.tenant_id, student_id=student, ticket_id=call.ticket_id)["configurable"]}
    state = graph.get_state(cfg)
    first_seen: dict[str, datetime] = {}
    times_known = False
    try:
        for i, snap in enumerate(graph.get_state_history(cfg)):          # newest first: overwriting leaves the EARLIEST checkpoint holding each message
            if i >= MAX_SNAPSHOTS:
                break
            ts = _parse(getattr(snap, "created_at", None))
            if ts is None:
                continue
            for m in (snap.values or {}).get("messages", []) or []:
                mid = getattr(m, "id", None)
                if mid:
                    first_seen[mid] = ts
                    times_known = True
    except Exception:  # noqa: BLE001 - times are an enhancement; the conversation is still shown, in order
        log.warning("transcript timestamps unavailable")
    out: list[dict] = []
    for m in (state.values or {}).get("messages", []) or []:
        if isinstance(m, HumanMessage):
            who = "customer"
        elif isinstance(m, AIMessage) and str(m.content or "").strip():
            who = "ai"
        else:
            continue                                                       # tool calls / tool results are not part of the conversation
        mid = getattr(m, "id", None)
        out.append({"speaker": who, "text": str(m.content), "message_id": mid, "ts": first_seen.get(mid) if mid else None})
    try:                                                                   # a turn paused for approval: its reply is not in the thread yet but was spoken
        pending = pending_approval(graph, tenant_id=call.tenant_id, student_id=student, ticket_id=call.ticket_id)
        if pending and out and out[-1]["speaker"] == "customer" and pending.get("student_message"):
            out.append({"speaker": "ai", "text": str(pending["student_message"]), "message_id": None, "ts": None})
    except Exception:  # noqa: BLE001
        pass
    return out, times_known


def _system(ev: CallEvent, announcement: str, trial_disclosure: str) -> dict | None:
    k, d = ev.kind, ev.detail or {}
    actor = ev.actor
    if k == "takeover_succeeded":
        return {"speaker": "system", "code": "takeover", "text": f"{actor} took over the call. The assistant is silent from here on."}
    if k == "announcement_started":
        return {"speaker": "system", "code": "announcement", "text": announcement, "status": "playing"}
    if k == "announcement_finished":
        return {"speaker": "system", "code": "announcement_done", "text": "The hand-over announcement finished playing.", "status": "played"}
    if k == "announcement_failed":
        return {"speaker": "system", "code": "announcement_failed", "text": "The hand-over announcement could not be played to the caller.", "status": "failed"}
    if k == "trial_notice_played":
        return {"speaker": "system", "code": "trial_notice", "text": trial_disclosure}
    if k == "consent_granted":
        return {"speaker": "system", "code": "consent", "text": "The caller agreed to the call being recorded."}
    if k == "consent_declined":
        return {"speaker": "system", "code": "consent", "text": "The caller declined recording. The call is not being recorded."}
    if k == "consent_withdrawn":
        return {"speaker": "system", "code": "consent", "text": "The caller withdrew consent to recording. Recording stopped."}
    if k == "participant_disconnected":
        who = {"caller": "The caller", "staff": f"{actor}"}.get(str(d.get("role")), str(actor))
        return {"speaker": "system", "code": "disconnected", "text": f"{who} disconnected."}
    if k == "ended":
        return {"speaker": "system", "code": "ended", "text": "The call ended.", "reason": d.get("reason")}
    if k == "failed":
        return {"speaker": "system", "code": "failed", "text": "The call failed.", "reason": d.get("reason")}
    return None


def build(graph, call: Call, events: list[CallEvent], *, announcement: str, trial_disclosure: str = "") -> dict:
    started = _aware(call.created_at) or datetime.now(timezone.utc)
    msgs, times_known = _thread_messages(graph, call) if graph is not None else ([], False)

    items: list[tuple[datetime, int, dict]] = []
    last = started
    for i, m in enumerate(msgs):
        ts = m["ts"] or last                                                # an unknown time sits right after the message before it
        last = ts
        items.append((ts, i, {"speaker": m["speaker"], "text": m["text"], "at": _iso(m["ts"]), "message_id": m["message_id"], "index": i,
                              "earlier": bool(m["ts"] and m["ts"] < started)}))
    base = len(msgs) + 1
    open_span: tuple[datetime, str] | None = None
    for j, ev in enumerate(events):
        ts = _aware(ev.created_at) or started
        if ev.kind == "staff_mic_started":
            open_span = (ts, ev.actor)
            continue
        if ev.kind == "staff_mic_stopped":
            if open_span:
                dur = max(0, round((ts - open_span[0]).total_seconds()))
                items.append((open_span[0], base + j, {"speaker": "staff", "kind": "speech", "text": None, "at": _iso(open_span[0]), "until": _iso(ts),
                                                       "duration_s": dur, "staff": open_span[1], "note": "Spoke to the caller (staff audio is not transcribed)"}))
                open_span = None
            continue
        sysmsg = _system(ev, announcement, trial_disclosure)
        if sysmsg:
            sysmsg["at"] = _iso(ts)
            if ev.kind == "ended" or ev.kind == "failed":
                pass
            items.append((ts, base + j, sysmsg))
    if open_span:                                                           # the call ended while the microphone was open
        items.append((open_span[0], base + len(events), {"speaker": "staff", "kind": "speech", "text": None, "at": _iso(open_span[0]), "until": None,
                                                         "duration_s": None, "staff": open_span[1], "note": "Spoke to the caller (staff audio is not transcribed)"}))
    items.sort(key=lambda t: (t[0], t[1]))
    entries = [e for _, _, e in items]
    truncated = len(entries) > MAX_ENTRIES
    entries = entries[-MAX_ENTRIES:]
    notes: list[str] = []
    if any(e.get("kind") == "speech" for e in entries) or any(e.get("code") == "takeover" for e in entries):
        notes.append("What the staff member said is not transcribed: the transcript shows when they spoke, not their words.")
    if any(e.get("earlier") for e in entries):
        notes.append("Messages marked 'earlier' were exchanged on the same ticket before this call started.")
    if msgs and not times_known:
        notes.append("Message times are not available for this conversation; the order is exact, the times are approximate.")
    if truncated:
        notes.append(f"Only the most recent {MAX_ENTRIES} entries are shown.")
    return {"call_id": call.id, "entries": entries, "notes": notes, "complete": not truncated}
