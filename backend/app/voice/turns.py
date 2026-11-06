"""Turn-management helpers (M2.2): when has the caller really finished, and how a turn is described for debugging. Pure code, no audio, no model.

Why this exists: Silero ends an utterance after `voice_silence_ms` of silence, but people pause inside sentences ("I want to know about the
course ... duration and eligibility"). Every endpoint therefore becomes a *segment*; the session keeps the segments of one turn together and
waits a further grace period before it commits the turn to the graph. How long depends on what the transcript looks like:

    complete    a finished sentence or question                     -> short grace (default 250 ms; usually already covered by STT time)
    short       one or two words, no final punctuation              -> medium grace (700 ms)
    incomplete  trailing "and"/"the"/"about", a comma, an ellipsis,
                "I want to know about ...", "I have a question"     -> long grace (1600 ms)

If speech resumes inside the grace period the new segment is MERGED into the same turn (the original audio is concatenated and transcribed
again, so the recogniser sees the whole sentence). A greeting, thanks, goodbye or other conversation-control phrase is always "complete".
"""

import re
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.conversation.intents import detect_intent

COMPLETE, SHORT, INCOMPLETE = "complete", "short", "incomplete"

# A transcript ending with one of these cannot be a finished thought.
_DANGLING = frozenset({
    "and", "or", "but", "so", "because", "the", "a", "an", "of", "to", "for", "about", "with", "in", "on", "at", "by", "from", "than", "into",
    "regarding", "like", "is", "are", "was", "were", "what", "which", "who", "whom", "how", "when", "where", "why", "my", "your", "our", "that",
    "if", "also", "then", "um", "uh", "er", "erm", "hmm", "i", "i'm", "i'd", "i've", "can", "could", "would", "will", "do", "does", "did",
})
# Openers that announce a question but do not contain it.
_OPENER = re.compile(
    r"^(?:okay[,\s]+|so[,\s]+|well[,\s]+|and[,\s]+)?(?:"
    r"i (?:do )?(?:have|got) (?:a |one |another |some )?(?:question|doubt|query)s?(?: (?:about|regarding|for you|to ask))?|"
    r"(?:can|may|could) i (?:ask|check|know|get)(?: (?:you )?(?:something|a question|one thing|about))?|"
    r"i (?:want|would like|wanna|need|'d like) to (?:know|ask|understand|find out|enquire|inquire|check|talk|speak|learn)(?: (?:about|regarding|more about))?(?: (?:the|your|a|an))?(?: \w+)?|"
    r"tell me (?:about|more about)(?: (?:the|your|a|an))?(?: \w+)?|"
    r"i(?:'m| am) (?:calling|here|looking) (?:to|for|about|regarding)(?: (?:the|your|a|an))?(?: \w+)?|"
    r"i(?:'m| am) (?:interested|curious) (?:in|about)(?: (?:the|your|a|an))?(?: \w+)?)"
    r"[\s.,!?…-]*$", re.I)


def completeness(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return COMPLETE
    if t.endswith(("...", "…", ",", "-", "–")):
        return INCOMPLETE
    if detect_intent(t) is not None:  # "thank you", "bye", "hello": nothing more is expected
        return COMPLETE
    low = re.sub(r"[^a-z0-9'\s]", " ", t.lower())
    words = low.split()
    if not words:
        return COMPLETE
    if _OPENER.match(t):
        return INCOMPLETE
    if words[-1] in _DANGLING:
        return INCOMPLETE
    if len(words) <= 2 and not t.endswith("?"):
        return SHORT
    return COMPLETE


def grace_ms(s: Settings, text: str) -> int:
    return {COMPLETE: s.voice_grace_complete_ms, SHORT: s.voice_grace_short_ms, INCOMPLETE: s.voice_grace_incomplete_ms}[completeness(text)]


@dataclass
class Turn:
    """One user turn: one or more audio segments (merged when the caller paused mid-sentence) or typed text."""

    id: int
    segments: list[bytes] = field(default_factory=list)
    segment_ms: list[int] = field(default_factory=list)
    text: str = ""
    stt_ms: int = 0
    typed: bool = False
    holding: bool = False            # the caller started speaking again; wait for that segment before deciding
    sealed: bool = False             # no more merging (maximum turn length reached)
    after_barge_in: bool = False     # this turn started by interrupting the assistant
    supersedes: bool = True          # a real question/request; light replies (hello, okay, thanks) do not make earlier answers stale
    wait_for: object | None = None   # settle task of an earlier sealed turn that must be committed first (keeps turns in order)
    created: float = field(default_factory=time.monotonic)
    _stt_for: int = -1               # audio length the current `text` was transcribed from (so a re-arm does not transcribe twice)

    @property
    def audio(self) -> bytes:
        return b"".join(self.segments)

    @property
    def audio_ms(self) -> int:
        return sum(self.segment_ms)

    @property
    def merged(self) -> bool:
        return len(self.segments) > 1

    def describe(self) -> dict:
        """Debug view: boundaries and sizes, never the audio or the words."""
        return {"turn_id": self.id, "segments": len(self.segments), "segment_ms": list(self.segment_ms), "audio_ms": self.audio_ms,
                "chars": len(self.text), "typed": self.typed, "merged": self.merged, "after_barge_in": self.after_barge_in}
