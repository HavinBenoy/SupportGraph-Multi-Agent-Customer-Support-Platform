"""Deterministic argument validation. Pure code -- no LLM, never imports graph/.

An LLM chooses tool arguments, so those arguments are untrusted input. We validate
them in code before they touch any data.
"""

import re

_ORDER_ID = re.compile(r"^[A-Z]\d{4}$")


def normalize_order_id(raw: str) -> str | None:
    """Return the canonical order id (e.g. 'a1001 ' -> 'A1001'), or None if malformed."""
    candidate = raw.strip().upper()
    return candidate if _ORDER_ID.fullmatch(candidate) else None
