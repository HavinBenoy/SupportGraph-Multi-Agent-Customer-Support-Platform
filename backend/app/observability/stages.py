"""Stage-level latency recording for one graph run (M3).

`node_ms` (graph/support.py `_timed`) says how long each graph NODE took. A node such as `knowledge` hides several very different costs
(embedding the question, searching Postgres, building the prompt, the model call, validating the citations), so this module records
those SUB-STAGES of a node, plus the counters Ollama returns with every response (model load time, prompt evaluation, token generation).

How it fits together (no double counting):
  * A `StageRecorder` is created per run by `ask_support` and stored in a ContextVar. Code that wants to be measured calls
    `with stage("embed"): ...`; with no recorder active that is a no-op, so unit tests and scripts are unaffected.
  * Stages are attributed to the node that is running (`_timed` sets `recorder.node`). Stages of one node never overlap one another, so
    `node_ms[node] == sum(stages of that node) + other`, where `other` is everything the node did that was not a named stage
    (graph/checkpointer overhead, tenant config lookup, DB session setup ...). It is reported, never hidden.
  * `summary()` reconciles the run: graph_ms (wall clock around graph.invoke) vs the sum of node_ms; the difference is `unaccounted_ms`.
  * Only names, numbers and flags are recorded. No question text, no answer text, no student data.

Ollama counters: an `OllamaMetricsHandler` callback is attached to every Ollama chat model by the gateway. Each response carries
`load_duration` (model had to be loaded into RAM: a COLD call), `prompt_eval_count/duration` (reading the prompt) and `eval_count/duration`
(generating the answer), all in nanoseconds. They make "why was this call slow" answerable from data instead of guesses.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

_current: ContextVar["StageRecorder | None"] = ContextVar("sg_stage_recorder", default=None)

_state_lock = threading.Lock()
_runs_started = 0          # graph runs started in this process (the first one pays every lazy initialisation)
_warmed = False            # a warm-up pass completed in this process


def mark_warm(value: bool = True) -> None:
    global _warmed
    _warmed = value


def is_warm() -> bool:
    return _warmed


def _ns_to_ms(v: Any) -> float:
    try:
        return round(float(v) / 1e6, 1)
    except (TypeError, ValueError):
        return 0.0


def parse_ollama_metrics(d: dict) -> dict | None:
    """Turn the counters in an Ollama response into milliseconds and token counts. None if the response carries none (other providers)."""
    if not any(k in d for k in ("eval_count", "load_duration", "prompt_eval_count", "total_duration")):
        return None
    decode_ms = _ns_to_ms(d.get("eval_duration"))
    tokens = int(d.get("eval_count") or 0)
    prompt_ms = _ns_to_ms(d.get("prompt_eval_duration"))
    prompt_tokens = int(d.get("prompt_eval_count") or 0)
    return {
        "load_ms": _ns_to_ms(d.get("load_duration")),
        "prompt_tokens": prompt_tokens, "prompt_eval_ms": prompt_ms,
        "prompt_tok_s": round(prompt_tokens / (prompt_ms / 1000), 1) if prompt_ms and prompt_tokens else None,
        "decode_tokens": tokens, "decode_ms": decode_ms,
        "decode_tok_s": round(tokens / (decode_ms / 1000), 1) if decode_ms and tokens else None,
        "total_ms": _ns_to_ms(d.get("total_duration")),
    }


class StageRecorder:
    """Collects stage timings, Ollama counters and flags for ONE graph run. Thread-safe (LangGraph runs nodes in worker threads)."""

    def __init__(self) -> None:
        global _runs_started
        with _state_lock:
            _runs_started += 1
            self.first_run = _runs_started == 1
        self.started = time.perf_counter()
        self.node = ""
        self.stages: dict[str, dict[str, float]] = {}     # node -> stage -> ms
        self.llm_calls: list[dict] = []
        self.flags: dict[str, Any] = {}
        self._lock = threading.Lock()

    def add(self, stage_name: str, ms: float, node: str | None = None) -> None:
        with self._lock:
            bucket = self.stages.setdefault(node if node is not None else (self.node or "-"), {})
            bucket[stage_name] = round(bucket.get(stage_name, 0) + ms, 1)

    def llm_call(self, role: str, metrics: dict) -> None:
        with self._lock:
            self.llm_calls.append({"role": role, "node": self.node or "-", **metrics})

    def note(self, key: str, value: Any) -> None:
        with self._lock:
            self.flags[key] = value

    def summary(self, node_ms: dict | None, graph_ms: float, *, cold_load_ms: float = 800, cold_embed_ms: float = 1500) -> dict:
        """The reconciled picture of this run (see the module docstring)."""
        node_ms = dict(node_ms or {})
        with self._lock:
            stages = {n: dict(v) for n, v in self.stages.items()}
            calls = [dict(c) for c in self.llm_calls]
            flags = dict(self.flags)
        nodes_sum = sum(node_ms.values())
        by_node: dict[str, dict[str, float]] = {}
        for node, ms in node_ms.items():
            st = stages.get(node)
            if not st:
                continue
            by_node[node] = {**st, "other": round(max(0.0, ms - sum(st.values())), 1)}
        flat: dict[str, float] = {}
        for st in stages.values():
            for k, v in st.items():
                flat[k] = round(flat.get(k, 0) + v, 1)
        loads = sum(c.get("load_ms", 0) for c in calls)
        reasons = []
        if loads >= cold_load_ms:
            reasons.append("llm_load")
        if flat.get("embed", 0) >= cold_embed_ms:
            reasons.append("embed_slow")
        if self.first_run and not _warmed and not reasons:
            reasons.append("first_run_without_warmup")
        return {
            "graph_ms": round(graph_ms), "nodes_sum_ms": round(nodes_sum), "unaccounted_ms": round(graph_ms - nodes_sum),
            "stages": flat, "by_node": by_node, "llm": calls, "llm_load_ms": round(loads, 1),
            "cold": bool(reasons), "cold_reasons": reasons, "first_run": self.first_run, "flags": flags,
        }


def start() -> tuple[StageRecorder, Token]:
    rec = StageRecorder()
    return rec, _current.set(rec)


def finish(token: Token) -> None:
    try:
        _current.reset(token)
    except ValueError:  # reset from a different context (should not happen); never break a turn over instrumentation
        _current.set(None)


def current() -> StageRecorder | None:
    return _current.get()


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a block as a stage of the node that is running. No-op without an active recorder."""
    rec = _current.get()
    if rec is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        rec.add(name, (time.perf_counter() - t0) * 1000)


def note(key: str, value: Any) -> None:
    rec = _current.get()
    if rec is not None:
        rec.note(key, value)


class OllamaMetricsHandler(BaseCallbackHandler):
    """LangChain callback that copies Ollama's per-response counters into the active recorder. Never raises into the model call."""

    raise_error = False

    def __init__(self, role: str) -> None:
        self.role = role

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001
        rec = _current.get()
        if rec is None:
            return
        try:
            for gens in response.generations:
                for g in gens:
                    msg = getattr(g, "message", None)
                    d = {**(getattr(msg, "response_metadata", None) or {}), **(getattr(g, "generation_info", None) or {})}
                    m = parse_ollama_metrics(d)
                    if m:
                        rec.llm_call(self.role, m)
                        return
        except Exception:  # noqa: BLE001 - instrumentation must never break an answer
            return
