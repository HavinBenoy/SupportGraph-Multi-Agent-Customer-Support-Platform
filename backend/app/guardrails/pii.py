"""Masking of contact details before text is stored somewhere other than the student's own record
(escalation briefs, audit logs). Deterministic and conservative: it hides emails and phone-like digit runs.
It does NOT remove names or free-text secrets; briefs therefore copy only a short excerpt of the message."""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s-]{8,}\d(?!\w)")


_ISO_DATES = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_phone(candidate: str) -> bool:
    """A phone-like run needs 10+ digits AFTER removing ISO dates ("2026-10-18, 2026-10-22" looks like a long number but is not one)."""
    return len(re.sub(r"\D", "", _ISO_DATES.sub("", candidate))) >= 10


def find_pii(text: str) -> list[str]:
    """Emails and phone-like numbers found in `text` (used by the QA check: a reply must not introduce contact details)."""
    return _EMAIL.findall(text) + [m for m in _PHONE.findall(text) if _is_phone(m)]


def mask_pii(text: str) -> str:
    return _PHONE.sub(lambda m: "[phone]" if _is_phone(m.group(0)) else m.group(0), _EMAIL.sub("[email]", text))


def mask_phone(phone: str | None) -> str:
    """'+919876543210' -> '+91*******210' (for audit details: enough to recognise, not enough to reuse)."""
    if not phone:
        return ""
    return phone[:3] + "*" * max(len(phone) - 6, 0) + phone[-3:]
