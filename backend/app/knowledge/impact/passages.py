"""Cutting a document into addressable passages (one line each, with the heading it sits under) and locating values inside them. Offsets are character
positions in the document body, so a proposed edit is a precise, reversible patch: replace [start, end) with new text."""

import re
from dataclasses import dataclass

from app.knowledge.impact.spec import words
from app.knowledge.impact.values import Span, find_value

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class Passage:
    start: int                 # the whole line, without its newline
    end: int
    text: str
    heading: str               # "Parent > Child" chain of the markdown headings above it ('' if none)
    matches: tuple[Span, ...] = ()     # absolute offsets of the value occurrences inside this line


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
