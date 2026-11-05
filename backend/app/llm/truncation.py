"""Detecting a model reply that was cut off by the output-token limit.

A reply that stopped because it hit the cap is incomplete: a half-sentence can still carry a valid citation, so a citation
check alone can never prove the reply is safe to show. Every product path that shows model text to a person must reject a
reply for which `is_truncated` is True (fail closed).

Providers report this differently: Ollama `done_reason == "length"`, OpenAI-compatible servers `finish_reason == "length"`
(Anthropic-style APIs say "max_tokens"). When no reason is reported at all, a reply that used the whole output allowance is
treated as truncated too; an explicit "stop" is trusted.
"""

_LENGTH_REASONS = {"length", "max_tokens", "max_output_tokens"}


def finish_reason(reply) -> str | None:
    meta = getattr(reply, "response_metadata", None) or {}
    reason = meta.get("done_reason") or meta.get("finish_reason") or meta.get("stop_reason")
    return str(reason).lower() if reason else None


def output_tokens(reply) -> int | None:
    usage = getattr(reply, "usage_metadata", None) or {}
    meta = getattr(reply, "response_metadata", None) or {}
    n = usage.get("output_tokens") or meta.get("eval_count")
    return int(n) if n else None


def is_truncated(reply, max_output_tokens: int = 0) -> bool:
    reason = finish_reason(reply)
    if reason in _LENGTH_REASONS:
        return True
    if reason is None and max_output_tokens:
        used = output_tokens(reply)
        return used is not None and used >= max_output_tokens
    return False
