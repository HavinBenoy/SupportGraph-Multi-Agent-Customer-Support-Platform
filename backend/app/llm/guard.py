"""Safety rails around model calls: external-endpoint gate, daily token budget, error normalisation, startup banner."""

import ipaddress
import threading
from datetime import date
from urllib.parse import urlparse

from app.config import Settings
from app.llm.errors import ExternalEndpointRefused, ModelUnavailable

_LOCAL_NAMES = {"localhost", "ollama", "host.docker.internal"}  # "ollama" = the docker-compose service name


def is_local_endpoint(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def check_endpoint_allowed(url: str, allow_external: bool, *, what: str) -> None:
    if is_local_endpoint(url) or allow_external:
        return
    host = urlparse(url).hostname or url
    raise ExternalEndpointRefused(
        f"The {what} endpoint '{host}' is not on this machine or private network. Student text would leave it. "
        "Refusing. If this is intended (synthetic data only), set SG_ALLOW_EXTERNAL_LLM=true explicitly."
    )


class TokenBudget:
    """Process-wide daily token counter. limit 0 = unlimited. Resets at local midnight. Thread-safe."""

    def __init__(self, limit: int):
        self.limit = limit
        self._day = date.today()
        self._used = 0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        if date.today() != self._day:
            self._day, self._used = date.today(), 0

    def check(self) -> None:
        with self._lock:
            self._roll()
            if self.limit and self._used >= self.limit:
                raise ModelUnavailable("daily token budget reached")

    def add(self, tokens: int) -> None:
        with self._lock:
            self._roll()
            self._used += max(0, tokens)

    @property
    def used(self) -> int:
        with self._lock:
            self._roll()
            return self._used


_budget: TokenBudget | None = None


def get_budget(settings: Settings) -> TokenBudget:
    global _budget
    if _budget is None or _budget.limit != settings.llm_daily_token_budget:
        _budget = TokenBudget(settings.llm_daily_token_budget)
    return _budget


def invoke_guarded(model, messages, settings: Settings, config=None):
    """model.invoke with the budget checked first and every failure normalised to ModelUnavailable."""
    budget = get_budget(settings)
    budget.check()
    try:
        out = model.invoke(messages, config) if config is not None else model.invoke(messages)
    except ModelUnavailable:
        raise
    except Exception as e:  # connection refused, timeout, model not pulled, server 5xx ...
        raise ModelUnavailable(type(e).__name__) from e
    usage = getattr(out, "usage_metadata", None) or {}
    budget.add(int(usage.get("total_tokens", 0) or 0))
    return out


def describe_llm(settings: Settings) -> list[str]:
    """Human-readable startup lines. Never includes keys."""
    s = settings
    if s.llm_provider == "fake":
        return ["LLM: fake (offline, deterministic) - no real model in use"]
    where = "local" if is_local_endpoint(s.llm_base_url) else "EXTERNAL"
    lines = [f"LLM: {s.llm_provider} model={s.llm_model} endpoint={urlparse(s.llm_base_url).netloc} ({where})"]
    overrides = {r: getattr(s, f"llm_model_{r}") for r in
                 ("action", "faq", "triage", "rewrite", "proposer", "critic", "reviser", "judge") if getattr(s, f"llm_model_{r}")}
    if s.embedding_provider != "fake":
        from app.llm.gateway import effective_embedding_prefixes

        qp, dp = effective_embedding_prefixes(s)
        lines.append(f"Embeddings: {s.embedding_provider} model={s.embedding_model} prefixes={'on' if (qp or dp) else 'off'} "
                     f"endpoint={urlparse(s.embedding_base_url).netloc} ({'local' if is_local_endpoint(s.embedding_base_url) else 'EXTERNAL'})")
    lines.append(f"QA LLM critic: {'on' if s.qa_llm_critic else 'off'}; query rewriter: {s.rewriter_mode}")
    if overrides:
        lines.append("LLM role overrides: " + ", ".join(f"{r}={m}" for r, m in overrides.items()))
    if where == "EXTERNAL":
        lines.append("WARNING: external LLM endpoint enabled. Use synthetic data only.")
    return lines
