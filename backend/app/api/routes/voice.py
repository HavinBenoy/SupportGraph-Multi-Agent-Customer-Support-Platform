"""The browser voice line: a WebSocket that carries microphone audio in and synthesised speech out, over the SAME chat turn as the text widget.

Protocol (JSON text messages unless noted):
  client -> server   {"type":"start","tenant":"qspiders_sample","token":"<bearer or null>","ticket_id":"<or null>"}   (must be first)
                     binary: PCM16 little-endian mono 16 kHz microphone audio, any chunk size <= 64 KB
                     {"type":"text","text":"..."}   typed fallback        {"type":"stop"}
                     {"type":"playback","turn_id":N,"state":"ended"}   the browser finished playing response N (M2.2)
  M2.2: every event of a turn carries "turn_id". Audio frames are binary: uint32-LE turn_id, uint32-LE sequence, then PCM16.
        "transcript" is sent twice per turn (final false, then true after the aggregation grace period); "reply" has "stale" and "turn".
  server -> client   {"type":"ready","ticket_id","signed_in","tenant_name","sample_rate"}   speech_start / transcribing / transcript / thinking
                     {"type":"reply","reply","status","citations","escalation_id","ticket_id","timing"}
                     {"type":"audio_start","sample_rate"} + binary PCM16 chunks + {"type":"audio_end","interrupted"}   {"type":"interrupted"}
                     {"type":"error","detail","fatal"?}

Identity is decided exactly as for POST /api/chat (token => tenant+student from the signed token; otherwise anonymous in the requested tenant;
ticket ids are signed and bound to that identity). Chat rate limits, analytics events, escalation, approvals and RBAC are the text path's own code.
Origin is checked here because browsers do not apply CORS to WebSockets."""

import asyncio
import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.api.deps import principal_from_token
from app.api.routes.chat import _identity, _ticket
from app.api.service import record_event, run_turn
from app.tenants.loader import load_tenant_config
from app.tools.scope import ANONYMOUS
from app.voice.session import SessionLimit, VoiceSession
from app.voice.stt import get_stt
from app.voice.tts import get_tts
from app.voice.vad import EnergyVad, SileroVad

router = APIRouter(prefix="/api/voice", tags=["voice"])
log = logging.getLogger(__name__)
START_TIMEOUT_S = 10
MAX_TEXT = 1000


def origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True  # not a browser page (script, test): it still needs a valid token/ticket to do anything
    s = ws.app.state.settings
    allowed = {o.strip().rstrip("/") for o in f"{s.voice_allowed_origins},{s.cors_origins}".split(",") if o.strip()}
    if origin.rstrip("/") in allowed:
        return True
    host = ws.headers.get("host", "")
    return urlparse(origin).netloc == host  # same-origin page


def _voice_parts(s):
    """(vad_factory, stt, tts). Built per call for the VAD (it keeps state), shared for the heavy models."""
    energy = s.voice_vad == "energy" or (s.voice_vad == "auto" and s.voice_provider == "fake")
    return ((lambda: EnergyVad()) if energy else (lambda: SileroVad(s.voice_vad_model))), get_stt(s), get_tts(s)


def _components(app, s):
    """Create the speech models once per process and reuse them (loading Whisper/Piper takes seconds)."""
    if getattr(app.state, "voice_parts", None) is None:
        app.state.voice_parts = _voice_parts(s)
    return app.state.voice_parts


def _warm(app, s) -> None:
    """Load Whisper, Piper and Silero and run each once, so the FIRST real question does not pay the model-loading time (M3).
    Runs in a background thread when the voice page asks for /status; harmless to repeat; real provider only."""
    if s.voice_provider != "real" or getattr(app.state, "voice_warm", None) is not None:
        return
    app.state.voice_warm = "running"

    def work() -> None:
        try:
            vad_factory, stt, tts = _components(app, s)
            vad_factory()
            getattr(tts, "sample_rate")
            for _ in tts.synthesize("Okay."):
                pass
            stt.transcribe(b"\x00\x00" * 16000)
            app.state.voice_warm = "ready"
        except Exception as e:  # noqa: BLE001 - a missing model is reported by /status and at call start; never crash here
            log.warning("voice warm-up failed (%s: %s)", type(e).__name__, e)
            app.state.voice_warm = "failed"

    import threading
    threading.Thread(target=work, name="voice-warm", daemon=True).start()


@router.get("/status")
def voice_status(request: Request) -> dict:
    """Is the voice line usable? Does not load anything heavy except checking the pieces exist."""
    s = request.app.state.settings
    if not s.voice_enabled:
        return {"enabled": False, "ready": False, "problems": ["voice is switched off (SG_VOICE_ENABLED=false)"], "provider": s.voice_provider}
    problems: list[str] = []
    if s.voice_provider == "real" or s.voice_vad == "silero":
        try:
            SileroVad(s.voice_vad_model)
        except Exception as e:  # noqa: BLE001
            problems.append(str(e))
    if s.voice_provider == "real":
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            problems.append("faster-whisper is not installed (pip install '.[voice]')")
        if not s.voice_tts_voice:
            problems.append("SG_VOICE_TTS_VOICE is not set (run `make voice-setup`)")
        else:
            import os
            if not os.path.isfile(s.voice_tts_voice):
                problems.append(f"Piper voice file not found: {s.voice_tts_voice}")
    if not problems:
        _warm(request.app, s)
    return {"enabled": True, "ready": not problems, "warm": getattr(request.app.state, "voice_warm", None), "problems": problems, "provider": s.voice_provider, "stt_model": s.voice_stt_model}


async def _fail(ws: WebSocket, detail: str, code: int = 1008) -> None:
    try:
        await ws.send_json({"type": "error", "detail": detail, "fatal": True})
        await ws.close(code=code)
    except Exception:  # noqa: BLE001 - already closed
        pass


@router.websocket("/ws")
async def voice_ws(ws: WebSocket) -> None:
    s = ws.app.state.settings
    if not s.voice_enabled:
        await ws.close(code=1008)
        return
    if not origin_allowed(ws):
        await ws.close(code=1008)  # before accept: the handshake is refused
        return
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), START_TIMEOUT_S))
        if first.get("type") != "start":
            raise ValueError("first message must be 'start'")
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, KeyError):
        await _fail(ws, "Expected a start message.")
        return

    # ---- identity: the text chat's own rules -------------------------------------------------------------------------------
    principal = None
    try:
        tenant = first.get("tenant") if isinstance(first.get("tenant"), str) else None
        token = first.get("token") if isinstance(first.get("token"), str) else None
        ticket = first.get("ticket_id") if isinstance(first.get("ticket_id"), str) else None
        if token:
            principal = await asyncio.to_thread(principal_from_token, ws.app.state.secret, token)
        tenant_id, student_id = _identity(ws, principal, tenant)       # type: ignore[arg-type]  (WebSocket has .app like Request)
        ticket_id = _ticket(ws, tenant_id, student_id, ticket)         # type: ignore[arg-type]
        tenant_cfg = load_tenant_config(tenant_id)
    except HTTPException as e:
        await _fail(ws, str(e.detail))
        return

    who = student_id if student_id != ANONYMOUS else (ws.client.host if ws.client else "unknown")
    vad_factory, stt, tts = _components(ws.app, s)
    try:
        vad = await asyncio.to_thread(vad_factory)
        await asyncio.to_thread(lambda: getattr(tts, "sample_rate"))
    except Exception as e:  # noqa: BLE001 - voice pieces missing: say how to fix it, do not crash
        await _fail(ws, f"Voice is not available on this server: {e}", code=1011)
        return

    def turn(text: str) -> dict:
        if not ws.app.state.chat_limiter.allow(f"chat:{tenant_id}:{who}"):  # voice shares the text chat's budget
            return {"reply": "You are talking too fast for me. Please wait a moment and try again.", "status": "needs_detail", "citations": [],
                    "escalation_id": None, "ticket_id": ticket_id}
        resp, out = run_turn(ws.app.state.graph, tenant_id=tenant_id, student_id=student_id, ticket_id=ticket_id, text=text[:MAX_TEXT],
                             today=getattr(ws.app.state, "today", None))
        record_event(tenant_id=tenant_id, ticket_id=ticket_id, signed_in=student_id != ANONYMOUS, out=out)
        return {"reply": resp.reply, "status": resp.status, "citations": [c.model_dump() for c in resp.citations],
                "escalation_id": resp.escalation_id, "ticket_id": ticket_id, "end_call": bool(out.get("end_call")),
                "intent": out.get("intent") or "", "context": out.get("context_kind") or ""}

    async def send_bytes(b: bytes) -> None:
        await ws.send_bytes(b)

    async def hang_up() -> None:  # the caller said goodbye: the closing was spoken, now end the call gracefully
        try:
            await ws.close(code=1000)
        except Exception:  # noqa: BLE001 - already closed
            pass

    session = VoiceSession(s=s, vad=vad, stt=stt, tts=tts, run_turn=turn, send_json=ws.send_json, send_bytes=send_bytes, on_end=hang_up,
                           stt_prompt=f"{tenant_cfg.name}. PySpiders, QSpiders, batch, attendance, make-up class, fee, instalment. Please end the call. Cut the call. Hang up. Goodbye.")
    await session.start()
    await ws.send_json({"type": "ready", "ticket_id": ticket_id, "signed_in": student_id != ANONYMOUS, "tenant_name": tenant_cfg.name,
                        "sample_rate": tts.sample_rate})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await session.feed_audio(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except ValueError:
                    continue
                kind = data.get("type")
                if kind == "stop":
                    break
                if kind == "playback" and data.get("state") == "ended" and isinstance(data.get("turn_id"), int):
                    session.playback_ended(data["turn_id"])   # the browser finished playing that response
                    continue
                if kind == "text" and isinstance(data.get("text"), str) and data["text"].strip():
                    await session.submit_text(data["text"].strip()[:MAX_TEXT])
    except SessionLimit as e:
        await _fail(ws, str(e), code=1000)
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
