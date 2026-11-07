"""Recording consent for ONE call (M5): offered, granted, declined, withdrawn. The database row is the truth; every transition is an atomic compare-and-set.

Rules that cannot be bypassed from the browser:
  * Consent comes only from an explicit decision message sent by the caller's own call connection after the notice was shown. It is NEVER inferred from silence,
    from what the caller says, or from the call simply continuing.
  * The decision names the policy version the caller was shown; a different version is refused (they would be consenting to words they did not see).
  * declined -> granted and withdrawn -> granted are allowed (an explicit change of mind); granted -> declined is not (the caller withdraws instead);
    withdrawn is final until a new explicit grant.
  * The recorder is started by the code that records a GRANT, and by nothing else (app/trial/call.py)."""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.calls import service as svc
from app.calls.state import now
from app.db.models import CallConsent

FLOW = {"granted": frozenset({"offered", "declined", "withdrawn"}), "declined": frozenset({"offered"}), "withdrawn": frozenset({"granted"})}
EVENT_OF = {"granted": "consent_granted", "declined": "consent_declined", "withdrawn": "consent_withdrawn"}


class ConsentError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def offer(db: Session, tenant_id: str, call_id: str, policy_version: int) -> None:
    if db.get(CallConsent, (tenant_id, call_id)) is None:
        stamp = now()
        db.add(CallConsent(tenant_id=tenant_id, call_id=call_id, status="offered", policy_version=policy_version, offered_at=stamp, updated_at=stamp))
        svc.add_event(db, tenant_id, call_id, "consent_offered", "system", policy_version=policy_version)


def current(db: Session, tenant_id: str, call_id: str) -> str | None:
    row = db.get(CallConsent, (tenant_id, call_id))
    return row.status if row is not None else None


def decide(db: Session, tenant_id: str, call_id: str, decision: str, policy_version: int) -> str:
    """Record the caller's decision. Returns the new status; raises ConsentError if it is not allowed from the current one."""
    if decision not in FLOW:
        raise ConsentError("unknown decision")
    row = db.get(CallConsent, (tenant_id, call_id))
    if row is None:
        raise ConsentError("recording was not offered on this call")
    if policy_version != row.policy_version:
        raise ConsentError("the recording terms changed; please read them again")
    if row.status not in FLOW[decision]:
        if row.status == decision:
            return row.status                                  # a repeated click: nothing changes
        raise ConsentError(f"cannot change from {row.status} to {decision}")
    stamp = now()
    vals: dict = {"status": decision, "updated_at": stamp}
    if decision == "withdrawn":
        vals["withdrawn_at"] = stamp
    else:
        vals["decided_at"] = stamp
    res = db.execute(update(CallConsent).where(CallConsent.tenant_id == tenant_id, CallConsent.call_id == call_id, CallConsent.status == row.status)
                     .values(**vals).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.expire_all()
        now_status = db.get(CallConsent, (tenant_id, call_id))
        if now_status is not None and now_status.status == decision:
            return decision
        raise ConsentError("the consent changed at the same moment; please try again")
    svc.add_event(db, tenant_id, call_id, EVENT_OF[decision], "caller", policy_version=policy_version)
    db.expire_all()
    return decision
