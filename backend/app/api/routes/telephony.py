"""M8: the telephone line and its administration.

Telephone media (the provider connects to us; disabled unless SG_TELEPHONY_PROVIDER=edesy and a stream token of >= 32 characters is set):
  WS   /api/telephony/edesy/stream/{token}      one Edesy media stream = one call (protocol: app/telephony/edesy.py)
A call runs on the SAME machinery as the browser voice line: VoiceSession (VAD, STT, the LangGraph turn, TTS), the call hub (Live Calls, listening,
takeover with its hand-over announcement, staff audio, ending) and Trial Verification Mode (spoken disclosure, recording consent). Only the edges differ:
audio is converted by app/telephony/media.py, and the keypad replaces the browser buttons:
  * PIN    `*` + digits + `#`   proves the caller is the account their number claims (app/telephony/identity.py). Until then the caller is ANONYMOUS.
  * trial  1 = allow recording, 2 = continue without recording, 3 = stop recording (only while a recording consent is offered on this call)

Administration (administrators of the caller's own tenant):
  GET    /api/admin/telephony/status            provider, whether it is configured, line settings, PIN rules, problems (never the token)
  GET    /api/admin/telephony/accounts          accounts with masked number, PIN set / locked
  PUT    /api/admin/telephony/phones/{user}     {phone}  number of a STAFF or ADMIN account (a student's number is their own record)    DELETE removes it
  DELETE /api/admin/telephony/pins/{user}       reset (remove) someone's phone PIN, e.g. after a lockout or a lost PIN
  GET    /api/admin/telephony/summary           phone-call counts (?days=1..365)             GET /api/admin/telephony/audit   the telephony trail
Every signed-in account (its own PIN only):
  GET /api/me/phone-pin     PUT {current_password, pin}     DELETE {current_password}"""

import asyncio
import hmac
import json
import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from app.api.deps import require_admin, require_principal
from app.api.routes.voice import _components
from app.api.service import record_event, run_turn
from app.auth.accounts import authenticate
from app.auth.tokens import Principal, new_anonymous_ticket, new_student_ticket
from app.db.models import Account, AccountPhone, Call, PhonePin, Student, TelephonyAudit
from app.db.session import session_scope
from app.telephony import identity as idn
from app.telephony.base import ProtocolError
from app.telephony.edesy import get_adapter
from app.telephony.media import PhoneBridge
from app.tenants.loader import load_tenant_config
from app.tools.scope import ANONYMOUS
from app.voice.session import SessionLimit, VoiceSession

router = APIRouter(tags=["telephony"])
log = logging.getLogger(__name__)
START_TIMEOUT_S = 10
MIN_TOKEN = 32
MAX_TEXT = 1000

GREETING = ("Hello! You are speaking with the {name} AI support assistant. "
            "If you are a registered student and want to hear your own records, press star, then your phone PIN, then the hash key. "
            "Otherwise, just ask your question.")
CONSENT_OPTIONS = "To allow recording, press 1. To continue without recording, press 2. You can press 3 at any time to stop recording."


# ---------------------------------------------------------------------------------------------------------------------------- helpers
def _token(s) -> str:
    return s.telephony_stream_token.get_secret_value() if s.telephony_stream_token else ""


def problems(s) -> list[str]:
    out = []
    if s.telephony_provider == "none":
        out.append("The telephone line is switched off (SG_TELEPHONY_PROVIDER=none). Enabling it uses a paid provider: see docs/M8.md.")
        return out
    if len(_token(s)) < MIN_TOKEN:
        out.append(f"SG_TELEPHONY_STREAM_TOKEN is missing or shorter than {MIN_TOKEN} characters: every call is refused.")
    if not s.voice_enabled:
        out.append("The voice line is switched off (SG_VOICE_ENABLED=false): phone calls need the same speech models.")
    return out


def tenant_for(s, dialled: str) -> str:
    want = idn.normalise(dialled, s.telephony_default_country)
    for pair in s.telephony_number_tenants.split(","):
        num, _, tenant = pair.partition("=")
        if num.strip() and tenant.strip() and idn.normalise(num.strip(), s.telephony_default_country) == want:
            return tenant.strip()
    return s.telephony_tenant


def _iso(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=idn.timezone.utc)).isoformat()


# ---------------------------------------------------------------------------------------------------------------------------- admin
@router.get("/api/admin/telephony/status")
def status(request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    return {"provider": s.telephony_provider, "enabled": s.telephony_provider != "none", "ready": s.telephony_provider != "none" and not problems(s),
            "problems": problems(s), "stream_path": "/api/telephony/edesy/stream/<token>" if s.telephony_provider == "edesy" else None,
            "token_configured": len(_token(s)) >= MIN_TOKEN, "sample_rate": s.telephony_sample_rate, "default_tenant": s.telephony_tenant,
            "ip_allowlist": bool(s.telephony_allowed_ips.strip()), "max_concurrent": s.telephony_max_concurrent,
            "active_phone_calls": getattr(request.app.state, "phone_calls", 0),
            "pin": {"length": s.phone_pin_length, "max_attempts": s.phone_pin_max_attempts, "lock_minutes": s.phone_pin_lock_minutes,
                    "attempts_per_call": s.phone_pin_attempts_per_call},
            "paid_service": True, "approval_note": "A public telephone number is a paid service (number rental, per-minute charges). It stays off until you approve it."}


@router.get("/api/admin/telephony/accounts")
def accounts(request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    with session_scope() as db:
        accs = db.execute(select(Account).where(Account.tenant_id == p.tenant_id).order_by(Account.role, Account.username).limit(1000)).scalars().all()
        phones = {a.username: a.phone_e164 for a in db.execute(select(AccountPhone).where(AccountPhone.tenant_id == p.tenant_id)).scalars()}
        studs = {st.id: st.phone for st in db.execute(select(Student).where(Student.tenant_id == p.tenant_id)).scalars()}
        pins = {r.username: r for r in db.execute(select(PhonePin).where(PhonePin.tenant_id == p.tenant_id)).scalars()}
        now = idn.now()
        out = []
        for a in accs:
            num = idn.normalise(studs.get(a.student_id), s.telephony_default_country) if a.role == "student" else phones.get(a.username)
            pin = pins.get(a.username)
            lock = idn._aware(pin.locked_until) if pin else None
            out.append({"username": a.username, "role": a.role, "disabled": bool(a.disabled), "phone_masked": idn.mask(num),
                        "phone_source": "student record" if a.role == "student" else "administrator" if num else None,
                        "has_pin": pin is not None, "locked_until": lock.isoformat() if lock and lock > now else None})
    return {"items": out}


class PhoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(min_length=6, max_length=24)


def _staff_account(db, tenant_id: str, username: str) -> Account:
    a = db.get(Account, (tenant_id, username))
    if a is None:
        raise HTTPException(404, "no such account")
    if a.role == "student":
        raise HTTPException(422, "a student's number is part of their student record; it changes only through their own confirmed request")
    return a


@router.put("/api/admin/telephony/phones/{username}")
def set_phone(username: str, body: PhoneIn, request: Request, p: Principal = Depends(require_admin)) -> dict:
    s = request.app.state.settings
    e164 = idn.normalise(body.phone, s.telephony_default_country)
    if e164 is None:
        raise HTTPException(422, "not a telephone number")
    with session_scope() as db:
        _staff_account(db, p.tenant_id, username)
        row = db.get(AccountPhone, (p.tenant_id, username))
        if row is None:
            db.add(AccountPhone(tenant_id=p.tenant_id, username=username, phone_e164=e164, created_by=p.subject, created_at=idn.now()))
        else:
            row.phone_e164, row.created_by = e164, p.subject
        idn.audit(db, p.tenant_id, p.subject, "phone_set", username)
    return {"username": username, "phone_masked": idn.mask(e164)}


@router.delete("/api/admin/telephony/phones/{username}")
def delete_phone(username: str, p: Principal = Depends(require_admin)) -> dict:
    with session_scope() as db:
        _staff_account(db, p.tenant_id, username)
        row = db.get(AccountPhone, (p.tenant_id, username))
        if row is None:
            return {"removed": False}
        db.delete(row)
        idn.audit(db, p.tenant_id, p.subject, "phone_removed", username)
    return {"removed": True}


@router.delete("/api/admin/telephony/pins/{username}")
def reset_pin(username: str, p: Principal = Depends(require_admin)) -> dict:
    with session_scope() as db:
        if db.get(Account, (p.tenant_id, username)) is None:
            raise HTTPException(404, "no such account")
        return {"reset": idn.clear_pin(db, p.tenant_id, username, p.subject, "pin_reset_by_admin")}


@router.get("/api/admin/telephony/summary")
def summary(days: Annotated[int, Query(ge=1, le=365)] = 30, p: Principal = Depends(require_admin)) -> dict:
    since = idn.now() - timedelta(days=days)
    with session_scope() as db:
        rows = db.execute(select(Call.channel, Call.caller_status, Call.takeover_at.is_not(None), Call.status, func.count())
                          .where(Call.tenant_id == p.tenant_id, Call.created_at >= since)
                          .group_by(Call.channel, Call.caller_status, Call.takeover_at.is_not(None), Call.status)).all()
    phone = {"total": 0, "verified": 0, "claimed_unverified": 0, "unknown": 0, "ambiguous": 0, "taken_over": 0, "failed": 0}
    web = 0
    for ch, st, took, status_, n in rows:
        if ch != "phone":
            web += n
            continue
        phone["total"] += n
        key = {"verified": "verified", "claimed": "claimed_unverified", "ambiguous": "ambiguous"}.get(st or "", "unknown")
        phone[key] += n
        phone["taken_over"] += n if took else 0
        phone["failed"] += n if status_ == "failed" else 0
    return {"days": days, "phone": phone, "web_calls": web}


@router.get("/api/admin/telephony/audit")
def audit_trail(limit: Annotated[int, Query(ge=1, le=500)] = 100, p: Principal = Depends(require_admin)) -> dict:
    with session_scope() as db:
        rows = db.execute(select(TelephonyAudit).where(TelephonyAudit.tenant_id == p.tenant_id).order_by(TelephonyAudit.id.desc()).limit(limit)).scalars().all()
        return {"items": [{"id": r.id, "at": _iso(r.created_at), "actor": r.actor, "action": r.action, "subject": r.subject} for r in rows]}


# ---------------------------------------------------------------------------------------------------------------------------- own PIN
class PinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=200)
    pin: str = Field(min_length=4, max_length=8)


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=200)


def _reauth(request: Request, p: Principal, password: str) -> None:
    key = f"pin:{p.tenant_id}:{p.subject}"
    lim = request.app.state.login_limiter
    if lim.blocked(key):
        raise HTTPException(429, "too many attempts, wait a few minutes", headers={"Retry-After": "300"})
    with session_scope() as db:
        ok = authenticate(db, p.tenant_id, p.subject, password) is not None
    if not ok:
        lim.record(key)
        raise HTTPException(403, "the password is not correct")


@router.get("/api/me/phone-pin")
def my_pin(request: Request, p: Principal = Depends(require_principal)) -> dict:
    s = request.app.state.settings
    with session_scope() as db:
        return {**idn.pin_status(db, p.tenant_id, p.subject), "length": s.phone_pin_length, "phone_line": s.telephony_provider != "none"}


@router.put("/api/me/phone-pin")
def set_my_pin(body: PinIn, request: Request, p: Principal = Depends(require_principal)) -> dict:
    s = request.app.state.settings
    try:
        idn.validate_pin(body.pin, s.phone_pin_length)
    except idn.PinError as e:
        raise HTTPException(422, str(e)) from None
    _reauth(request, p, body.current_password)
    with session_scope() as db:
        idn.set_pin(db, p.tenant_id, p.subject, body.pin, s.phone_pin_length)
    with session_scope() as db:
        return idn.pin_status(db, p.tenant_id, p.subject)


@router.delete("/api/me/phone-pin")
def clear_my_pin(request: Request, body: PasswordIn = Body(...), p: Principal = Depends(require_principal)) -> dict:
    _reauth(request, p, body.current_password)
    with session_scope() as db:
        return {"removed": idn.clear_pin(db, p.tenant_id, p.subject, p.subject)}


# ---------------------------------------------------------------------------------------------------------------------------- the call
class Keypad:
    """DTMF on one call: PIN entry (`*` digits `#`) and, while a recording consent is offered, 1 / 2 / 3. Identity changes happen only here, in code."""

    def __init__(self, call: "PhoneCall"):
        self.c = call
        self.buf: str | None = None
        self.attempts = 0
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)     # keys are handled strictly in the order pressed, one at a time

    def put(self, d: str) -> None:
        try:
            self.queue.put_nowait(d)
        except asyncio.QueueFull:                                       # a flood of key events is dropped, not buffered without bound
            pass

    async def run(self) -> None:
        while True:
            d = await self.queue.get()
            try:
                await self.press(d)
            except Exception:  # noqa: BLE001 - one bad key never stops the keypad
                log.warning("keypad handling failed", exc_info=True)

    async def press(self, d: str) -> None:
        if d == "*":
            self.buf = ""
            return
        if self.buf is not None:
            if d == "#":
                pin, self.buf = self.buf, None
                await self.c.verify(pin)
            elif d.isdigit():
                self.buf += d
                if len(self.buf) > 8:
                    self.buf = None
                    await self.c.say("That was too many digits. Press star to start again.")
            return
        if d in "123" and self.c.live is not None and self.c.live.trial is not None and self.c.live.trial.offered:
            decision = {"1": "granted", "2": "declined", "3": "withdrawn"}[d]
            await self.c.live.trial.handle({"type": "consent", "decision": decision, "policy_version": self.c.live.trial.cfg.consent_policy_version})


class PhoneCall:
    def __init__(self, ws: WebSocket, s, tenant_id: str, tenant_name: str, lookup: idn.Lookup, secret: str):
        self.ws, self.s, self.tenant_id, self.tenant_name, self.lk, self.secret = ws, s, tenant_id, tenant_name, lookup, secret
        self.student_id = ANONYMOUS
        self.ticket = new_anonymous_ticket(secret, tenant_id)
        self.status = lookup.status
        self.live = None
        self.keypad = Keypad(self)

    async def say(self, text: str, kind: str = "phone_prompt") -> None:
        live = self.live
        if live is None or live.ended or live.owner != "ai":
            return                                         # after a takeover only the staff member speaks (M4.1): no prompt ever overlaps them
        await live.play_notice(text, owner="ai", epoch=live.epoch, kind=kind)

    async def _event(self, kind: str, **detail) -> None:
        if self.live is not None:
            await self.live._event(kind, "caller", **detail)

    async def verify(self, pin: str) -> None:
        s = self.s
        self.keypad.attempts += 1
        if self.status == idn.VERIFIED:
            await self.say("You are already verified on this call.")
            return
        if self.keypad.attempts > s.phone_pin_attempts_per_call:
            await self.say("No more PIN attempts are possible on this call. You can still ask general questions, or ask for a person.")
            return
        if self.lk.status == idn.AMBIGUOUS:
            await self._event("caller_verification_failed", result="ambiguous")
            await self.say("This number belongs to more than one account, so it cannot be verified by phone. You can ask for a person.")
            return
        cand = self.lk.candidate

        def check():
            with session_scope() as db:
                if cand is None:
                    idn.verify_password(pin, idn._dummy())     # same work for an unknown number: the timing does not reveal it
                    return "wrong"
                r = idn.verify_pin(db, self.tenant_id, cand.username, pin, max_attempts=s.phone_pin_max_attempts, lock_minutes=s.phone_pin_lock_minutes)
                return "wrong" if r == "no_pin" else r          # "no PIN set" and "wrong PIN" sound the same to a caller
        res = await asyncio.to_thread(check)
        if res != "ok":
            await self._event("caller_verification_failed", result=res)
            if res == "locked":
                await self.say("Phone verification is locked for a while after too many wrong PINs. You can still ask general questions, or ask for a person.")
            else:
                await self.say("That PIN is not correct. Press star to try again.")
            return
        await self._apply_verified(cand)

    async def _apply_verified(self, cand: idn.Candidate) -> None:
        self.status = idn.VERIFIED
        student = cand.student_id if cand.role == "student" and cand.student_id else None
        if student:
            self.student_id = student
            self.ticket = new_student_ticket(self.secret, self.tenant_id, student)
        live = self.live

        def persist():
            with session_scope() as db:
                vals = {"caller_status": idn.VERIFIED, "verified_at": idn.now()}
                if student:
                    vals.update(student_id=student, ticket_id=self.ticket)
                db.execute(update(Call).where(Call.tenant_id == self.tenant_id, Call.id == live.id).values(**vals).execution_options(synchronize_session=False))
        if live is not None:
            await asyncio.to_thread(persist)
            if student:
                live.student_id, live.ticket_id = student, self.ticket
            await self._event("caller_verified", role=cand.role)
        if student:
            await self.say("Thank you, you are verified. You can now ask about your own courses, attendance, results and fees.")
        else:
            await self.say("Thank you, you are verified as a member of staff. On the phone I can answer general questions; staff tools are in the web console.")

    def turn(self, text: str) -> dict:
        who = self.student_id if self.student_id != ANONYMOUS else f"phone:{self.ws.client.host if self.ws.client else 'x'}:{self.ticket[-8:]}"
        if not self.ws.app.state.chat_limiter.allow(f"chat:{self.tenant_id}:{who}"):
            return {"reply": "You are talking too fast for me. Please wait a moment and try again.", "status": "needs_detail", "citations": [],
                    "escalation_id": None, "ticket_id": self.ticket}
        resp, out = run_turn(self.ws.app.state.graph, tenant_id=self.tenant_id, student_id=self.student_id, ticket_id=self.ticket, text=text[:MAX_TEXT],
                             today=getattr(self.ws.app.state, "today", None))
        record_event(tenant_id=self.tenant_id, ticket_id=self.ticket, signed_in=self.student_id != ANONYMOUS, out=out)
        return {"reply": resp.reply, "status": resp.status, "citations": [c.model_dump() for c in resp.citations], "escalation_id": resp.escalation_id,
                "ticket_id": self.ticket, "end_call": bool(out.get("end_call")), "intent": out.get("intent") or "", "context": out.get("context_kind") or "",
                "node_ms": out.get("node_ms") or {}, "perf": out.get("perf") or {}}


@router.websocket("/api/telephony/edesy/stream/{token}")
async def edesy_stream(ws: WebSocket, token: str) -> None:
    s = ws.app.state.settings
    secret_token = _token(s)
    if s.telephony_provider != "edesy" or len(secret_token) < MIN_TOKEN or not hmac.compare_digest(token.encode(), secret_token.encode()):
        await ws.close(code=1008)                          # refused before the handshake completes; nothing about the reason is revealed
        return
    allowed = {ip.strip() for ip in s.telephony_allowed_ips.split(",") if ip.strip()}
    if allowed and (ws.client is None or ws.client.host not in allowed):
        await ws.close(code=1008)
        return
    if not s.voice_enabled:
        await ws.close(code=1013)
        return
    app = ws.app
    if getattr(app.state, "phone_calls", 0) >= s.telephony_max_concurrent:
        await ws.close(code=1013)                          # try again later: the provider plays its own failure handling
        return
    await ws.accept()
    adapter = get_adapter(s.telephony_provider, s.telephony_sample_rate)
    try:
        start = adapter.parse_start(await asyncio.wait_for(ws.receive_text(), START_TIMEOUT_S))
        tenant_id = tenant_for(s, start.to_number)
        tenant_cfg = load_tenant_config(tenant_id)
    except (asyncio.TimeoutError, ProtocolError, WebSocketDisconnect, ValueError, KeyError, FileNotFoundError, OSError) as e:
        log.warning("telephone stream refused: %s", type(e).__name__)
        try:
            await ws.close(code=1008)
        except Exception:  # noqa: BLE001
            pass
        return

    app.state.phone_calls = getattr(app.state, "phone_calls", 0) + 1
    try:
        await _run_call(ws, s, adapter, start, tenant_id, tenant_cfg)
    finally:
        app.state.phone_calls = max(0, getattr(app.state, "phone_calls", 1) - 1)


async def _run_call(ws: WebSocket, s, adapter, start, tenant_id: str, tenant_cfg) -> None:
    app = ws.app
    e164 = idn.normalise(start.from_number, s.telephony_default_country)

    def find():
        with session_scope() as db:
            return idn.lookup(db, tenant_id, e164, s.telephony_default_country)
    try:
        lk = await asyncio.to_thread(find)
    except Exception:  # noqa: BLE001 - e.g. migration 0014 not applied: nobody is recognised, the call still works anonymously
        log.warning("caller lookup failed; the call runs as an anonymous caller. Run `make db-migrate`.")
        lk = idn.Lookup(idn.UNKNOWN, None, None)
    call = PhoneCall(ws, s, tenant_id, tenant_cfg.name, lk, app.state.secret)

    async def send_text(msg: dict) -> None:
        await ws.send_text(json.dumps(msg))

    async def close_line() -> None:
        await ws.close(code=1000)
    bridge = PhoneBridge(adapter, start.sample_rate, send_text, ws.send_bytes, close_line)

    vad_factory, stt, tts = _components(app, s)
    try:
        vad = await asyncio.to_thread(vad_factory)
        await asyncio.to_thread(lambda: getattr(tts, "sample_rate"))
    except Exception:  # noqa: BLE001 - speech models missing: hang up rather than leave the caller in silence
        log.warning("voice models are not available; the telephone call is ended")
        await bridge.hang_up()
        return

    hub = app.state.call_hub
    trial_cfg = None
    try:
        from app.trial import settings as trial_settings

        def read_trial():
            with session_scope() as db:
                return trial_settings.load(db, tenant_id)
        trial_cfg = await asyncio.to_thread(read_trial)
    except Exception:  # noqa: BLE001
        log.warning("trial settings could not be read; the call runs in normal mode")
    in_trial = bool(trial_cfg and trial_cfg.enabled)
    phone = {"caller_status": lk.status, "caller_role": lk.role, "caller_masked": idn.mask(e164), "caller_hash": idn.number_hash(app.state.secret, tenant_id, e164),
             "provider": adapter.name, "provider_call_ref": start.call_ref or None}

    async def on_status(msg: dict) -> None:            # the trial's consent results, which a browser would show, are spoken on a telephone line
        if msg.get("type") == "recording":
            await call.say("Recording is on." if msg.get("state") == "on" else "Recording is off.")
        elif msg.get("type") == "consent" and msg.get("error"):
            await call.say("Sorry, the recording setting could not be changed.")

    async def to_line(msg: dict) -> None:
        await bridge.on_json(msg)
        if msg.get("type") in ("recording", "consent"):
            hub.spawn(on_status(msg))
    live = None
    try:
        live = await hub.open(tenant_id, call.ticket, None, send_json=to_line, send_bytes=bridge.on_bytes, close_caller=bridge.hang_up, trial_mode=in_trial,
                              channel="phone", phone=phone)
        call.live = live
        await live._event("caller_identified", "system", caller_status=lk.status, channel="phone", provider=adapter.name)
    except Exception as e:  # noqa: BLE001
        log.warning("phone call could not be registered (%s); run `make db-migrate`. It continues without staff features.", type(e).__name__)

    async def on_goodbye() -> None:
        if live is not None:
            live.end_hint = "goodbye"
        await bridge.hang_up()

    session = VoiceSession(s=s, vad=vad, stt=stt, tts=tts, run_turn=call.turn, send_json=live.from_ai_json if live else to_line,
                           send_bytes=live.from_ai_bytes if live else bridge.on_bytes, on_end=on_goodbye, tap_caller=live.tap_caller_audio if live else None,
                           stt_prompt=f"{tenant_cfg.name}. PySpiders, QSpiders, batch, attendance, make-up class, fee, instalment. Goodbye.")
    bridge.on_played = session.playback_ended
    bridge.on_started = session.playback_started
    if live is not None:
        live.attach_session(session)
    await session.start()
    if live is not None:
        await live.mark_connected()

    async def opening() -> None:
        if live is not None and in_trial:
            from app.trial.call import TrialCall
            live.trial = TrialCall(live, trial_cfg, app.state.recording_provider)
            try:
                await live.trial.start()                   # the spoken disclosure (AI, may be monitored, may be taken over, recording policy)
            except Exception:  # noqa: BLE001
                log.warning("trial notice could not be started")
            if live.trial.offered:
                await call.say(CONSENT_OPTIONS, kind="consent_options")
            await call.say("If you are a registered student and want to hear your own records, press star, then your phone PIN, then the hash key.")
        else:
            await call.say(GREETING.format(name=tenant_cfg.name), kind="greeting")
    opener = hub.spawn(opening()) if live is not None else None
    keypad = asyncio.ensure_future(call.keypad.run())

    outcome, failed = "caller_disconnected", False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await session.feed_audio(bridge.caller_audio(msg["bytes"]))
            elif msg.get("text") is not None:
                ev = adapter.parse_event(msg["text"])
                if ev.kind == "dtmf" and ev.digit:
                    call.keypad.put(ev.digit)
                elif ev.kind == "mark":
                    bridge.on_mark(ev.name)
    except SessionLimit:
        outcome = "limit"
        await bridge.hang_up()
    except WebSocketDisconnect:
        pass
    except RuntimeError:                                   # the socket was closed by our own hang-up
        pass
    except Exception:
        outcome, failed = "error", True
        raise
    finally:
        if opener is not None and not opener.done():
            opener.cancel()
        keypad.cancel()
        await asyncio.gather(keypad, return_exceptions=True)
        try:
            await session.close()
        finally:
            if live is not None:
                await asyncio.shield(hub.spawn(live.end("caller" if not live.end_hint else "ai", live.end_hint or outcome, failed=failed, close_caller=False,
                                                        participant="caller" if outcome == "caller_disconnected" and not live.end_hint else None)))
        bridge.closed = True
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
