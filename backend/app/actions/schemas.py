"""What the model may PROPOSE. A proposal is data; it grants nothing. Code validates it (policy.py), a person or the
student approves it (graph/nodes/write.py), and only then does code write (executor.py). The LLM has no write tool."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ActionName = Literal["request_makeup_class", "update_contact_phone"]


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionName
    batch_id: str | None = Field(default=None, max_length=16)
    session_date: date | None = None
    reason: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=32)
