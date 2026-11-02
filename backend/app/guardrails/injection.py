"""Deterministic prompt-injection screen. Pure code: no LLM, never imports graph/.

This is ONE layer, and a weak one on purpose: a regex catches the lazy attacks ("ignore all previous instructions") and
gives a clean refusal. It cannot catch a clever paraphrase, so the real protections stay elsewhere: retrieval only ever
contains public documents, tools are scoped by the trusted run config, and prompts treat user text as data.
"""

import re

_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\bignore\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,30}\b(instructions?|rules?|prompts?)\b",
    r"\b(disregard|forget|override)\b.{0,30}\b(instructions?|rules?|prompt)\b",
    r"\b(admin|developer|debug|god)\s+mode\b",
    r"\bsystem\s+prompt\b",
    r"</?\s*(question|sources?|source|system)\b[^>]*>",
    r"\byou\s+are\s+now\b",
    r"\bjailbreak\b",
)]


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _PATTERNS)
