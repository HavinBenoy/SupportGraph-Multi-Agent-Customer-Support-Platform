"""Staff Live Calls (M4): list calls, listen, take over, return to the AI, end, read the audit trail, and the audio WebSocket.

REST (all staff/admin, tenant from the signed token, so another tenant's call id is simply "not found"):
  GET  /api/staff/calls                  live calls + recently ended      GET /api/staff/calls/{id}          one call
  POST /api/staff/calls/{id}/listen      validate + where to connect      POST .../takeover  .../return  .../end   (idempotent)
  GET  /api/staff/calls/{id}/events      the call's audit trail
WebSocket  /api/staff/calls/{id}/stream  (browsers cannot set headers, so the token is the first message, like the voice socket)
  client -> server   {"type":"auth","token":"..."}                       must be first
                     {"type":"mic_open","epoch":N}  {"type":"mic_close"}   the staff member who took over starts / stops speaking
                     binary: uint32-LE epoch, then PCM16 mono 16 kHz       microphone audio (refused unless it carries the CURRENT epoch)
  server -> client   {"type":"joined","call_id","owner","epoch","owner_staff","listeners","you_own",...}   {"type":"ownership","owner","epoch","staff"}
                     {"type":"mic","state":"open"|"rejected"|"closed","reason"?}   {"type":"transcript","speaker","text"}
                     {"type":"ai_audio_start","sample_rate","turn_id"} {"type":"ai_audio_end"} {"type":"call_ended","reason"} {"type":"error","detail","fatal"?}
                     binary: first byte 1 = caller PCM16 16 kHz | 2 = AI frame (uint32 turn, uint32 seq, PCM16 at the AI rate) | 3 = another staff member's PCM16 16 kHz
Origin is validated like the voice socket; the socket closes when the token expires or the account is disabled."""

import asyncio
import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from app.api.deps import principal_and_expiry, require_staff
from app.api.routes.voice import origin_allowed
from app.auth.tokens import Principal
from app.calls import service as svc
from app.calls.hub import HubError, LiveCall, StaffConn
from app.calls.state import TERMINAL
from app.db.models import Call
from app.db.session import session_scope

router = APIRouter(prefix="/api/staff/calls", tags=["calls"])
log = logging.getLogger(__name__)
AUTH_TIMEOUT_S = 10
MAX_STALE_FRAMES = 200      # a socket that keeps sending audio for an ownership that no longer exists is closed


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=None).isoformat() + "Z") if dt.tzinfo is None else dt.isoformat()


def _ref(ticket_id: str) -> str:
    """A reference to recognise the conversation by, NOT the ticket id (an anonymous ticket id is a capability to continue that chat)."""
    return ticket_id.split(".")[0][:10]


def _out(c: Call, live: LiveCall | None, p: Principal) -> dict:
    snap = live.snapshot() if live else {}
    end = c.ended_at or None
    start = c.created_at
    now = svc.now()
    aware = lambda d: d if d.tzinfo else d.replace(tzinfo=svc.timezone.utc)  # noqa: E731
    dur = int(((aware(end) if end else now) - aware(start)).total_seconds()) if start else 0
    owner = snap.get("owner", c.owner)
    owner_staff = snap.get("owner_staff", c.owner_subject)
    return {
        "id": c.id, "short_id": c.id[2:10], "status": c.status, "owner": owner, "owner_staff": owner_staff, "epoch": snap.get("epoch", c.epoch),
        "ticket_ref": _ref(c.ticket_id), "caller": "student" if c.student_id else "anonymous",
        **({"student_id": c.student_id} if p.role == "admin" and c.student_id else {}),
        "created_at": _iso(c.created_at), "connected_at": _iso(c.connected_at), "takeover_at": _iso(c.takeover_at), "returned_at": _iso(c.returned_at),
        "ended_at": _iso(c.ended_at), "end_reason": c.end_reason, "duration_s": max(0, dur),
        "live": bool(live and not live.ended), "listeners": snap.get("listeners", 0), "listening_staff": snap.get("listening_staff", []),
        "mic_open": snap.get("mic_open", False), "is_mine": owner == "staff" and owner_staff == p.subject,
        "listening": p.subject in snap.get("listening_staff", []),
    }


def _hub(request: Request):
    return request.app.state.call_hub


def _fetch(tenant_id: str, call_id: str) -> Call:
    with session_scope() as db:
        try:
            c = svc.get_call(db, tenant_id, call_id)
        except svc.NotFound:
            raise HTTPException(404, "call not found") from None
        db.expunge(c)
        return c


async def _live(request: Request, p: Principal, call_id: str) -> LiveCall:
    live = _hub(request).get(p.tenant_id, call_id)
    if live is not None and not live.ended:
        return live
    row = await asyncio.to_thread(_fetch, p.tenant_id, call_id)       # 404 for unknown / other tenant
    raise HTTPException(409, "this call has ended" if row.status in TERMINAL else "this call is not live on this server")


def _err(e: HubError) -> HTTPException:
    return HTTPException(e.status, e.detail)


@router.get("")
async def list_calls(request: Request, p: Principal = Depends(require_staff)) -> list[dict]:
    keep = request.app.state.settings.calls_keep_ended_minutes

    def load() -> list[Call]:
        with session_scope() as db:
            rows = svc.list_calls(db, p.tenant_id, keep_ended_minutes=keep)
            for r in rows:
                db.expunge(r)
            return rows

    hub = _hub(request)
    return [_out(c, hub.get(p.tenant_id, c.id), p) for c in await asyncio.to_thread(load)]


@router.get("/{call_id}")
async def get_call(call_id: str, request: Request, p: Principal = Depends(require_staff)) -> dict:
    c = await asyncio.to_thread(_fetch, p.tenant_id, call_id)
    return _out(c, _hub(request).get(p.tenant_id, call_id), p)


@router.post("/{call_id}/listen")
async def listen(call_id: str, request: Request, p: Principal = Depends(require_staff)) -> dict:
    live = await _live(request, p, call_id)
    c = await asyncio.to_thread(_fetch, p.tenant_id, call_id)
    return {**_out(c, live, p), "ws_path": f"/api/staff/calls/{call_id}/stream"}


@router.post("/{call_id}/takeover")
async def takeover(call_id: str, request: Request, p: Principal = Depends(require_staff)) -> dict:
    live = await _live(request, p, call_id)
    try:
        await live.takeover(p)
    except HubError as e:
        raise _err(e) from None
    return _out(await asyncio.to_thread(_fetch, p.tenant_id, call_id), live, p)


@router.post("/{call_id}/return")
async def return_to_ai(call_id: str, request: Request, p: Principal = Depends(require_staff)) -> dict:
    live = await _live(request, p, call_id)
    try:
        await live.return_to_ai(p)
    except HubError as e:
        raise _err(e) from None
    return _out(await asyncio.to_thread(_fetch, p.tenant_id, call_id), live, p)


@router.post("/{call_id}/end")
async def end_call(call_id: str, request: Request, p: Principal = Depends(require_staff)) -> dict:
    live = _hub(request).get(p.tenant_id, call_id)
    if live is not None:
        await live.end(p.subject, "ended_by_staff")
    else:                                    # not live here: an ended call stays ended (idempotent); a stale "live" row is cleaned up
        def close_row() -> None:
            with session_scope() as db:
                svc.get_call(db, p.tenant_id, call_id)
                svc.finish(db, p.tenant_id, call_id, reason="ended_by_staff", actor=p.subject)
        try:
            await asyncio.to_thread(close_row)
        except svc.NotFound:
            raise HTTPException(404, "call not found") from None
    return _out(await asyncio.to_thread(_fetch, p.tenant_id, call_id), None, p)


@router.get("/{call_id}/events")
async def call_events(call_id: str, limit: Annotated[int, Query(ge=1, le=500)] = 200, p: Principal = Depends(require_staff)) -> list[dict]:
    def load() -> list[dict]:
        with session_scope() as db:
            try:
                rows = svc.list_events(db, p.tenant_id, call_id, limit)
            except svc.NotFound:
                raise HTTPException(404, "call not found") from None
            return [{"id": r.id, "kind": r.kind, "actor": r.actor, "detail": r.detail, "created_at": _iso(r.created_at)} for r in rows]

    return await asyncio.to_thread(load)


# ---- the staff audio WebSocket -----------------------------------------------------------------------------------------------
async def _refuse(ws: WebSocket, detail: str, code: int = 1008) -> None:
    try:
        await ws.send_json({"type": "error", "detail": detail, "fatal": True})
        await ws.close(code=code)
    except Exception:  # noqa: BLE001
        pass


async def _writer(ws: WebSocket, conn: StaffConn) -> None:
    try:
        while True:
            kind, payload = await conn.out.get()
            if kind == "json":
                await ws.send_json(payload)
            elif kind == "bytes":
                await ws.send_bytes(payload)  # type: ignore[arg-type]
            else:
                await ws.close(code=1000 if payload == "call_ended" else 1008)
                return
    except Exception:  # noqa: BLE001 - socket gone
        return


async def _watch(conn: StaffConn, secret: str, token: str, exp: float, recheck: int) -> None:
    """Close the socket when the token expires or the account is disabled/changed (a signed token alone is not enough, as everywhere else)."""
    reason = "session expired"
    while True:
        remaining = exp - time.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(remaining, recheck) + 0.05)
        if time.time() >= exp:
            break
        try:
            await asyncio.to_thread(principal_and_expiry, secret, token)
        except HTTPException:
            reason = "session ended"
            break
        except Exception:  # noqa: BLE001
            continue
    conn.send({"type": "error", "detail": reason, "fatal": True})
    conn.close("auth_expired")


@router.websocket("/{call_id}/stream")
async def stream(ws: WebSocket, call_id: str) -> None:
    s = ws.app.state.settings
    if not origin_allowed(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT_S))
        token = first.get("token") if first.get("type") == "auth" else None
        if not isinstance(token, str):
            raise ValueError("auth required")
        principal, exp = await asyncio.to_thread(principal_and_expiry, ws.app.state.secret, token)
    except HTTPException:
        await _refuse(ws, "invalid or expired token")
        return
    except (asyncio.TimeoutError, ValueError, KeyError, WebSocketDisconnect, RuntimeError):
        await _refuse(ws, "Expected an auth message first.")
        return
    if principal.role not in ("staff", "admin"):
        await _refuse(ws, "staff access required")
        return
    live = ws.app.state.call_hub.get(principal.tenant_id, call_id)      # tenant is in the lookup: another tenant's call is "not found"
    if live is None or live.ended:
        await _refuse(ws, "call not found or already ended")
        return
    conn = StaffConn(principal, s.calls_listener_queue)
    try:
        await live.join(conn)
    except HubError as e:
        await _refuse(ws, e.detail)
        return
    writer = asyncio.create_task(_writer(ws, conn))
    watcher = asyncio.create_task(_watch(conn, ws.app.state.secret, token, exp, s.calls_recheck_s))
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                b = msg["bytes"]
                if len(b) < 4:
                    continue
                why = await live.staff_audio(conn, int.from_bytes(b[:4], "little"), b[4:])
                if why == "rate" or conn.stale_frames > MAX_STALE_FRAMES:
                    conn.send({"type": "error", "detail": "audio rejected", "fatal": True})
                    conn.close("abuse")
                    break
                continue
            try:
                data = json.loads(msg.get("text") or "")
            except ValueError:
                continue
            kind = data.get("type") if isinstance(data, dict) else None
            if kind == "mic_open" and isinstance(data.get("epoch"), int):
                why = await live.mic_open(conn, data["epoch"])
                conn.send({"type": "mic", "state": "open" if why == "ok" else "rejected", "epoch": live.epoch, **({} if why == "ok" else {"reason": why})})
            elif kind == "mic_close":
                await live.mic_close(conn)
                conn.send({"type": "mic", "state": "closed", "epoch": live.epoch})
            elif kind == "ping":
                conn.send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.warning("staff stream ended with an error")
    finally:
        # Leaving must complete even if this handler is cancelled (server shutdown, a test client closing): a staff connection that is
        # never removed would leave a dead microphone owning the call. So it runs as a task that outlives the handler.
        leaving = ws.app.state.call_hub.spawn(live.leave(conn))
        for t in (watcher, writer):
            t.cancel()
        try:
            await asyncio.gather(watcher, writer, return_exceptions=True)
            await asyncio.shield(leaving)
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
