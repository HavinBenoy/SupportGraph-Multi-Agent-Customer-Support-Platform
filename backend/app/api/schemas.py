"""Request/response models of the HTTP API. Responses expose what a UI needs, never internals (triage, QA codes, prompts, sources' text)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    tenant: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)


class LoginOut(BaseModel):
    token: str
    role: str
    username: str
    tenant_id: str
    student_id: str | None
    expires_in: int


class MeOut(BaseModel):
    role: str
    username: str
    tenant_id: str
    student_id: str | None


class ConfigOut(BaseModel):
    tenant_id: str
    tenant_name: str
    is_sample: bool


class ChatIn(Strict):
    text: str = Field(min_length=1, max_length=1000)
    ticket_id: str | None = Field(default=None, max_length=80)
    tenant: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)  # anonymous visitors only


class Citation(BaseModel):
    id: str
    heading: str


ChatStatus = Literal["answered", "needs_detail", "escalated", "awaiting_staff", "awaiting_confirmation", "done"]


class ChatOut(BaseModel):
    ticket_id: str
    reply: str
    status: ChatStatus
    citations: list[Citation] = []
    escalation_id: str | None = None
    signed_in: bool


class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class HistoryOut(BaseModel):
    ticket_id: str
    messages: list[MessageOut]
    awaiting: str | None = None


class EscalationRow(BaseModel):
    id: str
    ticket_id: str
    team: str
    priority: str
    reason: str
    status: str
    claimed_by: str | None
    created_at: datetime


class EscalationDetail(EscalationRow):
    brief: dict
    resolution_note: str | None = None
    resolved_at: datetime | None = None


class ResolveIn(Strict):
    note: str = Field(min_length=1, max_length=1000)


class ApprovalRow(BaseModel):
    request_id: str
    ticket_id: str | None
    student_id: str
    student_name: str | None
    batch_id: str
    session_date: str
    reason: str
    created_at: datetime


class DecisionIn(Strict):
    approve: bool


class DecisionOut(BaseModel):
    outcome: str
    message: str
