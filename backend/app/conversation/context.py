"""Conversation context for the shared support graph (M2.2): follow-ups and corrections that only make sense with the previous question.

    "What is the fee structure for Python Full Stack?"  ->  "Are there any discounts?"       => "Are there any discounts for Python Full Stack?"
    "What is the fee?" (course named earlier)           ->  "Actually, I meant Data Science." => "What is the fee for Data Science?"
    "What is the fee for Python Full Stack?"            ->  "Actually I meant Data Science"   => "What is the fee for Data Science?"

Pure code, deterministic, no model. It only ever rewrites the text that triage CLASSIFIES and the knowledge branch RETRIEVES with. It adds at most
five words, taken from the caller's own earlier question in this conversation. Injection screening, the escalation floor, RBAC, the action and
write branches and the QA checks keep using what the caller actually said, so this cannot be used to smuggle anything past them. A text that
names its own topic, or has nothing referential in it, is returned unchanged: unrelated new questions are never contaminated by old context.
"""

import re

FOLLOW_UP, CORRECTION = "follow_up", "correction"
LOOKBACK = 3            # previous caller messages that may supply the topic
MAX_TOPIC_WORDS = 5
MAX_FOLLOWUP_WORDS = 14  # a long sentence names its own subject

_BATCH = re.compile(r"\b[A-Z]{2,5}-\d{2,4}\b")
_PREP = re.compile(r"\b(?:for|about|of|in|on|regarding|concerning)\s+((?:[\w&+.#'-]+\s*){1,8})", re.I)
_TITLE = re.compile(r"(?:(?<=[\s,(])|^)((?:[A-Z][\w&+.#'-]*)(?:\s+(?:[A-Z][\w&+.#'-]*|and|of))*)")
_GENERIC = frozenset({
    "course", "courses", "batch", "batches", "class", "classes", "fee", "fees", "details", "detail", "information", "info", "more", "it", "this",
    "that", "them", "those", "these", "me", "you", "us", "my", "your", "our", "example", "instance", "now", "today", "free", "sure", "real", "a", "an",
    "the", "any", "some", "other", "another", "which", "what", "how", "there", "here", "one", "ones", "same", "different", "everyone", "anyone",
})
_STOP_TAIL = frozenset({"and", "or", "but", "please", "then", "also", "too", "now", "right", "today", "thanks", "if", "so", "because", "is", "are",
                        "was", "do", "does", "can", "will", "would", "should", "what", "how", "when", "where", "why", "which", "who", "to"})
_TRAIL_NOUNS = frozenset({"course", "courses", "program", "programme", "batch", "batches", "class", "classes", "training", "fee", "fees"})
_START = re.compile(r"^(?:and|also|what about|how about|then|so|or|what if|but)\b|^any\b|^(?:are|is) there any\b|^do you (?:have|offer) any\b|^(?:any )?other\b", re.I)
_REF = re.compile(r"\b(?:it|its|they|their|them|that|this|those|these|the same|there)\b", re.I)
_ASKS = re.compile(r"\b(?:fee|fees|price|cost|discount|discounts|scholarship|emi|instal?ments?|duration|eligibility|syllabus|curriculum|certificate|"
                   r"placement|refund|prerequisites?|requirements?|how long|how much)\b", re.I)
_FIX = re.compile(
    r"^(?:(?:actually|no|sorry|oh|wait|hmm|well|okay|ok|i mean|rather|um|uh)[\s,]+)*"
    r"(?:(?:i|we)\s+(?:meant|mean|wanted|was asking|am asking|'m asking|was talking|am talking|was referring|am referring|was thinking)(?:\s+(?:to ask|to know|to say))?"
    r"(?:\s+(?:about|for|to|of|regarding))?|make that|change (?:that|it) to|(?:it|that) should be|(?:it'?s|its|it is) for|"
    r"not (?P<neg>[^,]+?),?\s*(?:but|i meant|i mean)|(?:i )?meant)\s*(?P<x>.+?)[\s.!?]*$", re.I)


def _clean_topic(raw: str) -> str | None:
    words = re.findall(r"[\w&+.#'-]+", raw)
    while words and words[0].lower() in {"the", "a", "an", "your", "our", "this", "that", "course", "for"}:
        words = words[1:]
    words = words[:MAX_TOPIC_WORDS]
    for i, w in enumerate(words):             # stop at the first word that begins a new clause
        if i > 0 and w.lower() in _STOP_TAIL:
            words = words[:i]
            break
    while words and words[-1].lower() in _STOP_TAIL | _TRAIL_NOUNS:
        words.pop()
    if not words or all(w.lower() in _GENERIC for w in words) or words[0].lower() in _GENERIC:
        return None
    return " ".join(words)


def extract_topic(text: str) -> str | None:
    """The course/batch the text is about: a batch id, else the words after for/about/of/..., else a Title-Case name."""
    text = (text or "").strip()
    if not text:
        return None
    m = _BATCH.search(text)
    if m:
        return m.group(0)
    for m in reversed(list(_PREP.finditer(text))):
        t = _clean_topic(m.group(1))
        if t:
            return t
    first_end = re.match(r"\s*\S+", text)
    body = text[first_end.end():] if first_end else text   # the first word is capitalised by grammar, not because it is a name
    for m in _TITLE.finditer(body):
        t = _clean_topic(m.group(1))
        if t and t.lower() not in {"i", "i'm", "i'd", "i've", "i'll"}:
            return t
    return None


def _usable_history(history: list[str], looks_like_injection) -> list[str]:
    from app.conversation.intents import detect_intent
    out = []
    for h in reversed(history[-LOOKBACK - 4:]):
        h = " ".join((h or "").split())
        if len(h.split()) < 3 or detect_intent(h) is not None or looks_like_injection(h):
            continue
        out.append(h)
        if len(out) == LOOKBACK:
            break
    return out


def resolve(text: str, history: list[str], looks_like_injection=lambda _t: False) -> tuple[str, str]:
    """(text to classify and retrieve with, kind). kind is "" (unchanged), "follow_up" or "correction". `history` = earlier caller messages."""
    text = " ".join((text or "").split())
    if not text or not history:
        return text, ""
    prior = _usable_history(history, looks_like_injection)
    if not prior:
        return text, ""
    fix = _FIX.match(text)
    if fix and fix.group("x"):
        new = _clean_topic(fix.group("x"))
        if new:
            for q in prior:
                if _FIX.match(q):
                    continue
                old = extract_topic(q)
                if old and old.lower() != new.lower():
                    return re.sub(re.escape(old), new, q, count=1, flags=re.I).rstrip(" ?.!") + "?", CORRECTION
                if not old:
                    return q.rstrip(" ?.!") + f" for {new}?", CORRECTION
                return q, CORRECTION       # the same topic again: nothing to change
    if extract_topic(text) is not None or len(text.split()) > MAX_FOLLOWUP_WORDS:
        return text, ""                    # it names its own subject: a new question
    # A bare "this"/"it" proves nothing ("Let me end this"): a pronoun only counts together with something the caller is asking about.
    if not (_START.search(text) or (_REF.search(text) and _ASKS.search(text)) or (_ASKS.search(text) and len(text.split()) <= 7)):
        return text, ""
    for q in prior:
        topic = extract_topic(q)
        if topic:
            return text.rstrip(" ?.!") + f" for {topic}?", FOLLOW_UP
    return text, ""
