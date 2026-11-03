"""Deterministic QA checks on a drafted reply. Pure functions, no LLM, no database: they run on every reply before the
person sees it. A failing check BLOCKS the reply (it is revised once, then replaced by a safe fallback).

What they catch, each independent of how clever the model is:
  empty_reply          nothing to send
  internal_leak        a line of an internal-only document appears in the reply
  other_student_id     the reply names a student id other than the person asking
  pii_in_reply         the reply contains an email/phone that is in neither the question nor a cited passage
  forbidden_promise    claims the bot cannot make: guaranteed jobs/salary, "I have approved/refunded...", "refund will be issued"
  uncited_answer       an answered knowledge reply with no citation
  unsupported_number   a number (fee, %, weeks) that is not in any cited passage
  entity_mismatch      the question names a specific thing (a course, a batch, a city) that no cited passage mentions:
                       the classic "right-looking answer about the wrong thing" that a citation alone does not catch
What they do NOT catch: a plausible, cited, wrong paraphrase. That needs the optional LLM critic (critic.py) and human review.
"""

import re
from dataclasses import dataclass

from app.guardrails.pii import find_pii
from app.rag.evaluate import unsupported_numbers

SECURITY_CODES = frozenset({"internal_leak", "other_student_id", "pii_in_reply"})
_STUDENT_ID = re.compile(r"\bstu_[a-z0-9_]+\b", re.I)
_PROPER = re.compile(r"\b([A-Z][A-Za-z0-9+#.-]{1,}(?:-\d+)?|[A-Z]{2,4}-\d{3})\b")
# Capitalised words that are not entities: question starters, the institute's own brand, generic words.
_NOT_ENTITIES = frozenset({
    "what", "which", "who", "whom", "when", "where", "why", "how", "can", "could", "do", "does", "did", "is", "are", "am", "was",
    "will", "would", "should", "may", "might", "please", "hi", "hello", "hey", "thanks", "thank", "tell", "show", "give", "i",
    "my", "me", "we", "you", "the", "a", "an", "and", "or", "if", "also", "any", "are", "there", "ok", "okay", "yes", "no",
    "qspiders", "pyspiders", "jspiders", "tspiders", "bspiders", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december",
})
_NEGATION = re.compile(r"(\bnot\b|\bno\b|n't\b|\bnever\b|\bcannot\b|\bwithout\b)[^.]{0,25}$", re.I)
_PROMISES = [
    ("guarantee", re.compile(r"\bguarantee[sd]?\b[^.]{0,40}\b(job|placement|salary|package|hike)\b", re.I)),
    ("first_person_action", re.compile(r"\bI(?:'ve| have)\s+(?:approved|refunded|waived|cancelled|canceled|processed|granted|issued)\b", re.I)),
    ("refund_promise", re.compile(r"\b(refund|concession|discount|waiver)\b[^.]{0,30}\bwill be\s+(?:processed|issued|credited|granted|given|approved)\b", re.I)),
]


@dataclass(frozen=True)
class QaIssue:
    code: str
    detail: str = ""

    @property
    def is_security(self) -> bool:
        return self.code in SECURITY_CODES


def _promise(reply: str) -> str | None:
    for name, pat in _PROMISES:
        for m in pat.finditer(reply):
            if not _NEGATION.search(reply[max(0, m.start() - 30):m.start()]):  # "does not guarantee a job" is honest
                return name
    return None


def question_entities(question: str) -> list[str]:
    """Specific things the question names. The first word of a sentence is ignored (it is capitalised anyway)."""
    found, seen = [], set()
    for sentence in re.split(r"(?<=[.!?])\s+", question):
        words = _PROPER.findall(sentence)
        first = sentence.split()[0].strip("?,.!").lower() if sentence.split() else ""
        for w in words:
            lw = w.lower()
            if lw in _NOT_ENTITIES or (lw == first and "-" not in w) or lw in seen:
                continue
            seen.add(lw)
            found.append(w)
    return found


def run_checks(*, reply: str, question: str, student_id: str | None, markers: list[str], route: str, abstained: bool,
               citations: list[str], sources: list[dict]) -> list[QaIssue]:
    issues: list[QaIssue] = []
    low = " ".join(reply.lower().split())
    if not reply.strip():
        return [QaIssue("empty_reply")]
    if any(m in low for m in markers):
        issues.append(QaIssue("internal_leak"))
    for sid in {s.lower() for s in _STUDENT_ID.findall(reply)}:
        if sid != (student_id or "").lower():
            issues.append(QaIssue("other_student_id", sid))
    known = " ".join([question, *(str(s.get("text", "")) for s in sources)])
    for item in find_pii(reply):
        if item not in known:
            issues.append(QaIssue("pii_in_reply", "contact detail"))
            break
    if (p := _promise(reply)):
        issues.append(QaIssue("forbidden_promise", p))
    if route == "knowledge" and not abstained:
        if not citations:
            issues.append(QaIssue("uncited_answer"))
        bad = unsupported_numbers(reply, sources)
        if bad:
            issues.append(QaIssue("unsupported_number", "; ".join(map(str, bad))))
        text = " ".join(f"{s.get('heading', '')} {s.get('text', '')}" for s in sources).lower()
        for ent in question_entities(question):
            if ent.lower() not in text:
                issues.append(QaIssue("entity_mismatch", ent))
    return issues
