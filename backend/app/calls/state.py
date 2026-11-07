"""The call lifecycle and the ONE place ownership moves (M4).

    created --connected--> active_ai --takeover--> staff_takeover            (M4.1: a human-owned call NEVER goes back to the AI)
    (any non-terminal state) --end--> ended          (any non-terminal state) --fail--> failed          ended / failed are terminal

`returning_to_ai` (M4) is kept in the vocabulary ONLY so that a row written by M4 is still legal and readable (and can still be ended or failed):
no move leads INTO it any more, and none leads out of it except to a terminal state. The database CHECK constraint still lists it (0006 is not rewritten).

Listening is NOT a state: any number of staff may listen while the AI (or another staff member) owns the audio, and a listener
coming or going changes nothing about who speaks. Only ownership is a state, so the states cannot multiply.

`transition` is an atomic compare-and-set: `UPDATE calls SET ... WHERE id = :id AND tenant_id = :t AND status = :expected`. Two requests
racing for the same move cannot both succeed (the database lets one UPDATE match the row; the other matches nothing), on SQLite and
Postgres alike. Every successful move bumps `epoch`, which is what stale audio and replayed requests are checked against."""

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Call

TERMINAL = frozenset({"ended", "failed"})
LIVE = ("created", "active_ai", "staff_takeover", "returning_to_ai")
OWNER_OF = {"created": "none", "active_ai": "ai", "staff_takeover": "staff", "returning_to_ai": "none", "ended": "none", "failed": "none"}

ALLOWED: dict[str, frozenset[str]] = {
    "created": frozenset({"active_ai", "ended", "failed"}),
    "active_ai": frozenset({"staff_takeover", "ended", "failed"}),
    "staff_takeover": frozenset({"ended", "failed"}),
    "returning_to_ai": frozenset({"ended", "failed"}),       # legacy rows only; see the module docstring
    "ended": frozenset(),
    "failed": frozenset(),
}

# which timestamp a move stamps
_STAMP = {"active_ai": "connected_at", "staff_takeover": "takeover_at", "ended": "ended_at", "failed": "ended_at"}


class TransitionError(Exception):
    """The move is not allowed from the call's current state (or somebody else made it first). `current` is the state found."""

    def __init__(self, message: str, current: str | None = None):
        super().__init__(message)
        self.current = current


def now() -> datetime:
    return datetime.now(timezone.utc)


def can(src: str, dst: str) -> bool:
    return dst in ALLOWED.get(src, frozenset())


def transition(db: Session, tenant_id: str, call_id: str, src: str, dst: str, *, subject: str | None = None, reason: str | None = None) -> Call:
    """Move a call from `src` to `dst` atomically. Raises TransitionError if the move is not in ALLOWED or the call is no longer in `src`."""
    if not can(src, dst):
        raise TransitionError(f"{src} -> {dst} is not a valid move", src)
    values: dict = {"status": dst, "owner": OWNER_OF[dst], "epoch": Call.epoch + 1}
    if dst == "staff_takeover":
        values["owner_subject"] = subject
        values["intervention_owner"] = subject        # M4.1: the durable fact; never cleared, never reassigned (the WHERE clause below allows one takeover)
    elif dst in ("active_ai", "ended", "failed"):
        values["owner_subject"] = None
    if dst in _STAMP:
        values[_STAMP[dst]] = now()
    if dst in TERMINAL:
        values["end_reason"] = (reason or "unspecified")[:32]
    res = db.execute(update(Call).where(Call.tenant_id == tenant_id, Call.id == call_id, Call.status == src).values(**values)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.expire_all()
        row = db.get(Call, (tenant_id, call_id))
        raise TransitionError("the call is no longer in the expected state", row.status if row else None)
    db.expire_all()
    call = db.get(Call, (tenant_id, call_id))
    assert call is not None
    return call
