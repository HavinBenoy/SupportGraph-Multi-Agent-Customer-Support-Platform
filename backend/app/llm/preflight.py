"""Startup checks and warm-up for a local Ollama, so a missing model or a stopped server is found when the API starts, not by
the first student. Nothing here changes data. Only endpoints allowed by the external-endpoint gate are contacted.

    run_preflight(settings) -> PreflightResult   reachable? every model that will be used pulled?
    enforce(settings)                            applies SG_LLM_PREFLIGHT: off | warn (log) | strict (raise, refuse to start)
    warm_up(settings)                            loads the models into memory (blocking; the API runs it in a background thread)
"""

import logging
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.llm.errors import ExternalEndpointRefused
from app.llm.guard import check_endpoint_allowed

log = logging.getLogger(__name__)
ROLES = ("action", "faq", "triage", "rewrite", "proposer", "critic", "reviser", "judge")


@dataclass
class PreflightResult:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def chat_models_in_use(s: Settings) -> list[str]:
    names = [s.llm_model, *(getattr(s, f"llm_model_{r}") for r in ROLES)]
    return sorted({n for n in names if n})


def _installed(base_url: str, timeout: float) -> set[str]:
    import httpx

    r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
    r.raise_for_status()
    return {m["name"] for m in r.json().get("models", [])}


def _present(model: str, installed: set[str]) -> bool:
    return model in installed or (":" not in model and f"{model}:latest" in installed)


def run_preflight(s: Settings, timeout: float = 3.0) -> PreflightResult:
    res = PreflightResult()
    wanted: list[tuple[str, str, str]] = []  # (what, base_url, model)
    if s.llm_provider == "ollama":
        wanted += [("chat model", s.llm_base_url, m) for m in chat_models_in_use(s)]
    if s.embedding_provider == "ollama":
        wanted.append(("embedding model", s.embedding_base_url, s.embedding_model))
    if not wanted:
        res.notes.append("no Ollama provider configured: nothing to check")
        return res
    for base in sorted({w[1] for w in wanted}):
        try:
            check_endpoint_allowed(base, s.allow_external_llm, what="model")
            installed = _installed(base, timeout)
        except ExternalEndpointRefused as e:
            res.problems.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001 - connection refused, timeout, bad JSON: all mean "cannot use this server"
            res.problems.append(f"cannot reach Ollama at {base} ({type(e).__name__}). Start it with `ollama serve`.")
            continue
        for what, b, model in wanted:
            if b != base:
                continue
            if _present(model, installed):
                res.notes.append(f"{what} '{model}' is available")
            else:
                res.problems.append(f"{what} '{model}' is not pulled. Run: ollama pull {model}")
    return res


def enforce(s: Settings) -> PreflightResult | None:
    """Apply SG_LLM_PREFLIGHT. Returns the result (None when off or nothing to check)."""
    if s.llm_preflight == "off" or (s.llm_provider != "ollama" and s.embedding_provider != "ollama"):
        return None
    res = run_preflight(s)
    for n in res.notes:
        log.info("preflight: %s", n)
    for p in res.problems:
        log.warning("preflight: %s", p)
    if res.problems and s.llm_preflight == "strict":
        raise RuntimeError("Model preflight failed (SG_LLM_PREFLIGHT=strict): " + " | ".join(res.problems))
    return res


def runner_options(s: Settings) -> dict:
    """The Ollama options that decide whether a loaded model can be reused (see warm_up). Must match what the gateway sends."""
    out: dict = {"num_ctx": s.llm_num_ctx}
    if s.llm_num_thread:
        out["num_thread"] = s.llm_num_thread
    return out


def warm_up(s: Settings) -> list[tuple[str, float | str]]:
    """Load each model into memory once (keep_alive keeps it there). Returns (model, seconds) or (model, error name)."""
    import httpx

    out: list[tuple[str, float | str]] = []
    jobs: list[tuple[str, str, str, dict]] = []
    if s.llm_provider == "ollama":
        # M3: the preload must use the SAME runner options as the real calls (context size, threads). Ollama starts a new runner (a full
        # model reload) when they differ, so a warm-up with other options would leave the first real question to pay for the load again.
        opts = runner_options(s)
        jobs += [(m, s.llm_base_url, "/api/generate", {"model": m, "keep_alive": s.llm_keep_alive, "options": {**opts, "num_predict": 1}})
                 for m in chat_models_in_use(s)]
    if s.embedding_provider == "ollama":
        jobs.append((s.embedding_model, s.embedding_base_url, "/api/embed", {"model": s.embedding_model, "input": "warm up", "keep_alive": s.llm_keep_alive}))
    for model, base, path, body in jobs:
        t0 = time.perf_counter()
        try:
            check_endpoint_allowed(base, s.allow_external_llm, what="model")
            httpx.post(f"{base.rstrip('/')}{path}", json=body, timeout=max(s.llm_timeout_seconds, 120.0)).raise_for_status()
            out.append((model, round(time.perf_counter() - t0, 2)))
        except Exception as e:  # noqa: BLE001
            out.append((model, type(e).__name__))
    return out
