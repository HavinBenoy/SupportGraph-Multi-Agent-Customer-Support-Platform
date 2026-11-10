"""Cutting a document into addressable passages (one line each, with the heading it sits under) and locating values inside them. Offsets are character
positions in the document body, so a proposed edit is a precise, reversible patch: replace [start, end) with new text."""

import re
from dataclasses import dataclass

from app.knowledge.impact.spec import PERSON_ATTRS, Fact, attribute_words, words
from app.knowledge.impact.values import Span, find_value, kind_of

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class Passage:
    start: int                 # the whole line, without its newline
    end: int
    text: str
    heading: str               # "Parent > Child" chain of the markdown headings above it ('' if none)
    matches: tuple[Span, ...] = ()     # absolute offsets of the value occurrences inside this line
    lead: str = ""                      # the line this one continues ("He has ..." after "... is Santhosh sir") or the question above an answer; context, never edited


def lines(content: str) -> list[tuple[int, int, str]]:
    out, pos = [], 0
    for raw in content.split("\n"):
        out.append((pos, pos + len(raw), raw))
        pos += len(raw) + 1
    return out


def with_headings(content: str) -> list[tuple[int, int, str, str]]:
    stack: list[tuple[int, str]] = []
    out = []
    for a, b, raw in lines(content):
        m = _HEADING.match(raw)
        if m:
            level = len(m.group(1))
            stack = [h for h in stack if h[0] < level] + [(level, m.group(2))]
        out.append((a, b, raw, " > ".join(h[1] for h in stack)))
    return out


def heading_chain(content: str, offset: int) -> str:
    for a, b, _, h in with_headings(content):
        if a <= offset <= b:
            return h
    return ""


def find_passages(content: str, value: str) -> list[Passage]:
    """Every non-empty line that contains `value` (any spelling)."""
    spans = find_value(content, value)
    if not spans:
        return []
    out = []
    for a, b, raw, h in with_headings(content):
        if not raw.strip() or _HEADING.match(raw):
            continue
        inside = tuple(s for s in spans if a <= s.start and s.end <= b)
        if inside:
            out.append(Passage(a, b, raw, h, inside))
    return out


def best_lines(content: str, terms: list[str], limit: int = 3) -> list[Passage]:
    """Fallback for documents that do not contain the old value: the lines that share the most words with the change."""
    want = set(terms)
    scored = []
    for a, b, raw, h in with_headings(content):
        if not raw.strip() or _HEADING.match(raw):
            continue
        hit = len(want & set(words(raw)))
        if hit:
            scored.append((-hit, a, Passage(a, b, raw, h)))
    return [p for _, _, p in sorted(scored)[:limit]]


# ---------------------------------------------------------------------------------------------------------------------------- slot values
_VALUE_AFTER = re.compile(r"^\s*(?:is|are|will be|:|=|-|–|—)\s*(?P<val>[^\n]*?)\s*[.;]?\s*$", re.I)
_TITLES = {"sir", "madam", "mam", "ma'am", "dr", "mr", "mrs", "ms", "prof", "professor", "miss"}


def plausible_slot_value(fact: Fact, value: str) -> bool:
    """Is `value` the kind of thing the attribute holds? Replacing "excellent" in "the faculty is excellent" with a name would be a wrong edit, so a person attribute needs a
    name-like value, and a money / time attribute needs a money / time value."""
    v = " ".join(value.split())
    if not v or len(v) > 120 or len(v.split()) > 8:
        return False
    new_kind = kind_of(fact.new_value or "")
    if new_kind != "text":
        return kind_of(v) == new_kind
    if (fact.attribute or "").lower() in PERSON_ATTRS:
        ws = v.replace(".", " ").split()
        return any(w[:1].isupper() for w in ws) or any(w.lower() in _TITLES for w in ws)
    return True


def slot_passages(content: str, fact: Fact) -> list[tuple[Passage, Fact]]:
    """For a change that names no old value ("the ML faculty is now X"): every line that states the attribute with a value after it ("Machine Learning faculty is Jyothi sir",
    "Faculty: Jyothi sir"). Each passage comes with a copy of the fact whose old value is the value that line holds today, so the rest of the pipeline treats it like any
    other old -> new change. Whether the line is about the right SUBJECT is decided later by the scope guard, on the line plus the document's title and folder."""
    names = attribute_words(fact.attribute)
    if not names or not fact.new_value:
        return []
    rx = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")s?\b", re.I)
    out: list[tuple[Passage, Fact]] = []
    for a, b, raw, h in with_headings(content):
        if not raw.strip() or _HEADING.match(raw):
            continue
        for m in rx.finditer(raw):
            mv = _VALUE_AFTER.match(raw[m.end():])
            if not mv or not mv.group("val"):
                continue
            val = mv.group("val").strip()
            if not plausible_slot_value(fact, val):
                continue
            start = a + m.end() + mv.start("val")
            span = Span(start, start + len(mv.group("val")), mv.group("val"))
            out.append((Passage(a, b, raw, h, (span,)), fact.model_copy(update={"old_value": span.text.strip()})))
            break
    return out
