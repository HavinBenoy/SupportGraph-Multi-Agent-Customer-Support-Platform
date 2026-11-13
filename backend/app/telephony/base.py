"""The telephony provider contract. A provider adapter only translates its wire protocol; it never decides identity, permissions or conversation flow."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CallStart:
    """What the provider tells us when a call's media stream opens."""

    provider: str
    call_ref: str                 # the provider's id for this call (support / reconciliation only)
    from_number: str              # the caller ID as delivered (unverified, may be spoofed or withheld)
    to_number: str                # the number that was dialled (selects the tenant)
    sample_rate: int              # the line's audio rate (8000 or 16000), L16 mono
    direction: str = "inbound"
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LineEvent:
    """A provider event on an open call. kind: dtmf | mark | ignored."""

    kind: str
    digit: str | None = None
    name: str | None = None


class ProtocolError(ValueError):
    """The provider sent something that does not follow its protocol (the call is refused or the frame ignored)."""


class TelephonyAdapter(Protocol):
    name: str

    def parse_start(self, text: str) -> CallStart: ...

    def parse_event(self, text: str) -> LineEvent: ...

    def kill_audio(self) -> dict: ...          # flush whatever the provider still has queued for the caller (barge-in, ownership change)

    def hang_up(self) -> dict: ...

    def mark(self, name: str) -> dict: ...     # a playback marker the provider echoes when the caller has heard everything before it
