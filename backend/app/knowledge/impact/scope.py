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


def is_historical(heading: str, line: str, today: date | None = None) -> bool:
    """A passage that is a record of the past, not the current fact: it sits under a historical heading, says so ("previous", "until", "was"), or carries an
    explicit year before this year."""
    year = (today or date.today()).year
    for chunk in (heading, line):
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


def out_of_scope(spec: ChangeSpec, text: str) -> bool:
    """Is this text about something other than the change's subject? A different alternative (weekday vs weekend), a missing distinguishing term (Java vs Python), or,
    when nothing distinguishes the subject, fewer than half of its words. Aliases of the subject (ML = machine learning) are expanded first."""
    from app.knowledge.impact.aliases import expand
    text = expand(text, spec.alias_pairs)
    if sibling_conflict(spec, text):
        return True
    if spec.key_terms:
        return scope_fraction(spec.key_terms, text) < 1.0
    return bool(spec.entity_terms) and scope_fraction(spec.entity_terms, text) < 0.5


def context_of(doc, p) -> str:
    return f"{doc.title} {doc.path} {doc.category} {p.heading} {p.text}"
