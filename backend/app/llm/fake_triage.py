"""Offline rule-based triage for tests and the no-LLM demo. Returns the same `TriageResult` a real model would.

It is a keyword matcher, NOT language understanding: it exists to exercise the routing plumbing deterministically.
Its accuracy numbers say how well these rules fit the sample tickets (they were written with that set in view), not how a
real model would do. Judge real triage by running with a real model (SG_LLM_PROVIDER) and `make eval-routing`.
"""

import re

from app.agents.schemas import RECORD_CATEGORIES, TriageResult

_BATCH = re.compile(r"\b[A-Za-z]{2,4}-\d{3}\b")
_PERSONAL = re.compile(r"\bmy\b|\bam i\b|\bdid i\b|\bi (got|have|missed|attended)\b", re.I)
_POLICY_CUE = re.compile(r"\b(how long|how many|how much|what (is|are) the|do i need|do i have to|who is allowed|who can|"
                         r"policy|rule|eligible|need to)\b", re.I)
_LIST_BATCHES = re.compile(r"\b(which batch(es)?|enrolled in|my batch(es)?)\b", re.I)
_OTHER_PERSON = re.compile(r"[A-Z][a-z]+ [A-Z][a-z]+'s\b|\b(email addresses|phone numbers)\b.*\bstudents?\b|\bstudents? in (batch|the)\b"
    r"|\b(my|his|her)\s+(friend|classmate|brother|sister|cousin|roommate)\b|\b(someone|somebody|another student|other students?)(\s+else)?'s\b", re.I)
_HINDI_ROMAN = re.compile(r"\b(ki|kitni|kitna|hai|kya|mujhe|nahi|aap)\b", re.I)

# Escalation reasons, checked in this order (most serious first).
_ESCALATE = [
    ("safety", r"\bunsafe\b|\bthreaten|\bassault|\bharass|\binjur", "urgent"),
    ("legal_threat", r"\blegal action\b|\blawyer\b|\bsue\b|\bconsumer (court|forum)\b", "urgent"),
    ("human_request", r"\b(talk|speak) to (a |an )?(person|human|someone|agent|counsellor)\b", "medium"),
    ("payment_dispute", r"\b(paid|charged|deducted)\b.*\b(twice|double|wrong)\b|\bmoney back\b|\bchargeback\b", "high"),
    ("complaint", r"\bcomplain|\bshouted\b|\brude\b|\bmisbehav", "high"),
    ("concession_request", r"\bdiscount\b|\bconcession\b|\bwaive", "medium"),
]
# Knowledge/record categories in priority order: the first whose keywords appear wins.
_TOPICS = [
    ("privacy_policy", r"\bprivacy\b|\bwho (can|is allowed to) see\b|\bpersonal (data|details)\b"),
    ("assessment", r"\bmarks?\b|\bscores?\b|\bquiz|\bassignments?\b|\bexams?\b|\bre-?check\b|\bresults?\b|\bgrades?\b|\bcertificat"),
    ("attendance", r"\battend|\babsent\b|\bmissed\b"),
    ("schedule", r"\bschedule\b|\bnext class\b|\bwhen is\b|\bwhich batch|\benrolled in\b|\bmy batch"),
    ("placement", r"\bplacements?\b|\bjobs?\b|\bsalary\b|\bcareer\b|\binterviews?\b|\bresume\b|\bhiring\b|\bcompanies\b|\blpa\b|\blakh\b"),
    ("fees", r"\bfees?\b|\bprice\b|\bcost\b|\bpay\b|\bpayments?\b|\binstal+ments?\b|\brefund|\bgst\b|\bemi\b|\bupi\b|\bscholarship"),
    ("eligibility", r"\beligib|\bprerequisite|\bcoding experience\b|\bdocuments?\b|\bfinal[- ]year\b|\bcan i join\b|\bjoin\b"),
    ("admissions", r"\badmission|\bregist|\bdemo\b|\benquir|\benrol"),
    ("courses", r"\bcourses?\b|\bprogramm?es?\b|\bduration\b|\bsyllabus\b|\bcentres?\b|\bbatch\b|\bpython\b|\btesting\b|\bdata science\b|\bdevops\b|\bjava\b|\bcurriculum\b"),
]
_SUPPORT = re.compile(r"\bsupport team\b|\breply\b|\brespond\b|\braise an issue\b", re.I)


def _language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    return "hi" if len(_HINDI_ROMAN.findall(text)) >= 2 else "en"


class RuleTriage:
    """`classify(text) -> TriageResult`, the interface every triager implements."""

    def classify(self, text: str) -> TriageResult:
        lang = _language(text)
        t = text.strip()
        low = t.lower()
        angry = bool(re.search(r"!!|\bunacceptable\b|\bfurious\b|\bshouted\b|\bthreaten", low))
        sentiment = "angry" if angry else "neutral"

        if re.match(r"^(thanks|thank you|ok|okay|great|bye)\b", low) and len(low.split()) <= 8:
            return TriageResult(category="chitchat", sentiment="positive", language=lang, confidence=0.95)
        if _OTHER_PERSON.search(t):
            return TriageResult(category="other_person_data", language=lang, confidence=0.9)
        for category, pattern, priority in _ESCALATE:
            if re.search(pattern, low):
                return TriageResult(category=category, priority=priority, sentiment="angry" if angry else "negative",
                                    language=lang, confidence=0.9)
        if re.search(r"\bmake-?up (class|session)\b|\bmissed (a |the )?(class|session)\b.*\b(make|arrange|reschedule)", low):
            return TriageResult(category="makeup_request", priority="medium", language=lang, confidence=0.9)
        if re.search(r"\b(phone|mobile|contact)\s+(number|no\.?)\b.*\b(change|update)|\b(change|update)\b.*\b(phone|mobile|contact)\s+(number|no\.?)\b", low):
            return TriageResult(category="contact_update", language=lang, confidence=0.9)
        for category, pattern in _TOPICS:
            if re.search(pattern, low):
                has_batch = bool(_BATCH.search(t))
                personal = (has_batch or bool(_PERSONAL.search(t))) and not (_POLICY_CUE.search(t) and not has_batch)
                confidence = 0.85
                if category in RECORD_CATEGORIES and personal and not has_batch and not _LIST_BATCHES.search(t):
                    confidence = 0.5  # "how is my attendance?" with no batch: not enough to act on
                return TriageResult(category=category, sentiment=sentiment, language=lang, confidence=confidence,
                                    wants_own_records=personal and category in RECORD_CATEGORIES)
        return TriageResult(category="other", language=lang, sentiment=sentiment, confidence=0.7 if _SUPPORT.search(t) else 0.4)
