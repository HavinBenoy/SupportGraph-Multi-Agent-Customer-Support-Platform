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
    # An explicit request for a person. Added in M1b (the real 1.7B triage answered "I want to talk to a person" as `other` at 0.5) and
    # widened in M2.1 for spoken, imperfectly transcribed requests ("please forward this call towards your supporting"). Not urgent
    # (priority stays as triage set it); the team comes from the tenant's escalation_teams for human_request.
    ("human_request", "low", re.compile(
        # talk / speak / chat / connect / transfer ... to a person, an agent, the support team, a counsellor
        r"\b(talk|speak|chat|connect|transfer)(ing)?\s+(me\s+)?(to|with)\s+(a|an|the|your|our)?\s*(real\s+|live\s+|actual\s+|human\s+|customer\s+)?"
        r"(person|human|agent|representative|executive|staff member|supporting|support|customer (care|support|service)|counsel+or|manager|supervisor|someone(?!\s+(who|else)))\b"
        # forward / transfer / redirect / put me through ... to / towards the support, a person, an agent
        r"|\b(forward|transfer|redirect|reroute|route|divert|patch|put|pass|hand|escalate|connect)\w*\b.{0,25}?\b(to|towards?|through to|over to|with)\s+"
        r"(?:(?:your|the|a|an|our)\s+)?(?:(?:real|live|actual|human|customer|support|supporting)\s+)*"
        r"(support|supporting|person|human|agent|representative|executive|staff|someone(?!\s+(who|else))|team|helpline|customer care|care|desk|counsel+or|manager|supervisor)\b"
        # M2.1.1: "please proceed towards your support desk", "send me to your support team", "take this to your manager". The weak verbs
        # (proceed/go/send/take/...) need "your/our" so that "can I go to the support centre?" stays a question about a place.
        r"|\b(proceed|go|move|head|take|send|pass|hand|bring|direct|refer|switch|shift)\w*\b(?:\s+(?:me|us|this|my|the call|this call|the chat|over|ahead|on|along))*\s+(?:to|towards?)\s+(?:your|our)\s+"
        r"(?:(?:real|live|human|customer|support|supporting|help)\s+)*(support|supporting|helpdesk|help desk|desk|customer care|team|staff|executive|agent|person|representative|manager|supervisor|helpline|counsel+or)\b"
        # "let a human handle this", "have someone from support call me"
        r"|\blet\s+(?:a|an|the|your)?\s*(?:real\s+|live\s+)?(?:human|person|agent|someone|support)\b.{0,15}\b(?:handle|take over|answer|deal with|help)\b"
        r"|\b(?:someone|somebody|a person|an agent|a human)\s+from\s+(?:the\s+|your\s+)?(?:support|customer care|helpline)\b"
        # the destination alone: "support desk please", "customer care", "your helpline"
        r"|^\W*(?:please\s+)?(?:(?:a|the|your)\s+)?(?:support desk|help ?desk|front desk|support team|support executive|support staff|support agent|customer care|helpline)(?:\s+please)?\W*$"
        # "I want / need / would like to contact / call / reach the support", "please contact support", "can you call someone"
        r"|\b(i\s+(want|need|would like|wanna|have)\s+to|i'?d like to|please|can you|could you|will you|let me)\s+(contact|call|reach|ring|phone|get)\s+"
        r"(?:(?:me|us|to|a|an|the|your|our)\s+)*(?:(?:real|live|actual|human|customer)\s+)*(support|supporting|person|human|agent|representative|executive|staff|someone(?!\s+(who|else))|helpline|customer care|counsel+or|manager|supervisor)\b"
        # "get me a human", "give me an agent", "I need a real person"
        r"|\b(get|give|find|bring)\s+me\s+(a|an|the|your)?\s*(real\s+|live\s+|human\s+)?(human|person|agent|representative|supervisor|manager)\b|\bi\s+(need|want)\s+(a|an)\s+(real\s+|live\s+)?(human|person|agent|representative)\b"
        r"|\b(real|live|actual)\s+(person|human)\b|\bhuman\s+(agent|being|support)\b|\bnot\s+(a\s+)?(bot|robot|machine)\b"
        r"|\b(don'?t|do not)\s+want\s+(to\s+)?(talk|speak|chat)(ing)?\s+(to|with)\s+(a\s+|an\s+)?(bot|robot|machine|ai|computer)\b"
        r"|^\W*(please\s+)?(a\s+)?(human|agent|representative|operator|customer care|customer support|real person)(\s+please)?\W*$", re.I)),
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
