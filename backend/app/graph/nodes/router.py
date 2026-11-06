"""The router: pure code that turns a triage suggestion into one of four branches.

Rules, in priority order (first match wins). Nothing here calls a model, and nothing trusts triage for permissions:
  1. injection screen hit                      -> clarify (refuse)          [guardrail, independent of triage]
  2. urgent priority OR an escalation category -> escalate                  [a person must handle it]
  3. other_person_data                         -> clarify (refuse)          [privacy]
  4. chitchat                                  -> clarify (friendly close)
  5. confidence below the threshold            -> clarify (ask for detail)  [low confidence: ask, do not guess]
     EXCEPT category `other` with confidence in [other_floor, threshold): -> knowledge. An unrelated or unanswerable question gets
     a model-unsure triage (0.5 with the real 1.7B); the grounded FAQ answers it with a citation or abstains, which is a better
     reply than "name your batch". Below other_floor, and parse failures (0.0), still clarify.
  6. write request (make-up class, phone change) -> write IF signed in, else clarify (sign in)  [build step 4]
  6b. own-records question                      -> action IF the person is signed in, else clarify (sign in)
  7. everything else                           -> knowledge
Escalation outranks low confidence on purpose: a vague but alarming message is better seen by a person than quizzed by a bot.
Cost of the opposite error (over-escalation) is tracked by `make eval-routing` as escalation precision.
"""

from app.agents.schemas import ESCALATION_CATEGORIES, RECORD_CATEGORIES, WRITE_CATEGORIES, TriageResult
from app.guardrails.injection import looks_like_injection
from app.tools.scope import ANONYMOUS

KNOWLEDGE, ACTION, CLARIFY, ESCALATE, WRITE = "knowledge", "action", "clarify", "escalate", "write"


def is_signed_in(student_id: str | None) -> bool:
    return bool(student_id) and student_id != ANONYMOUS


def decide_route(triage: TriageResult, *, text: str, signed_in: bool, min_confidence: float, other_floor: float = 0.4) -> tuple[str, str]:
    """Returns (route, reason)."""
    if looks_like_injection(text):
        return CLARIFY, "injection_suspected"
    if triage.priority == "urgent" or triage.category in ESCALATION_CATEGORIES:
        return ESCALATE, triage.category if triage.category in ESCALATION_CATEGORIES else "urgent_priority"
    if triage.category == "other_person_data":
        return CLARIFY, "other_person_data"
    if triage.category == "chitchat":
        return CLARIFY, "chitchat"
    if triage.confidence < min_confidence:
        if triage.category == "other" and triage.confidence >= other_floor:
            return KNOWLEDGE, "other_low_confidence"
        return CLARIFY, "low_confidence"
    if triage.category in WRITE_CATEGORIES:
        return (WRITE, triage.category) if signed_in else (CLARIFY, "sign_in_required")
    if triage.category in RECORD_CATEGORIES and triage.wants_own_records:
        return (ACTION, "own_records") if signed_in else (CLARIFY, "sign_in_required")
    return KNOWLEDGE, triage.category
