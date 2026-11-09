"""The HTTP API (build step 5): FastAPI over the existing LangGraph workflows. Endpoints are plain `def`, so FastAPI runs the
blocking graph calls in its worker threads and the event loop stays free.

    uvicorn app.main:app --port 8000          (or: make api)

Requires SG_AUTH_SECRET (>= 32 characters). Errors never carry stack traces or internals: unexpected failures become a generic 500
and are logged server-side."""

import asyncio
import logging
import secrets as _secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.ratelimit import SlidingWindow
from app.calls.hub import CallHub
from app.trial.recording import get_provider
from app.api.routes import admin, auth, calls, chat, knowledge, staff, trial, voice
from app.config import Settings, get_settings
from app.graph.checkpointer import get_checkpointer
from app.graph.support import build_support_graph


from app.llm.guard import describe_llm
from app.llm.preflight import enforce
from app.llm.warmup import start_llm_warmup

log = logging.getLogger(__name__)
MIN_SECRET_LENGTH = 32


def create_app(settings: Settings | None = None, graph=None) -> FastAPI:
    s = settings or get_settings()
    secret = s.auth_secret.get_secret_value() if s.auth_secret else ""
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError("SG_AUTH_SECRET must be set to at least 32 characters (generate one with `make auth-init`)")

    def _sweep_orphan_calls() -> None:
        """M4: a call still marked live at startup belongs to a process that no longer exists (the audio lives in memory): mark it failed."""
        try:
            from app.calls.service import sweep_orphans
            from app.db.session import session_scope

            with session_scope() as db:
                n = sweep_orphans(db)
            if n:
                log.warning("marked %d call(s) left over from a previous run as failed", n)
        except Exception as e:  # noqa: BLE001 - e.g. migration 0006 not applied yet: the API must still start
            log.warning("could not check for left-over calls (%s); run `make db-migrate`", type(e).__name__)

    async def _purge_loop(app: FastAPI) -> None:
        """M5: the retention job. Runs once at startup (after recordings left over from a crash were marked failed), then every SG_TRIAL_PURGE_INTERVAL_MIN minutes.
        Idempotent and failure-tolerant: an error is logged and the next round tries again."""
        from app.trial import retention

        def sweep() -> None:
            from app.db.session import session_scope
            with session_scope() as db:
                n = retention.sweep_recordings(db)
            if n:
                log.warning("marked %d recording(s) left over from a previous run as failed", n)

        try:
            await asyncio.to_thread(sweep)
        except Exception as e:  # noqa: BLE001 - e.g. migration 0008 not applied yet
            log.warning("retention sweep skipped (%s); run `make db-migrate`", type(e).__name__)
            return
        while True:
            try:
                await asyncio.to_thread(retention.run_all, app.state.recording_provider)
            except Exception as e:  # noqa: BLE001
                log.warning("retention run failed (%s)", type(e).__name__)
            await asyncio.sleep(s.trial_purge_interval_min * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for line in describe_llm(s):
            log.info(line)
        if graph is None:  # real startup (tests inject a graph and must not touch the network)
            enforce(s)  # off | warn | strict
            start_llm_warmup(app, s)  # M3: models + triage + retrieval, in the background, one after the other
        app.state.graph = graph or build_support_graph(settings=s, checkpointer=get_checkpointer(s))
        _sweep_orphan_calls()
        purge_task = asyncio.create_task(_purge_loop(app), name="trial-retention") if s.trial_purge_interval_min > 0 else None
        yield
        if purge_task is not None:
            purge_task.cancel()
            await asyncio.gather(purge_task, return_exceptions=True)
        await app.state.call_hub.shutdown()   # M4: every live call is ended cleanly (staff sockets told, rows closed)

    app = FastAPI(title="SupportGraph API", version="0.5.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings, app.state.secret = s, secret
    app.state.login_limiter = SlidingWindow(s.login_max_failures, s.login_lockout_seconds)
    app.state.chat_limiter = SlidingWindow(s.chat_max_messages_per_minute, 60)
    app.state.call_hub = CallHub(s)
    app.state.recording_provider = get_provider(s)   # M5: "none" unless SG_TRIAL_RECORDING_PROVIDER=local
    if graph is not None:
        app.state.graph = graph
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])
    for r in (auth.router, chat.router, staff.router, admin.router, voice.router, calls.router, calls.interventions_router, trial.admin_router, trial.staff_router, knowledge.admin_router, knowledge.staff_router):
        app.include_router(r)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        ref = _secrets.token_hex(4)
        log.exception("unhandled error %s on %s %s", ref, request.method, request.url.path)
        return JSONResponse({"detail": f"something went wrong (reference {ref})"}, status_code=500)

    return app
