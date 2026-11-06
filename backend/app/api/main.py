"""The HTTP API (build step 5): FastAPI over the existing LangGraph workflows. Endpoints are plain `def`, so FastAPI runs the
blocking graph calls in its worker threads and the event loop stays free.

    uvicorn app.main:app --port 8000          (or: make api)

Requires SG_AUTH_SECRET (>= 32 characters). Errors never carry stack traces or internals: unexpected failures become a generic 500
and are logged server-side."""

import logging
import secrets as _secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.ratelimit import SlidingWindow
from app.api.routes import admin, auth, chat, staff, voice
from app.config import Settings, get_settings
from app.graph.checkpointer import get_checkpointer
from app.graph.support import build_support_graph

import threading

from app.llm.guard import describe_llm
from app.llm.preflight import enforce, warm_up

log = logging.getLogger(__name__)
MIN_SECRET_LENGTH = 32


def create_app(settings: Settings | None = None, graph=None) -> FastAPI:
    s = settings or get_settings()
    secret = s.auth_secret.get_secret_value() if s.auth_secret else ""
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError("SG_AUTH_SECRET must be set to at least 32 characters (generate one with `make auth-init`)")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for line in describe_llm(s):
            log.info(line)
        if graph is None:  # real startup (tests inject a graph and must not touch the network)
            enforce(s)  # off | warn | strict
            if s.llm_warmup and (s.llm_provider == "ollama" or s.embedding_provider == "ollama"):
                threading.Thread(target=lambda: log.info("warm-up: %s", warm_up(s)), daemon=True, name="llm-warmup").start()
        app.state.graph = graph or build_support_graph(settings=s, checkpointer=get_checkpointer(s))
        yield

    app = FastAPI(title="SupportGraph API", version="0.5.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings, app.state.secret = s, secret
    app.state.login_limiter = SlidingWindow(s.login_max_failures, s.login_lockout_seconds)
    app.state.chat_limiter = SlidingWindow(s.chat_max_messages_per_minute, 60)
    if graph is not None:
        app.state.graph = graph
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    for r in (auth.router, chat.router, staff.router, admin.router, voice.router):
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
