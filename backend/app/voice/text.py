"""Turning a written chat reply into something worth saying aloud, and splitting it so speech can start early.
The WRITTEN reply is never changed (it is what the transcript and audit show); only the spoken version is adapted."""

import re

CITATION = re.compile(r"\s*\[[a-z0-9][a-z0-9-]*#\d+\]")
MD = re.compile(r"[*_`#>]+")
URL = re.compile(r"https?://\S+")
BATCH_ID = re.compile(r"\b([A-Z]{2,4})-(\d{2,4})\b")
REF_ID = re.compile(r"\b(ESC|REQ|MK|TK)-([A-Za-z0-9]{3,12})\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
SAMPLE_NOTICE = re.compile(r"\(Demo data:[^)]*\)\s*", re.I)


def _spell(token: str) -> str:
    return " ".join(token)


def speakable(reply: str, *, max_chars: int = 600) -> str:
    """Remove source ids, markdown and links; read batch ids and reference numbers character by character; cap the length at a sentence."""
    t = SAMPLE_NOTICE.sub("", reply or "")
    t = CITATION.sub("", t)
    t = URL.sub("the link in the chat", t)
    t = MD.sub("", t)
    t = REF_ID.sub(lambda m: f"{_spell(m.group(1))} dash {_spell(m.group(2))}", t)
    t = BATCH_ID.sub(lambda m: f"{_spell(m.group(1))} {_spell(m.group(2))}", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > max_chars // 3 else cut.rsplit(" ", 1)[0]) + " The rest is in the chat."


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text) if s.strip()]
