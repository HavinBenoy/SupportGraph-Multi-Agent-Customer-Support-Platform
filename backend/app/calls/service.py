"""Call records and events (M4): create, read, list, record. The lifecycle rules are in `state.py`; the live audio is in `hub.py`.

Every function takes the tenant id explicitly and filters on it, so a call of another tenant is simply "not found". An event is written
in the SAME transaction as the state change it describes. Event details are restricted to a fixed set of short scalar keys: nothing that
could carry audio, a transcript or free text can be stored by accident."""

import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calls.state import LIVE, TERMINAL, TransitionError, now, transition
from app.db.models import Call, CallEvent

EVENT_KINDS = frozenset({"created", "connected", "listen_started", "listen_stopped", "takeover_requested", "takeover_succeeded", "takeover_failed",
                         "returned_to_ai", "ended", "failed", "participant_disconnected"})
SAFE_DETAIL_KEYS = frozenset({"reason", "role", "epoch", "listeners", "previous_owner", "duration_s", "connection", "status"})
MAX_DETAIL_STR = 64


class NotFound(Exception):
    pass


def _safe(detail: dict | None) -> dict:
    out: dict = {}
    for k, v in (detail or {}).items():
        if k in SAFE_DETAIL_KEYS and isinstance(v, (str, int, float, bool)):
            out[k] = v[:MAX_DETAIL_STR] if isinstance(v, str) else v
    return out


def add_event(db: Session, tenant_id: str, call_id: str, kind: str, actor: str, **detail) -> None:
    assert kind in EVENT_KINDS, kind
    db.add(CallEvent(tenant_id=tenant_id, call_id=call_id, kind=kind, actor=actor[:64], detail=_safe(detail)))


def new_call_id() -> str:
    return "c_" + secrets.token_hex(8)


def create_call(db: Session, tenant_id: str, ticket_id: str, student_id: str | None) -> Call:
    call = Call(tenant_id=tenant_id, id=new_call_id(), ticket_id=ticket_id, student_id=student_id, status="created", owner="none", epoch=0, created_at=now())
    db.add(call)
    add_event(db, tenant_id, call.id, "created", "caller", status="created")
    db.flush()
    return call


def get_call(db: Session, tenant_id: str, call_id: str) -> Call:
    call = db.get(Call, (tenant_id, call_id))
    if call is None:
        raise NotFound(call_id)
    return call


def move(db: Session, tenant_id: str, call_id: str, src: str, dst: str, actor: str, event: str, *, subject: str | None = None,
         reason: str | None = None, **detail) -> Call:
    """transition + its event, in one transaction. The event is written only if the move happened."""
    call = transition(db, tenant_id, call_id, src, dst, subject=subject, reason=reason)
    add_event(db, tenant_id, call_id, event, actor, epoch=call.epoch, **({"reason": reason} if reason else {}), **detail)
    return call


def finish(db: Session, tenant_id: str, call_id: str, *, reason: str, actor: str = "system", failed: bool = False) -> Call | None:
    """End (or fail) a call from whatever live state it is in. Idempotent: an already-terminal call is returned unchanged (None if unknown)."""
    for _ in range(4):               # the state can move under us (a takeover finishing): re-read and retry; terminal states stop the loop
        call = db.get(Call, (tenant_id, call_id))
        if call is None:
            return None
        if call.status in TERMINAL:
            return call
        try:
            dst = "failed" if failed else "ended"
            duration = max(0, int((now() - _aware(call.created_at)).total_seconds()))
            return move(db, tenant_id, call_id, call.status, dst, actor, dst, reason=reason, duration_s=duration)
        except TransitionError:
            continue
    return db.get(Call, (tenant_id, call_id))


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def list_calls(db: Session, tenant_id: str, *, keep_ended_minutes: int = 30, limit: int = 100) -> list[Call]:
    """Live calls, newest first, plus calls that ended recently (so the console can show how a call finished)."""
    rows = db.execute(select(Call).where(Call.tenant_id == tenant_id).order_by(Call.created_at.desc()).limit(limit * 3)).scalars().all()
    cutoff = now().timestamp() - keep_ended_minutes * 60
    keep = [c for c in rows if c.status in LIVE or (keep_ended_minutes and c.ended_at and _aware(c.ended_at).timestamp() >= cutoff)]
    return keep[:limit]


def list_events(db: Session, tenant_id: str, call_id: str, limit: int = 200) -> list[CallEvent]:
    get_call(db, tenant_id, call_id)
    return list(db.execute(select(CallEvent).where(CallEvent.tenant_id == tenant_id, CallEvent.call_id == call_id)
                           .order_by(CallEvent.id).limit(limit)).scalars().all())


def sweep_orphans(db: Session) -> int:
    """At startup no call can really be live (the audio lives in this process): any call still marked live belongs to a process that is gone."""
    rows = db.execute(select(Call).where(Call.status.in_(LIVE))).scalars().all()
    n = 0
    for c in rows:
        if finish(db, c.tenant_id, c.id, reason="server_restart", actor="system", failed=True):
            n += 1
    return n
