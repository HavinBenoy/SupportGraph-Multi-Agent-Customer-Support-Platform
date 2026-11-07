"""Model lifecycle: load everything the first call needs, once, in the background, one piece at a time (M3).

Why: a cold first call paid for model loading and lazy initialisation on top of its own work (11.1 s vs 6.0 s for the same question).
What is warmed, in this order (sequential on purpose: on a 4-core CPU, loading two models at once makes both slower):

  1. `ollama`     chat models and the embedding model preloaded with the SAME runner options the real calls use (llm.preflight.warm_up)
  2. `triage`     one real structured-output triage call on a fixed synthetic sentence (allocates the KV cache, compiles the grammar)
  3. `retrieval`  one embedding call and one database round trip (HTTP connection, connection pool)
  4. `voice`      Silero VAD, Piper (one short synthesis) and Whisper (one second of silence)            [voice line only]

Rules: it never blocks the event loop or a request (own daemon thread); a failed piece is reported and the others still run; repeating the
call is harmless (state is kept on `app.state.warm`); no student data is involved (the sentences are fixed); nothing is loaded twice
(`_components` and the gateway factories are reused, not duplicated). Only the voice status endpoint and API start-up call it.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from app.config import Settings

log = logging.getLogger(__name__)
_lock = threading.Lock()

PRIME_TEXT = "What is the fee for the course?"


def _timed_step(detail: dict, name: str, fn: Callable[[], Any]) -> bool:
    t0 = time.perf_counter()
    try:
        fn()
        detail[name] = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000)}
        return True
    except Exception as e:  # noqa: BLE001 - a warm-up failure is information, never a crash
        detail[name] = {"ok": False, "ms": round((time.perf_counter() - t0) * 1000), "error": type(e).__name__}
        log.warning("warm-up step %s failed (%s: %s)", name, type(e).__name__, e)
        return False


def warm_llm(s: Settings, detail: dict | None = None) -> dict:
    """Steps 1-3. Returns the per-step detail ({"ollama": {...}, "triage": {...}, "retrieval": {...}}); safe to call with any provider."""
    detail = {} if detail is None else detail
    ollama = s.llm_provider == "ollama" or s.embedding_provider == "ollama"
    if ollama:
        def preload() -> None:
            from app.llm.preflight import warm_up

            bad = [(m, r) for m, r in warm_up(s) if isinstance(r, str)]
            if bad:
                raise RuntimeError(f"{len(bad)} model(s) did not load: {bad[0][1]}")
        _timed_step(detail, "ollama", preload)
    if s.warmup_prime_triage and s.llm_provider == "ollama":
        def prime() -> None:
            from app.llm.gateway import get_triager

            t = get_triager(s)
            (getattr(t, "prime", None) or t.classify)(PRIME_TEXT)
        _timed_step(detail, "triage", prime)
    if s.warmup_prime_triage and (ollama or s.embedding_provider != "fake"):
        def retrieval() -> None:
            from sqlalchemy import text

            from app.db.session import session_scope
            from app.llm.gateway import get_embedding_client

            get_embedding_client(s).embed_query("warm up")
            with session_scope() as db:
                db.execute(text("SELECT 1"))
        _timed_step(detail, "retrieval", retrieval)
    return detail


def _state(app) -> dict:
    st = getattr(app.state, "warm", None)
    if st is None:
        st = app.state.warm = {"llm": None, "voice": None, "detail": {}}
    return st


def start_llm_warmup(app, s: Settings) -> None:
    """Start step 1-3 in the background once per process (API start-up and the voice status call both ask; only the first one runs)."""
    if not s.llm_warmup or not (s.llm_provider == "ollama" or s.embedding_provider == "ollama"):
        return
    with _lock:
        st = _state(app)
        if st["llm"] is not None:
            return
        st["llm"] = "running"

    def work() -> None:
        ok = True
        try:
            detail = warm_llm(s, st["detail"])
            ok = all(v.get("ok") for v in detail.values()) if detail else False
        except Exception as e:  # noqa: BLE001
            log.warning("llm warm-up failed (%s)", type(e).__name__)
            ok = False
        st["llm"] = "ready" if ok else "failed"
        if ok:
            from app.observability.stages import mark_warm

            mark_warm(True)
        log.info("warm-up (models): %s %s", st["llm"], st["detail"])

    threading.Thread(target=work, name="llm-warmup", daemon=True).start()


def start_voice_warmup(app, s: Settings, load_voice: Callable[[], None]) -> None:
    """Step 4 (after the LLM warm-up, never concurrently with it). `load_voice` loads and runs Silero / Piper / Whisper once."""
    if s.voice_provider != "real":
        return
    with _lock:
        st = _state(app)
        if st["voice"] is not None:
            return
        st["voice"] = "running"
        app.state.voice_warm = "running"

    def work() -> None:
        # wait for the LLM warm-up (bounded) so the two never compete for the CPU
        deadline = time.monotonic() + max(s.llm_timeout_seconds, 120.0)
        while st["llm"] == "running" and time.monotonic() < deadline:
            time.sleep(0.2)
        ok = _timed_step(st["detail"], "voice", load_voice)
        st["voice"] = "ready" if ok else "failed"
        app.state.voice_warm = st["voice"]

    threading.Thread(target=work, name="voice-warm", daemon=True).start()


def warm_report(app) -> dict:
    st = _state(app)
    return {"llm": st["llm"], "voice": st["voice"], "steps": dict(st["detail"])}
