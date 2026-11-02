"""Structured output schemas for agents. The triage schema is what a real LLM must fill in (via
`with_structured_output`) and what the offline rule-based fake also returns, so both are validated identically."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    # answered from approved documents (knowledge branch)
    "courses", "eligibility", "fees", "admissions", "placement", "privacy_policy",
    # the student's own records (action branch, only when signed in); with wants_own_records=False they are policy questions
    "attendance", "assessment", "schedule",
    # need a person (escalation branch)
    "complaint", "payment_dispute", "legal_threat", "safety", "human_request", "makeup_request", "concession_request",
    # decline / ask
    "other_person_data", "chitchat", "other",
]

RECORD_CATEGORIES: frozenset[str] = frozenset({"attendance", "assessment", "schedule"})
ESCALATION_CATEGORIES: frozenset[str] = frozenset(
    {"complaint", "payment_dispute", "legal_threat", "safety", "human_request", "makeup_request", "concession_request"})


class TriageResult(BaseModel):
    """What triage says about one message. It is a *suggestion*: the router (code) decides, and authorisation never
    depends on any field here (tool scope comes from the trusted run config)."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    priority: Literal["low", "medium", "high", "urgent"] = "low"
    sentiment: Literal["positive", "neutral", "negative", "angry"] = "neutral"
    language: str = Field(default="en", min_length=2, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)
    # True when the person asks about THEIR OWN records ("what is my attendance"), False for rules ("what attendance do I need").
    wants_own_records: bool = False


def low_confidence_triage() -> TriageResult:
    """The fail-safe result when triage cannot produce a valid answer: the router will ask a clarifying question."""
    return TriageResult(category="other", confidence=0.0)
