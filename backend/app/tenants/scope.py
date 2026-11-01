"""Trusted request scope. Lives in `tenants/` so both the DB layer and the tools can use it
without depending on each other."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Scope:
    """Who is asking. Built by trusted code from the authenticated request, never from LLM output."""

    tenant_id: str
    student_id: str
    today: date
