"""Deterministic argument validation. Pure code -- no LLM, never imports graph/.

An LLM chooses tool arguments, so those arguments are untrusted input. We validate
them in code before they touch any data.
"""

import re

_BATCH_ID = re.compile(r"^[A-Z]{2,4}-\d{3}$")


def normalize_batch_id(raw: str) -> str | None:
    """Return the canonical batch id (e.g. ' pyf-101 ' -> 'PYF-101'), or None if malformed."""
    candidate = raw.strip().upper()
    return candidate if _BATCH_ID.fullmatch(candidate) else None
