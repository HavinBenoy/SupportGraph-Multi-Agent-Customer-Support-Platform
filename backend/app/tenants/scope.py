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


@dataclass(frozen=True)
class Reviewer:
    """Who is deciding on a paused action. Built by trusted code (the staff console / student session), never by the LLM.
    role "staff": a person on the institute's team. role "student": the student who owns the ticket confirming their own change."""

    tenant_id: str
    reviewer_id: str
    role: str  # "staff" | "student"

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.reviewer_id}"
