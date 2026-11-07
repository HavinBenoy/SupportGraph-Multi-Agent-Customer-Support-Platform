"""Trial Verification Mode settings: persisted per tenant, edited only by administrators, versioned for safe concurrent edits, every change audited."""

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.tokens import Principal
from app.calls.state import now
from app.db.models import CallRecording, TrialEvent, TrialSettings, WITHDRAWAL_POLICIES

DEFAULT_DISCLOSURE = ("You're speaking with our AI support assistant. During this trial, a member of our support team may monitor the conversation "
                      "and step in whenever additional assistance is needed.")
DEFAULT_RECORDING_NOTICE = ("If you agree, we will record the audio of this call so our team can check how well the assistant performed. "
                            "Recording is optional: if you say no, the call works exactly the same. A recording is kept for {days} days and then deleted. "
                            "You can stop the recording at any time during the call.")
TRIAL_SAFE_KEYS = frozenset({"fields", "version", "policy_version", "recording_id", "call_id", "purged", "failed", "held", "consents", "feedback",
                             "reason", "enabled", "hold", "track", "dry_run"})
TRIAL_EVENT_KINDS = frozenset({"trial_enabled", "trial_disabled", "settings_changed", "purge_run", "legal_hold_set", "legal_hold_cleared",
                               "recording_accessed", "purge_requested"})
MAX_EVENT_STR = 200


class SettingsError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class TrialConfig:
    tenant_id: str
    enabled: bool = False
    version: int = 0                       # 0 = never saved (defaults in force)
    recording_offered: bool = False
    disclosure_text: str = DEFAULT_DISCLOSURE
    recording_notice: str = DEFAULT_RECORDING_NOTICE
    consent_policy_version: int = 1
    retention_recording_days: int = 30
    retention_consent_days: int = 365
    retention_feedback_days: int = 365
    withdrawal_policy: str = "delete"
    updated_by: str | None = None
    updated_at: datetime | None = None

    def notice_text(self) -> str:
        return self.recording_notice.replace("{days}", str(self.retention_recording_days))

    def public(self) -> dict:
        d = asdict(self)
        d["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        d["recording_notice_rendered"] = self.notice_text()
        return d


def _from_row(r: TrialSettings) -> TrialConfig:
    return TrialConfig(tenant_id=r.tenant_id, enabled=bool(r.enabled), version=r.version, recording_offered=bool(r.recording_offered),
                       disclosure_text=r.disclosure_text, recording_notice=r.recording_notice, consent_policy_version=r.consent_policy_version,
                       retention_recording_days=r.retention_recording_days, retention_consent_days=r.retention_consent_days,
                       retention_feedback_days=r.retention_feedback_days, withdrawal_policy=r.withdrawal_policy, updated_by=r.updated_by, updated_at=r.updated_at)


def load(db: Session, tenant_id: str) -> TrialConfig:
    """The tenant's settings, or the defaults (mode OFF) if none were ever saved. Read from the database every time: nothing is cached, so a change
    made by an administrator takes effect for the next call and survives a restart."""
    row = db.get(TrialSettings, tenant_id)
    return _from_row(row) if row is not None else TrialConfig(tenant_id=tenant_id)


def audit(db: Session, tenant_id: str, kind: str, actor: str, **detail) -> None:
    assert kind in TRIAL_EVENT_KINDS, kind
    safe = {k: (v[:MAX_EVENT_STR] if isinstance(v, str) else v) for k, v in detail.items() if k in TRIAL_SAFE_KEYS and isinstance(v, (str, int, float, bool))}
    db.add(TrialEvent(tenant_id=tenant_id, kind=kind, actor=actor[:64], detail=safe))


def list_events(db: Session, tenant_id: str, limit: int = 100) -> list[dict]:
    rows = db.execute(select(TrialEvent).where(TrialEvent.tenant_id == tenant_id).order_by(TrialEvent.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "kind": r.kind, "actor": r.actor, "detail": r.detail, "created_at": (r.created_at.isoformat() if r.created_at else None)} for r in rows]


def _text(v, name: str, lo: int, hi: int) -> str:
    t = "".join(ch for ch in str(v) if ch == "\n" or ch >= " ").strip()
    if not (lo <= len(t) <= hi):
        raise SettingsError(422, f"{name} must be {lo}-{hi} characters")
    return t


def _days(v, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        raise SettingsError(422, f"{name} must be a whole number from {lo} to {hi}")
    return v


EDITABLE = ("enabled", "recording_offered", "disclosure_text", "recording_notice", "retention_recording_days", "retention_consent_days",
            "retention_feedback_days", "withdrawal_policy")
CONSENT_RELEVANT = ("recording_notice", "retention_recording_days", "withdrawal_policy")   # a change here means earlier consents were given under different terms


def update_settings(db: Session, p: Principal, patch: dict, expected_version: int, *, provider_available: bool) -> TrialConfig:
    """Apply an administrator's change. `expected_version` is the version the admin's screen was showing: if somebody else saved in between the
    compare-and-set matches nothing and the caller gets a 409 (no lost update). Administrators only (the route enforces it; checked again here)."""
    if p.role != "admin":
        raise SettingsError(403, "admin access required")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise SettingsError(422, "expected_version is required")
    unknown = set(patch) - set(EDITABLE)
    if unknown:
        raise SettingsError(422, f"unknown setting: {sorted(unknown)[0]}")
    cur = load(db, p.tenant_id)
    new: dict = {}
    for k, v in patch.items():
        if k in ("enabled", "recording_offered"):
            if not isinstance(v, bool):
                raise SettingsError(422, f"{k} must be true or false")
            new[k] = v
        elif k == "disclosure_text":
            new[k] = _text(v, "the customer disclosure", 20, 600)
        elif k == "recording_notice":
            new[k] = _text(v, "the recording notice", 20, 1200)
        elif k == "retention_recording_days":
            new[k] = _days(v, k, 1, 365)
        elif k in ("retention_consent_days", "retention_feedback_days"):
            new[k] = _days(v, k, 1, 3650)
        elif k == "withdrawal_policy":
            if v not in WITHDRAWAL_POLICIES:
                raise SettingsError(422, "withdrawal_policy must be delete or keep_until_expiry")
            new[k] = v
    if new.get("recording_offered") and not provider_available:
        raise SettingsError(409, "recording cannot be offered: no recording provider is configured on this server (SG_TRIAL_RECORDING_PROVIDER)")
    changed = [k for k, v in new.items() if getattr(cur, k) != v]
    if not changed:
        return cur
    bump = any(k in CONSENT_RELEVANT for k in changed)
    stamp = now()
    values = {**new, "updated_by": p.subject, "updated_at": stamp, "version": expected_version + 1}
    if bump:
        values["consent_policy_version"] = cur.consent_policy_version + 1
    if cur.version == 0:                                   # first save for this tenant: the row does not exist yet
        if expected_version != 0:
            raise SettingsError(409, "the settings changed; refresh and try again")
        base = {k: getattr(cur, k) for k in EDITABLE}
        try:
            with db.begin_nested():
                db.add(TrialSettings(tenant_id=p.tenant_id, consent_policy_version=values.get("consent_policy_version", cur.consent_policy_version),
                                     **{**base, **new}, version=1, updated_by=p.subject, updated_at=stamp))
        except IntegrityError:
            raise SettingsError(409, "the settings changed; refresh and try again") from None
    else:
        res = db.execute(update(TrialSettings).where(TrialSettings.tenant_id == p.tenant_id, TrialSettings.version == expected_version)
                         .values(**values).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise SettingsError(409, "the settings changed while you were editing; refresh and try again")
    if "retention_recording_days" in changed:              # the new period applies to recordings that already exist (never to held or purged ones)
        days = new["retention_recording_days"]
        from datetime import timedelta
        rows = db.execute(select(CallRecording).where(CallRecording.tenant_id == p.tenant_id, CallRecording.status.in_(("completed", "failed")),
                                                      CallRecording.legal_hold.is_(False), CallRecording.stopped_at.is_not(None))).scalars().all()
        for r in rows:
            r.expires_at = r.stopped_at + timedelta(days=days)
    audit_detail = {"fields": ",".join(changed), "version": expected_version + 1}
    if bump:
        audit_detail["policy_version"] = values["consent_policy_version"]
    if "enabled" in changed:
        audit(db, p.tenant_id, "trial_enabled" if new["enabled"] else "trial_disabled", p.subject, version=expected_version + 1, enabled=new["enabled"])
    others = [c for c in changed if c != "enabled"]
    if others:
        audit(db, p.tenant_id, "settings_changed", p.subject, **{**audit_detail, "fields": ",".join(others)})
    db.flush()
    db.expire_all()
    return load(db, p.tenant_id)
