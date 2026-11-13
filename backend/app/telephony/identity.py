"""Caller identification for the telephone channel (M8).

Caller ID is a CLAIM, never proof: it can be spoofed or withheld. So:
  * the number is normalised and looked up only to know who the caller *claims* to be (`claimed`), or that several accounts share it (`ambiguous`), or nobody
    has it (`unknown`). Every phone call STARTS AS AN ANONYMOUS CALLER: public answers only, exactly like a signed-out web visitor.
  * personal records become available only after the caller proves the claim with that account's phone-support PIN (typed on the keypad). The PIN is
    checked here, in code, against a salted scrypt hash; the language model never sees it and never decides who the caller is or what they may access.
  * failed PINs are counted atomically in the database (several calls at once cannot exceed the limit) and lock phone verification for a while.
  * an ambiguous (shared) number cannot be verified by PIN at all: the caller is offered a person instead.
Nothing here stores or logs a raw number: calls keep a masked form for staff and a keyed hash to group repeat calls."""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

import secrets

from app.auth.passwords import N, P, R, verify_password
from app.db.models import Account, AccountPhone, PhonePin, Student, TelephonyAudit

UNKNOWN, CLAIMED, AMBIGUOUS, VERIFIED = "unknown", "claimed", "ambiguous", "verified"
_DIGITS = re.compile(r"\D")


def normalise(raw: str | None, default_cc: str = "91") -> str | None:
    """E.164 (`+<country><number>`) or None. Accepts '+91 98765 43210', '09876543210', '919876543210', '9876543210' (=> +91...)."""
    if not raw:
        return None
    s = raw.strip()
    plus = s.startswith("+")
    d = _DIGITS.sub("", s)
    if s.startswith("00"):
        d, plus = d[2:], True
    if not d:
        return None
    if not plus:
        if len(d) == 11 and d.startswith("0"):
            d = default_cc + d[1:]
        elif len(d) == 10:
            d = default_cc + d
    if not 8 <= len(d) <= 15:
        return None
    return "+" + d


def mask(e164: str | None) -> str | None:
    """'+91 ******3210': enough for staff to recognise a repeat caller, not enough to dial."""
    if not e164:
        return None
    cc = e164[1:3] if e164.startswith("+91") else e164[1:-10] or e164[1:3]
    return f"+{cc} {'*' * 6}{e164[-4:]}"


def number_hash(secret: str, tenant_id: str, e164: str | None) -> str | None:
    if not e164:
        return None
    return hmac.new(secret.encode(), f"phone:{tenant_id}:{e164}".encode(), hashlib.sha256).hexdigest()[:32]


@dataclass(frozen=True)
class Candidate:
    username: str
    role: str
    student_id: str | None


@dataclass(frozen=True)
class Lookup:
    status: str                       # unknown | claimed | ambiguous
    candidate: Candidate | None       # only when claimed
    role: str | None                  # the claimed role (None unless claimed)


def lookup(db: Session, tenant_id: str, e164: str | None, default_cc: str = "91") -> Lookup:
    """Who may this number belong to? Students by `students.phone` (stored in various formats: normalised here), staff and administrators by
    `account_phones`. Disabled accounts are ignored. Tenant-scoped."""
    if not e164:
        return Lookup(UNKNOWN, None, None)
    found: dict[str, Candidate] = {}
    for st in db.execute(select(Student).where(Student.tenant_id == tenant_id, Student.phone.is_not(None))).scalars():
        if normalise(st.phone, default_cc) == e164:
            for acc in db.execute(select(Account).where(Account.tenant_id == tenant_id, Account.student_id == st.id, Account.disabled.is_(False))).scalars():
                found[acc.username] = Candidate(acc.username, acc.role, st.id)
    for ph, acc in db.execute(select(AccountPhone, Account).join(Account, (Account.tenant_id == AccountPhone.tenant_id) & (Account.username == AccountPhone.username))
                              .where(AccountPhone.tenant_id == tenant_id, AccountPhone.phone_e164 == e164, Account.disabled.is_(False))).all():
        found[acc.username] = Candidate(acc.username, acc.role, acc.student_id)
    if not found:
        return Lookup(UNKNOWN, None, None)
    if len(found) > 1:
        return Lookup(AMBIGUOUS, None, None)
    c = next(iter(found.values()))
    return Lookup(CLAIMED, c, c.role)


# ---------------------------------------------------------------------------------------------------------------------------- PINs
class PinError(ValueError):
    pass


def _weak(pin: str) -> bool:
    if len(set(pin)) == 1:
        return True
    asc = "0123456789012345"
    return pin in asc or pin in asc[::-1]


def validate_pin(pin: str, length: int) -> str:
    if not isinstance(pin, str) or not pin.isdigit() or len(pin) != length:
        raise PinError(f"the PIN must be exactly {length} digits")
    if _weak(pin):
        raise PinError("choose a less predictable PIN (not all the same digit, not a run like 123456)")
    return pin


def hash_pin(pin: str) -> str:
    """The password module's scrypt format (salted, memory-hard), without its 10-character minimum: a PIN is short by design, which is why it is only
    accepted with the account's lockout and only on the telephone channel."""
    salt = secrets.token_bytes(16)
    return f"scrypt${N}${R}${P}${salt.hex()}${hashlib.scrypt(pin.encode(), salt=salt, n=N, r=R, p=P, dklen=32).hex()}"


_DUMMY_HASH: str | None = None


def _dummy() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_pin("000000")
    return _DUMMY_HASH


def audit(db: Session, tenant_id: str, actor: str, action: str, subject: str | None = None) -> None:
    db.add(TelephonyAudit(tenant_id=tenant_id, actor=actor[:64], action=action[:40], subject=(subject or None) and subject[:64]))


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pin_status(db: Session, tenant_id: str, username: str) -> dict:
    row = db.get(PhonePin, (tenant_id, username))
    locked = _aware(row.locked_until) if row else None
    return {"has_pin": row is not None, "locked_until": locked.isoformat() if locked and locked > now() else None,
            "updated_at": (_aware(row.updated_at) or _aware(row.created_at)).isoformat() if row and (row.updated_at or row.created_at) else None}


def set_pin(db: Session, tenant_id: str, username: str, pin: str, length: int) -> None:
    validate_pin(pin, length)
    row = db.get(PhonePin, (tenant_id, username))
    h = hash_pin(pin)
    if row is None:
        db.add(PhonePin(tenant_id=tenant_id, username=username, pin_hash=h, failed_attempts=0, created_at=now(), updated_at=now()))
    else:
        row.pin_hash, row.failed_attempts, row.locked_until, row.updated_at = h, 0, None, now()
    audit(db, tenant_id, username, "pin_set", username)


def clear_pin(db: Session, tenant_id: str, username: str, actor: str, action: str = "pin_cleared") -> bool:
    row = db.get(PhonePin, (tenant_id, username))
    if row is None:
        return False
    db.delete(row)
    audit(db, tenant_id, actor, action, username)
    return True


def verify_pin(db: Session, tenant_id: str, username: str, pin: str, *, max_attempts: int, lock_minutes: int) -> str:
    """'ok' | 'wrong' | 'locked' | 'no_pin'.

    The attempt is RESERVED before the PIN is checked: one UPDATE ... SET failed_attempts = failed_attempts + 1 WHERE <not locked> RETURNING. On PostgreSQL
    that row stays locked until this transaction ends, so parallel calls guessing the same account are checked one after another, and a guess that arrives
    after the limit was reached finds the lock and is refused WITHOUT being compared with the hash. Reaching the limit with a wrong PIN sets the lock (and
    restarts the counter for after it expires). A correct PIN resets the counter."""
    t = now()
    row = db.execute(update(PhonePin).where(PhonePin.tenant_id == tenant_id, PhonePin.username == username,
                                            PhonePin.locked_until.is_(None) | (PhonePin.locked_until <= t))
                     .values(failed_attempts=PhonePin.failed_attempts + 1).returning(PhonePin.failed_attempts, PhonePin.pin_hash)
                     .execution_options(synchronize_session=False)).first()
    if row is None:
        exists = db.get(PhonePin, (tenant_id, username)) is not None
        if not exists:
            verify_password(pin or "0", _dummy())                          # same work as a real check: the answer time does not reveal whether a PIN exists
        return "locked" if exists else "no_pin"
    attempts, stored = row
    if verify_password(pin, stored):
        db.execute(update(PhonePin).where(PhonePin.tenant_id == tenant_id, PhonePin.username == username)
                   .values(failed_attempts=0, locked_until=None).execution_options(synchronize_session=False))
        return "ok"
    if attempts >= max_attempts:
        db.execute(update(PhonePin).where(PhonePin.tenant_id == tenant_id, PhonePin.username == username)
                   .values(locked_until=t + timedelta(minutes=lock_minutes), failed_attempts=0).execution_options(synchronize_session=False))
        audit(db, tenant_id, "system", "pin_locked", username)
        return "locked"
    return "wrong"
