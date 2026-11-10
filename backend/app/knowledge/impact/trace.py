"""A per-request processing trace for the AI knowledge update: what happened to the change at every stage, and why.

One `Trace` per analysis attempt, made current with `tracing(...)` (a context variable, so the deep stages - discovery, passage selection, context building, the model call,
validation, classification - add events without any plumbing). Each event is a small dict:
    {"n": 7, "ms": 1234, "stage": "model_call", "outcome": "ok", "doc": "data-science", "section": "Faculty", ...details}
and is written two ways:
  * to the application log, one structured line per event (logger `supportgraph.knowledge.trace`, `trace=<request_id>#<attempt>`), WITHOUT any document text, prompt or answer;
  * into the request's analysis JSON (`analysis.trace`), which only staff/admins who can see the request can read (the existing RBAC). Document text, the exact prompt and
    the raw model answer are stored there ONLY when `SG_KB_AI_TRACE_CONTENT=true` (a debugging switch, off by default), truncated.

Outcomes that answer "why did nothing happen?" are named explicitly (see `OUTCOMES`): not_discovered, no_passage, not_sent, model_not_invoked, model_failed, malformed,
truncated, no_change, rejected, review, proposed."""

import contextvars
import json
import logging
import time
from contextlib import contextmanager

log = logging.getLogger("supportgraph.knowledge.trace")

MAX_EVENTS = 400
MAX_TEXT = 4000
OUTCOMES = {
    "not_discovered": "the document was considered but not nominated",
    "no_passage": "the document was nominated but no passage of it was selected",
    "not_sent": "a section was selected but deliberately not sent to the model",
    "model_not_invoked": "no model call was made (the section editor is off or no model is configured)",
    "model_failed": "the model was called but did not answer (error, timeout, unreachable)",
    "truncated": "the model's answer was cut off",
    "malformed": "the model's answer could not be parsed",
    "no_change": "the model returned the section unchanged (the change does not apply there)",
    "rejected": "the model proposed a change that validation rejected",
    "review": "a change was proposed but flagged for human review",
    "proposed": "a change was proposed",
}

_current: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar("kb_trace", default=None)


class Trace:
    def __init__(self, request_id: str, attempt: int, *, content: bool = False) -> None:
        self.key = f"{request_id}#{attempt}"
        self.content = content
        self.events: list[dict] = []
        self.dropped = 0
        self._t0 = time.monotonic()

    def add(self, stage: str, outcome: str, doc: str | None = None, *, text: dict | None = None, **data) -> None:
        """`data` is logged and stored; `text` (document text, prompt, answer) is stored only when content tracing is on, and never logged."""
        ev = {"n": len(self.events) + self.dropped + 1, "ms": int((time.monotonic() - self._t0) * 1000), "stage": stage, "outcome": outcome}
        if doc:
            ev["doc"] = doc
        ev.update({k: _small(v) for k, v in data.items() if v is not None})
        try:
            log.info("kb.trace %s", json.dumps({"trace": self.key, **ev}, default=str, ensure_ascii=False)[:2000])
        except Exception:                                                     # noqa: BLE001 - tracing never breaks the analysis
            pass
        if text and self.content:
            ev["text"] = {k: (str(v)[:MAX_TEXT] + ("…" if len(str(v)) > MAX_TEXT else "")) for k, v in text.items() if v is not None}
        if len(self.events) < MAX_EVENTS:
            self.events.append(ev)
        else:
            self.dropped += 1

    def export(self) -> dict:
        return {"events": self.events, "dropped": self.dropped, "content": self.content, "outcomes": OUTCOMES}


def _small(v):
    if isinstance(v, str):
        return v[:300]
    if isinstance(v, (list, tuple)):
        return [_small(x) for x in list(v)[:20]]
    if isinstance(v, dict):
        return {str(k)[:60]: _small(x) for k, x in list(v.items())[:20]}
    return v


def current() -> "Trace | None":
    return _current.get()


def event(stage: str, outcome: str, doc: str | None = None, **data) -> None:
    """Add an event to the current trace, if there is one (a no-op outside an analysis, e.g. in unit tests or the probe script)."""
    t = _current.get()
    if t is not None:
        t.add(stage, outcome, doc, **data)


@contextmanager
def tracing(trace: Trace):
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)
