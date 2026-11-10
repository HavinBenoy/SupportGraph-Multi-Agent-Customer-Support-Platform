"""Scope and history checks shared by discovery, scanning, evaluation and verification (moved here from evaluate/discover so they can be used without import cycles; both
modules still export the same names). Pure text functions, no database, no model."""

import re
from datetime import date

from app.knowledge.impact.spec import ChangeSpec, sibling_marks, words

_HIST_WORDS = re.compile(r"\b(history|historical|archive[ds]?|previous(?:ly)?|prior|former(?:ly)?|superseded|obsolete|outdated|old|earlier|until|till|before|was|were|used to|no longer|expired|valid\s+(?:until|till|through))\b", re.I)
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def scope_fraction(terms: list[str], haystack: str) -> float:
    if not terms:
        return 1.0
    have = set(words(haystack))
    return sum(1 for t in terms if t in have) / len(terms)


_SENTENCE = re.compile(r"(?<=[.!?;])\s+")


def _sentences_with(line: str, matches, base: int) -> str:
    """The sentences of `line` that contain one of the value occurrences (absolute offsets, `base` = absolute start of the line); the whole line when nothing is known."""
    if not matches:
        return line
    parts, pos = [], 0
    for piece in _SENTENCE.split(line):
        a = line.find(piece, pos)
        pos = a + len(piece)
        if any(a <= m.start - base < pos or a < m.end - base <= pos for m in matches):
            parts.append(piece)
    return " ".join(parts) or line


def is_historical(heading: str, line: str, today: date | None = None, matches=(), base: int = 0) -> bool:
    """A passage that is a record of the past, not the current fact: it sits under a historical heading, says so ("previous", "until", "was"), or carries an
    explicit year before this year. In a line of several sentences only the sentence that holds the value counts: "He has 3 years of experience. Previously he worked in IT."
    is a current fact followed by history."""
    year = (today or date.today()).year
    for chunk in (heading, _sentences_with(line, matches, base)):
        if _HIST_WORDS.search(chunk):
            return True
        if any(int(y) < year for y in _YEAR.findall(chunk)):
            return True
    return False


def sibling_conflict(spec: ChangeSpec, text: str) -> bool:
    """The change names one alternative (the weekend batch, the morning slot, batch A) and this text is about a DIFFERENT alternative of the same thing."""
    want = sibling_marks(" ".join(spec.entity_terms))
    have = sibling_marks(text)
    for key, val in have:
        mine = {v for k, v in want if k == key}
        if mine and val not in mine and not any(k == key and v in mine for k, v in have):
            return True
    return False


_PERSON = re.compile(r"\b([A-Za-z]{3,})\s+(?:sir|madam|mam|garu|ji)\b", re.I)


def person_terms(spec: ChangeSpec) -> list[str]:
    """A person the change is ABOUT ("the work experience of santhosh sir"): the name must be on the passage, a shared title ("sir") proves nothing."""
    return [n for n in (m.group(1).lower() for m in _PERSON.finditer(spec.summary or "")) if n in spec.entity_terms]


def out_of_scope(spec: ChangeSpec, text: str) -> bool:
    """Is this text about something other than the change's subject? A different alternative (weekday vs weekend), a missing distinguishing term (Java vs Python), or,
    when nothing distinguishes the subject, fewer than half of its words. Aliases of the subject (ML = machine learning) are expanded first."""
    from app.knowledge.impact.aliases import expand
    text = expand(text, spec.alias_pairs)
    if sibling_conflict(spec, text):
        return True
    need = list(dict.fromkeys([*spec.key_terms, *person_terms(spec)]))
    if need:
        return scope_fraction(need, text) < 1.0
    return bool(spec.entity_terms) and scope_fraction(spec.entity_terms, text) < 0.5


def context_of(doc, p) -> str:
    return f"{doc.title} {doc.path} {doc.category} {p.heading} {getattr(p, 'lead', '')} {p.text}"
