"""A model-independent safety net for escalation (build step 6). Pure code: no LLM, never imports graph/.

Why it exists: the router escalates when TRIAGE says so, and triage is a model. A real model can miss "a person in the lab keeps following
me and I'm scared to attend" or "I was double charged"; the offline rule-based triager did exactly that on the held-out evaluation set
(escalation recall 71%). For the cases where a miss costs the most (someone's safety, a legal threat, a duplicate charge), "use code for
rules" means the router must not depend on the model's recall alone.

This is a FLOOR, not the main classifier: it can only ADD an escalation, never remove one, and it is deliberately tuned for recall over
precision (a false alarm costs one staff glance; a miss can cost far more). Its precision cost is measured by `make eval`
(escalation precision) and the patterns are covered by tests/unit/test_escalation_floor.py, including look-alike sentences that must NOT fire.
"""

import re

# (category, minimum priority, pattern). Categories are the triage categories, so the existing team routing and SLA tables apply unchanged.
_RULES: list[tuple[str, str, re.Pattern]] = [
    ("safety", "urgent", re.compile(
        r"\b(unsafe|threaten\w*|stalk\w*|harass\w*|assault\w*|molest\w*|bull(y|ied|ying)|hit me|hurt me|touch(ed)? me|following me|follows me)\b"
        r"|\b(scared|afraid|frightened|terrified)\b.{0,40}\b(attend|come|go|class|centre|center|lab|him|her|them|someone|somebody|person)\b", re.I)),
    ("legal_threat", "urgent", re.compile(
        r"\b(legal action|legal notice|lawyer|advocate|sue you|sue the|consumer (court|forum|commission)|police complaint|file a case|"
        r"take (you|this|it) to court|approach the court)\b", re.I)),
    ("payment_dispute", "high", re.compile(
        r"\b(double|twice|two times|2 times|duplicate|again)\b.{0,30}\b(charged|paid|payment|deducted|debited)\b"
        r"|\b(charged|paid|deducted|debited)\b.{0,30}\b(twice|double|two times|2 times)\b|\bwrong(ly)? (charged|deducted|debited)\b", re.I)),
]
_ORDER = {"low": 0, "medium": 1, "high": 2, "urgent": 3}


def escalation_floor(text: str) -> tuple[str, str] | None:
    """(category, minimum priority) of the first rule the text matches, else None."""
    for category, priority, pattern in _RULES:
        if pattern.search(text):
            return category, priority
    return None


def raise_priority(current: str, floor: str) -> str:
    return floor if _ORDER.get(floor, 0) > _ORDER.get(current, 0) else current
