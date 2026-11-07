"""Conversation-control intents: greetings, thanks, acknowledgements, goodbyes and "never mind / I'm just testing".

Pure code: no model, no database, nothing the person says can change the wording of the replies (they are constants in
`graph/nodes/replies.py`). It sits in the shared support graph, so the text chat and the voice line behave identically.

The rule that keeps it safe: an utterance counts only when EVERY sentence of it is one of these pleasantries. "Thanks, what is the fee
for PYF-101?" has a real question in it, so no intent is detected and the normal flow (triage, RAG, escalation, RBAC) runs unchanged.
The router also checks the escalation floor and the injection screen first, so "bye, someone is following me" still escalates.

Asking for a person is NOT handled here: that is the escalation floor's `human_request` rule (guardrails/escalation_floor.py).

M2.1.1 widened the phrase lists after a real call ("you can just cut the cord", "you may please end the call", "I'm actually just testing you",
"I didn't ask anything in the first place for you to say"). Call-ending commands still need a call word (call, chat, line, cord, ...) or "hang up":
"stop", "end" and "cut" on their own, or inside "stop the course fee payment", are never a command to end the call.

M2.2 added AFFIRM ("yes", "of course", "go ahead"), comma-separated pleasantries ("Hi, hello, how are you?"), hedged goodbyes ("I think that's it"),
"proceed to / feel free to" commands, a trailing "it's fine", "thanks for having me" and topic tails on corrections ("I didn't ask you regarding the
instalments"). A pending staff approval or student confirmation is resolved in graph/review.py BEFORE the graph, so "yes" only reaches here when
nothing is pending. Deliberately absent: a bare "no" and bare "stop" (the caller's barge-in).
"""

import re

GREETING, THANKS, ACK, GOODBYE, INTERRUPTION, CORRECTION, AFFIRM = ("greeting", "thanks", "acknowledgement", "goodbye", "interruption", "correction", "affirm")
INTENTS = (GREETING, THANKS, ACK, GOODBYE, INTERRUPTION, CORRECTION, AFFIRM)

_SPLIT = re.compile(r"[.!?;]+\s*|\s*,\s+(?=(?:and\s+)?(?:you|that|i|we|thank|thanks|bye|goodbye|ok|okay)\b)|\n+", re.I)
_FILLER = re.compile(r"^(?:(?:um+|uh+|hmm+|well|so|and|but|then|ok(?:ay)?|alright|right|oh|ah|please|just|actually|honestly|basically)\b[\s,]*)+", re.I)
_TRAIL = re.compile(r"[\s,]*\b(?:(?:it|that)(?:'s| is) (?:fine|file|ok(?:ay)?|good|alright|all right)|please|thanks|thank you|then|now|ok(?:ay)?|sir|ma'?am|bro)\s*$", re.I)
_HEDGE = re.compile(r"^(?:i (?:think|guess|suppose|reckon|believe)(?: so)?[\s,]+)", re.I)
_NAME = r"(?:\s+(?:there|team|support|assistant|bot|everyone|all))?"

_RULES: list[tuple[str, re.Pattern]] = [
    (GOODBYE, re.compile(
        r"^(?:no(?:pe)?[\s,]+)?(?:good\s*bye|bye(?:[\s-]*bye)?|see (?:you|ya)(?: (?:later|soon|around))?|take care|talk (?:to you )?later|have a (?:good|great|nice|lovely) (?:day|one|evening|night|time)|"
        r"that'?s (?:all|it|everything|enough|about it|all (?:i|we) (?:need|needed|want|wanted|had|have))(?: (?:for now|from me|for today|for me|then))?|that is (?:all|it|everything|enough)(?: for now)?|that will be all|that would be all|"
        r"that(?:'ll| will| would) (?:do|be enough|be everything)(?: for now)?|"
        r"(?:i'?m|i am|we'?re|we are) (?:all )?(?:done|good|finished|set|through)(?: (?:here|now|for now|for today|with (?:that|this|you)))?|all (?:good|done|set)|nothing (?:else|more)(?: (?:to (?:ask|say)|from me))?|"
        r"(?:i )?(?:have|got|don'?t have|do not have) (?:no|any) (?:more|other|further) (?:questions|queries|doubts)(?: for now)?|(?:i )?(?:have|got) nothing (?:else|more) to (?:ask|say)|"
        r"(?:let'?s|we can|we may|i think we can) (?:wrap(?: this)? up|end(?: it)? here|stop here|finish here|call it a day)|(?:we can |you can )?wrap(?: this)? up|"
        r"(?:(?:you|u)\s+)?(?:(?:can|may|could|would|should|will|might)\s+)?(?:(?:just|please|kindly|now|simply|go ahead and|proceed to|feel free to|also)\s+)*(?:please\s+)?(?:go ahead and )?(?:hang ?up|disconnect)(?: (?:the|this|our|my|that)(?: (?:call|chat|conversation|session|line|phone))?)?(?: now| here| for now| please)*|"
        r"(?:(?:you|u)\s+)?(?:(?:can|may|could|would|should|will|might)\s+)?(?:(?:i|we)\s+(?:want|would like|wanna|'d like) (?:you )?to\s+)?(?:(?:just|please|kindly|now|simply|go ahead and|proceed to|feel free to|also)\s+)*(?:please\s+)?(?:go ahead and )?(?:stop|end|finish|cut|close|drop|terminate|kill)(?: (?:off|up|out))?(?: (?:the|this|our|my|that|it))?"
        r" (?:call|chat|conversation|session|line|cord|phone|connection|call here|gore|coal|caul|cull|calls)(?: (?:now|here|for now|off|please))*|"
        r"(?:(?:let me|let'?s|lets|i (?:want|would like|wanna|need|'d like) to|i'?m going to|i will|we can|we may|you can|you may|please|kindly|just|now)\s+)+(?:end|finish|wrap up|wrap|close|conclude|terminate)(?: (?:up|off))?(?: (?:this|it|here|now|the|our|call|chat|conversation))+(?: (?:now|here|for now|please))*|"
        r"(?:(?:you|u)\s+)?(?:(?:can|may|could|would|should|will|might)\s+)?(?:(?:just|please|kindly|now|simply|go ahead and|proceed to|feel free to|also)\s+)*(?:please\s+)?(?:go ahead and )?cut (?:it|this|that)(?: (?:off|out|short))(?: now| here| for now| please)*|"
        r"(?:you|u)\s+(?:can|may|could|would)\s+(?:(?:just|please|kindly|now|simply|go ahead and|proceed to|feel free to|also)\s+)*(?:please\s+)?(?:go|leave)(?: now)?|(?:i )?(?:have to|need to|got to|gotta) (?:go|leave|run|hang up))$", re.I)),
    (THANKS, re.compile(
        r"^(?:thanks?(?: you)?(?: (?:a lot|so much|very much|a ton|again|for (?:your|the) (?:help|time|information|info|answer|support)|for calling|for that|for having me|for everything|for all (?:your|the) (?:help|time|support)|for all that|for your (?:time|help)))*|"
        r"thx|ty|cheers|many thanks|much appreciated|(?:i )?(?:really )?appreciate (?:it|that|your help|the help)|thank you (?:so|very) much|"
        r"that was (?:helpful|useful|great)|that helps|that'?s (?:helpful|useful|very helpful))$", re.I)),
    (CORRECTION, re.compile(
        r"^(?:(?:no|oh|hey|hmm|well|okay|ok)[\s,]+)*(?:"
        r"i (?:didn'?t|did not|never|haven'?t|have not|wasn'?t|was not)\s+(?:even\s+|actually\s+|really\s+)?(?:ask(?:ed|ing)?|say|said|request(?:ed)?|mean|meant)"
        r"(?:\s+(?:anything|that|this|it|you|for (?:that|this|it|anything|you to \w+(?: that| this| it)?)|you to \w+(?: that| this| it)?|in the first place|at all|for you|about (?:that|this|it)|anything))*(?:\s+(?:regarding|about|on|for|concerning|of)\s+(?:the |any |your |that |this )?[a-z]+(?: [a-z]+){0,3})?|"
        r"you (?:didn'?t|did not) (?:have|need) to (?:say|ask|tell me) (?:that|this|it|anything)(?: (?:at all|in the first place))?|"
        r"why (?:did|are|would|do) you (?:say|saying|ask|asking|mention|mentioning|bring up|bringing up|tell me|telling me) (?:that|this|it|those|them)(?: in the first place| at all)?|who asked you(?: (?:that|this))?|"
        r"that'?s not what i (?:asked|meant|said|wanted)|that was not what i (?:asked|meant|said|wanted)|that'?s not my question|that was not my question|not what i (?:asked|meant)|"
        r"you (?:misunderstood|misheard|got me wrong)(?: me)?|(?:you )?(?:are|'?re) not listening|(?:that'?s )?(?:wrong|not right)|that'?s not it|not that|"
        r"(?:no[\s,]*)+(?:that'?s )?(?:not (?:what )?(?:i (?:asked|meant|said))|wrong))$", re.I)),
    (INTERRUPTION, re.compile(
        r"^(?:(?:i(?: am|'?m)|we(?: are|'?re)|this is|it'?s|it is)\s+(?:(?:actually|really|only|just|simply|still|now|basically|also|honestly)\s+)*(?:a )?(?:test(?:ing)?|checking|trying(?: (?:it|this|you|this out|you out))?|kidding|joking)(?: (?:you|it|this|out|the (?:system|call|line|bot|assistant)|your (?:response|answer|replies|voice)))*(?: (?:out|only|for fun))?|"
        r"(?:i )?(?:was|am|'?m) (?:(?:actually|really|only|just|simply|still|now|basically|also|honestly)\s+)*(?:testing|checking|trying|playing)(?: (?:you|it|this|out|the (?:system|call|line|bot|assistant)|your (?:response|answer|replies|voice)))*(?: (?:out|only|for fun|with you))?|"
        r"(?:(?:actually|really|only|just|simply|still|now|basically|also|honestly)\s+)*(?:a )?(?:test(?:ing)?|checking|kidding|joking)|(?:this is )?(?:a )?(?:test|testing)(?: (?:one|1|two|2|three|3|call|only))*|"
        r"(?:testing[\s,]*)+(?:one|1)?[\s,]*(?:two|2)?[\s,]*(?:three|3)?|sorry|my (?:bad|mistake)|(?:i'?m|i am) sorry|excuse me|pardon(?: me)?|"
        r"never ?mind|forget (?:it|that)|(?:ignore|disregard) (?:that|it|what i said)|scratch that|"
        r"wait(?: (?:a|one) (?:second|sec|moment|minute))?|hold on(?: (?:a|one) (?:second|sec|moment|minute))?|one (?:second|sec|moment|minute)|give me (?:a|one) (?:second|sec|moment|minute))$", re.I)),
    (ACK, re.compile(
        r"^(?:ok(?:ay)?(?: (?:cool|great|fine|then|thanks?))?|alright|all right|fine|sure|got it|i (?:see|understand|got it|get it)|understood|noted|right|cool|nice|great|perfect|awesome|excellent|good|"
        r"(?:that'?s|that is|it'?s|it is) (?:fine|ok(?:ay)?|good|great|perfect|alright|all right|cool|nice|clear|understood|enough|noted)|that works(?: for me)?|makes sense|(?:no )?(?:problem|worries|issue)|no worries|no thanks?|no thank you|nope|"
        r"sounds (?:good|great|fine|right)|(?:all )?clear|mm+(?:[\s-]*hm+)?|uh[\s-]*huh|i (?:know|knew)|oh (?:ok(?:ay)?|i see|nice|great|cool)|ah (?:ok(?:ay)?|i see)|fair enough|good to know|(?:ok(?:ay)?[\s,]*)+)$", re.I)),
    (AFFIRM, re.compile(
        r"^(?:yes|yeah|yea|yep|yup|ya|aye|of course|sure thing|absolutely|definitely|certainly|go ahead|please do|do it|that'?s (?:right|correct)|that is (?:right|correct)|correct|exactly|indeed|affirmative|yes please|yes sure|yeah sure|yes of course|yeah of course|why not)$", re.I)),
    (GREETING, re.compile(
        r"^(?:hi+|hello+|hey+|hiya|hi there|hello there|hey there|howdy|yo|namaste|namaskar|good (?:morning|afternoon|evening|day)|greetings|hi,? (?:how are you|good (?:morning|afternoon|evening))|"
        r"(?:hi|hello|hey)[\s,]+(?:how (?:are|r) (?:you|u)(?: (?:doing|today))?|how'?s it going)|"
        r"how (?:are|r) (?:you|u)(?: (?:doing|today))?|how'?s it going|how do you do|what'?s up|"
        r"(?:hello|hi|hey)?[\s,?]*(?:is (?:anyone|anybody|someone) (?:there|here|available|listening)|are you (?:there|here|listening|available|awake)|can you (?:hear|understand|get|follow) me|do you (?:hear|understand|get|follow) me|(?:are|r) you (?:getting|understanding|following|hearing) me|can you (?:hear|understand) what i(?:'m| am) (?:saying|asking)|am i audible|can you hear me (?:now|ok(?:ay)?|clearly)|hello[\s,?]+(?:hello[\s,?]*)+)|"
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
    hedged = _HEDGE.sub("", stripped or s).strip(" ,?-")   # "I think that's it", "I guess we're done"
    if hedged and hedged not in candidates:
        candidates.append(hedged)
        again = _TRAIL.sub("", hedged).strip(" ,?-")
        if again and again not in candidates:
            candidates.append(again)
    for cand in candidates:
        for kind, pattern in _RULES:
            if pattern.match(cand):
                return kind
    return None  # filler only ("hmm", "um"): not an intent, the normal flow decides


_LEAD = re.compile(r"\b(ok(?:ay)?|alright|all right|thanks|thank you(?: very much| so much)?)\b[,.!]?\s+(?!for\b|a lot\b|so much\b|very much\b|again\b)(?=\S)", re.I)
_MAX_WORDS = 24
_PRIORITY = (GOODBYE, CORRECTION, INTERRUPTION, THANKS, GREETING, ACK, AFFIRM)


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
        if k is None and "," in part:  # "Hi, hello, how are you?": every comma-separated piece must be a pleasantry on its own
            subs = [_classify(x) for x in part.split(",") if x.strip()]
            if subs and None not in subs:
                kinds.extend(x for x in subs if x)
                continue
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
