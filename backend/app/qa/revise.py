"""Offline reviser: removes the sentences that carry the problem instead of rewriting. Deterministic, so the loop can be
tested. It can only fix what is local to a sentence (an unsupported number, a promise); anything else returns empty,
which makes the QA loop fall back to the safe message."""

import re

from app.qa.checks import SECURITY_CODES, QaIssue, _promise

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
FIXABLE = frozenset({"unsupported_number", "forbidden_promise"})


def _mentions(sentence: str, number: str) -> bool:
    """True if the sentence contains this number however it is written ("99000" matches "99,000")."""
    pattern = ",?".join(re.escape(c) for c in number.replace(",", ""))
    return re.search(rf"(?<![\d.,]){pattern}(?!\d|,\d)", sentence) is not None


class DeterministicReviser:
    def revise(self, *, reply: str, issues: list[QaIssue], sources: list[dict]) -> str:  # noqa: ARG002
        if any(i.code not in FIXABLE for i in issues) or any(i.code in SECURITY_CODES for i in issues):
            return ""
        numbers = {n.strip() for i in issues if i.code == "unsupported_number" for n in i.detail.split(";") if n.strip()}
        kept = []
        for s in _SENTENCE.split(reply):
            if _promise(s) or any(_mentions(s, n) for n in numbers):
                continue
            kept.append(s)
        return " ".join(kept).strip()
