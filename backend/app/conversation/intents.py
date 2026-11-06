"""Conversation-control intents: greetings, thanks, acknowledgements, goodbyes and "never mind / I'm just testing".

Pure code: no model, no database, nothing the person says can change the wording of the replies (they are constants in
`graph/nodes/replies.py`). It sits in the shared support graph, so the text chat and the voice line behave identically.

The rule that keeps it safe: an utterance counts only when EVERY sentence of it is one of these pleasantries. "Thanks, what is the fee
for PYF-101?" has a real question in it, so no intent is detected and the normal flow (triage, RAG, escalation, RBAC) runs unchanged.
The router also checks the escalation floor and the injection screen first, so "bye, someone is following me" still escalates.

Asking for a person is NOT handled here: that is the escalation floor's `human_request` rule (guardrails/escalation_floor.py).

Deliberately absent: "yes" and "no" (they answer a pending confirmation; see graph/review.py) and bare "stop" (the caller's barge-in).
"""

import re

GREETING, THANKS, ACK, GOODBYE, INTERRUPTION = "greeting", "thanks", "acknowledgement", "goodbye", "interruption"
INTENTS = (GREETING, THANKS, ACK, GOODBYE, INTERRUPTION)

_SPLIT = re.compile(r"[.!?;]+\s*|\s*,\s+(?=(?:and\s+)?(?:you|that|i|we|thank|thanks|bye|goodbye|ok|okay)\b)|\n+", re.I)
_FILLER = re.compile(r"^(?:(?:um+|uh+|hmm+|well|so|and|but|then|ok(?:ay)?|alright|right|oh|ah|please|just)\b[\s,]*)+", re.I)
_TRAIL = re.compile(r"[\s,]*\b(?:please|thanks|thank you|then|now|ok(?:ay)?|sir|ma'?am|bro)\s*$", re.I)
_NAME = r"(?:\s+(?:there|team|support|assistant|bot|everyone|all))?"

_RULES: list[tuple[str, re.Pattern]] = [
    (GOODBYE, re.compile(
        r"^(?:no(?:pe)?[\s,]+)?(?:good\s*bye|bye(?:[\s-]*bye)?|see (?:you|ya)(?: (?:later|soon|around))?|take care|talk (?:to you )?later|have a (?:good|great|nice|lovely) (?:day|one|evening|night|time)|"
        r"that'?s (?:all|it)(?: (?:for now|from me|for today))?|that is (?:all|it)(?: for now)?|that will be all|that would be all|"
        r"(?:i'?m|i am|we'?re|we are) (?:all )?(?:done|good|finished|set)(?: (?:here|now|for now|for today))?|all (?:good|done|set)|nothing (?:else|more)|"
        r"(?:i )?(?:have|got) no (?:more|other|further) questions|"
        r"(?:(?:you can|you may|i will|i'll|i'?m going to|let'?s|please)\s+)?(?:go ahead and )?(?:hang ?up|disconnect)(?: (?:the|this|our|my|that)(?: (?:call|chat|conversation|session|line))?)?(?: now| here| for now)?|"
        r"(?:(?:you can|you may|i will|i'll|i'?m going to|let'?s|please)\s+)?(?:go ahead and )?(?:stop|end|finish|cut|close|drop|terminate)(?: (?:the|this|our|my|that))? (?:call|chat|conversation|session|line)(?: now| here| for now)?|"
        r"(?:you can |you may )(?:go|leave)(?: now)?|(?:i )?(?:have to|need to|got to|gotta) (?:go|leave|run|hang up))$", re.I)),
    (THANKS, re.compile(
        r"^(?:thanks?(?: you)?(?: (?:a lot|so much|very much|a ton|again|for (?:your|the) (?:help|time|information|info|answer|support)|for calling|for that))*|"
        r"thx|ty|cheers|many thanks|much appreciated|(?:i )?(?:really )?appreciate (?:it|that|your help|the help)|thank you (?:so|very) much|"
        r"that was (?:helpful|useful|great)|that helps|that'?s (?:helpful|useful|very helpful))$", re.I)),
    (INTERRUPTION, re.compile(
        r"^(?:(?:i(?: am|'?m)|we(?: are|'?re)|this is|it'?s|it is)\s+(?:just |only |simply )?(?:a )?(?:test(?:ing)?|checking|trying(?: (?:it|this|you|this out|you out))?|kidding)(?: (?:you|it|this|out|the (?:system|call|line|bot|assistant)|your (?:response|answer|replies|voice)))*|"
        r"(?:i )?(?:was|am) (?:just |only )?(?:testing|checking|trying)(?: (?:you|it|this|out|the (?:system|call|line|bot|assistant)|your (?:response|answer|replies|voice)))*|"
        r"just (?:a )?(?:test(?:ing)?|checking|kidding)|(?:this is )?(?:a )?(?:test|testing)(?: (?:one|1|two|2|three|3|call|only))*|"
        r"(?:testing[\s,]*)+(?:one|1)?[\s,]*(?:two|2)?[\s,]*(?:three|3)?|sorry|my (?:bad|mistake)|(?:i'?m|i am) sorry|excuse me|pardon(?: me)?|"
        r"never ?mind|forget (?:it|that)|(?:ignore|disregard) (?:that|it|what i said)|scratch that|"
        r"wait(?: (?:a|one) (?:second|sec|moment|minute))?|hold on(?: (?:a|one) (?:second|sec|moment|minute))?|one (?:second|sec|moment|minute)|give me (?:a|one) (?:second|sec|moment|minute)|"
        r"(?:no[\s,]*)+(?:that'?s )?(?:not (?:what )?(?:i (?:asked|meant|said))|wrong)|that'?s not what i (?:asked|meant|said)|i (?:didn'?t|did not) (?:ask|say|mean) that|not that)$", re.I)),
    (ACK, re.compile(
        r"^(?:ok(?:ay)?(?: (?:cool|great|fine|then|thanks?))?|alright|all right|fine|sure|got it|i (?:see|understand|got it|get it)|understood|noted|right|cool|nice|great|perfect|awesome|excellent|good|"
        r"(?:that'?s|that is|it'?s|it is) (?:fine|ok(?:ay)?|good|great|perfect|alright|all right|cool|nice|clear|understood|enough|noted)|that works(?: for me)?|makes sense|(?:no )?(?:problem|worries|issue)|no worries|no thanks?|no thank you|nope|"
        r"sounds (?:good|great|fine|right)|(?:all )?clear|mm+(?:[\s-]*hm+)?|uh[\s-]*huh|i (?:know|knew)|oh (?:ok(?:ay)?|i see|nice|great|cool)|ah (?:ok(?:ay)?|i see)|fair enough|good to know|(?:ok(?:ay)?[\s,]*)+)$", re.I)),
    (GREETING, re.compile(
        r"^(?:hi+|hello+|hey+|hiya|hi there|hello there|hey there|howdy|yo|namaste|namaskar|good (?:morning|afternoon|evening|day)|greetings|hi,? (?:how are you|good (?:morning|afternoon|evening))|"
        r"(?:hi|hello|hey)[\s,]+(?:how (?:are|r) (?:you|u)(?: (?:doing|today))?|how'?s it going)|"
        r"how (?:are|r) (?:you|u)(?: (?:doing|today))?|how'?s it going|how do you do|what'?s up|"
        r"(?:hello|hi|hey)?[\s,?]*(?:is (?:anyone|anybody|someone) (?:there|here|available|listening)|are you (?:there|here|listening|available|awake)|can you (?:hear|understand) me|do you (?:hear|understand) me|am i audible|can you hear me (?:now|ok(?:ay)?|clearly)|hello[\s,?]+(?:hello[\s,?]*)+)|"
        r"(?:hello|hi|hey)[\s,]*(?:is (?:anyone|anybody|someone) (?:there|here)|can you hear me))$", re.I)),
]
# Sentences that are only a lead-in to the greeting ("Hi, this is Meera"): not matched here, so the normal flow handles them.


def _clean(sentence: str) -> str:
    s = re.sub(r"[^a-z0-9'\s,?-]", " ", sentence.lower().replace("’", "'"))
    s = " ".join(s.split())
    return s.strip(" ,?-")


def _classify(sentence: str) -> str | None:
    s = _clean(sentence)
    if not s:
        return ""  # punctuation or nothing: neutral
    candidates = [s]
    stripped = _FILLER.sub("", s).strip(" ,?-")
    if stripped and stripped != s:
        candidates.append(stripped)
    trailed = _TRAIL.sub("", stripped or s).strip(" ,?-")
    if trailed and trailed not in candidates:
        candidates.append(trailed)
    for cand in candidates:
        for kind, pattern in _RULES:
            if pattern.match(cand):
                return kind
    return None  # filler only ("hmm", "um"): not an intent, the normal flow decides


_LEAD = re.compile(r"\b(ok(?:ay)?|alright|all right|thanks|thank you(?: very much| so much)?)\b[,.!]?\s+(?!for\b|a lot\b|so much\b|very much\b|again\b)(?=\S)", re.I)
_MAX_WORDS = 24
_PRIORITY = (GOODBYE, INTERRUPTION, THANKS, GREETING, ACK)


def detect_intent(text: str) -> str | None:
    """The intent when the WHOLE utterance is a pleasantry or a call-control phrase, else None (the normal flow decides)."""
    text = (text or "").strip()
    if not text or len(text.split()) > _MAX_WORDS:
        return None
    text = _LEAD.sub(r"\1. ", text)
    parts = [p for p in _SPLIT.split(text) if p and p.strip()]
    kinds: list[str] = []
    for part in parts:
        k = _classify(part)
        if k is None:
            return None  # a sentence with real content: not a pure pleasantry
        if k:
            kinds.append(k)
    if not kinds:
        return None
    for want in _PRIORITY:
        if want in kinds:
            return want
    return None
