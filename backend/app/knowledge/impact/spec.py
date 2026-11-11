"""The structured form of a described change (what changed, from what, to what, for which entities), plus a deterministic parser.

`ChangeSpec` is the schema a model must fill (validated by pydantic); `parse_rules` is the offline parser used by the rule model and as the safety net for the real
model: if the model's answer does not validate, or has no old/new value, the rules get a try before staff are asked to rephrase."""

import re
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.knowledge.impact.values import _MONEY, _TIME, kind_of

MAX_FACTS = 5

_STOP = set("""a an the of in on at to for from and or is are was were be been being has have had will would shall should can could now then with by as it its this that these
those wherever where when whenever applicable relevant update updated updating change changed changes changing institutional institute knowledge base please new old
starting start starts effective beginning begin onwards onward also all any every each other others related documents document files file information info details
detail replace replaced replacing replacement swap swapped instead across mention mentions mentioned reflect reflects reflected make sure ensure need needs needed from about into over under per such so than""".split())
ATTRIBUTES = set("""fee fees price prices pricing cost costs charge charges tuition amount timing timings time times schedule schedules duration durations faculty trainer trainers
instructor instructors teacher teachers mentor mentors batch batches course courses class classes session sessions policy policies syllabus curriculum discount discounts
installment installments instalment instalments rupees rupee rs inr program programme programs programmes""".split())
_MONTHS = set("january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec".split())
_DATE = (r"(?:[A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Z][a-z]+(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2}|"
         r"\d{1,2}/\d{1,2}/\d{2,4})")
_RELATIVE = re.compile(r"\b(?:starting|effective|beginning|w\.?e\.?f\.?|from|as of|with effect from|onwards? from)\s+(?P<r>today|tomorrow|now|immediately)\b(?:\s+on(?:wards?)?\b)?|\b(?P<r2>immediately)\b", re.I)
_EFFECTIVE = re.compile(rf"\b(?:starting|effective|beginning|w\.?e\.?f\.?|from|with effect from|onwards? from)\s+(?:on\s+)?(?P<d>{_DATE})")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*|\d+")


class Fact(BaseModel):
    model_config = ConfigDict(extra="ignore")
    attribute: str = Field(default="", max_length=60)
    old_value: str | None = Field(default=None, max_length=200)
    new_value: str | None = Field(default=None, max_length=200)
    effective: str | None = Field(default=None, max_length=60)
    conditions: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("old_value", "new_value", "effective", mode="before")
    @classmethod
    def _clean(cls, v):
        if v is None:
            return None
        t = " ".join(str(v).split()).strip(" .,;:\"'")
        return t or None


# Attributes whose value is a person: a slot value must look like a name.
PERSON_ATTRS = {"faculty", "trainer", "trainers", "instructor", "instructors", "teacher", "teachers", "mentor", "mentors"}
ATTRIBUTE_GROUPS = (PERSON_ATTRS, {"fee", "fees", "price", "prices", "pricing", "cost", "costs", "charge", "charges", "tuition", "amount"},
                    {"timing", "timings", "time", "times", "schedule", "schedules"}, {"duration", "durations"})


def attribute_words(attribute: str) -> set[str]:
    """The attribute and its synonyms (faculty = trainer = instructor ...)."""
    a = (attribute or "").lower().strip()
    for g in ATTRIBUTE_GROUPS:
        if a in g:
            return set(g)
    return {a} if a else set()


class Question(BaseModel):
    id: str = Field(max_length=24)
    doc_id: str | None = Field(default=None, max_length=120)
    text: str = Field(max_length=300)
    options: list[str] = Field(default_factory=list, max_length=12)


class ChangeSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = Field(default="", max_length=600)
    facts: list[Fact] = Field(default_factory=list, max_length=MAX_FACTS)
    entity_terms: list[str] = Field(default_factory=list, max_length=12)
    key_terms: list[str] = Field(default_factory=list, max_length=12)      # the entity terms that tell this subject apart from its neighbours (set by discovery, never by a model)
    alias_pairs: list[tuple[str, list[str]]] = Field(default_factory=list, max_length=24)   # (surface form, subject words it stands for): "ml" -> machine learning (set by discovery, never by a model)
    wants_all: bool = False                                  # the description says "all" / "every" / "both": no need to ask which one
    general: bool = False                                    # a change that is not a value replacement (add / remove / rewrite ...): the model reads the sections and drafts the revision

    @field_validator("entity_terms", mode="before")
    @classmethod
    def _terms(cls, v):
        """Entity terms are SINGLE words. A model often answers with phrases ("machine learning faculty"); a phrase can never equal a word of a document, so the scope
        check would reject every passage. Split phrases into words and drop the generic and attribute words."""
        return entity_terms_from(" ".join(str(x) for x in (v or [])))

    @model_validator(mode="after")
    def _subject_not_value(self):
        """The words of the NEW (or old) value name the answer, not the subject: "santhosh sir" is not part of what the change is about."""
        value_words = {w for f in self.facts for v in (f.new_value, f.old_value) if v for w in words(v)}
        if value_words and any(not f.old_value for f in self.facts) or any(f.old_value and kind_of(f.old_value) == "text" for f in self.facts):
            self.entity_terms = [t for t in self.entity_terms if t not in value_words]
        return self

    def actionable(self) -> list[Fact]:
        """Facts the analysis can act on: old and new value known, OR (a slot change) only the new value known together with the attribute and the subject
        ("the machine learning faculty is now X": the old value is whatever the document says today)."""
        return [f for f in self.facts if f.new_value and (f.old_value or ((f.attribute or "").lower() in ATTRIBUTES and self.entity_terms))]


def words(text: str) -> list[str]:
    return [w.lower().strip(".-") for w in _WORD.findall(text or "") if w.strip(".-")]


_TITLE_WORDS = {"sir", "madam", "mam", "garu", "ji", "mrs", "miss", "prof", "professor", "shri", "smt"}      # a title names no one: "Ramesh sir" must not match "Santhosh sir"


def entity_terms_from(text: str) -> list[str]:
    """The words that NAME what the change is about (course, batch type, person), with generic words and attribute words (fee, timing ...) removed."""
    out = []
    for w in words(text):
        if w in _STOP or w in _TITLE_WORDS or w in ATTRIBUTES or w in _MONTHS or w.isdigit() or len(w) < 2 or (len(w) == 2 and w not in {"ai", "ml", "bi", "qa", "ui", "ux", "hr"}):
            continue
        out.append(w)
    return list(dict.fromkeys(out))[:12]


_GENERIC = set("course courses batch batches class classes session sessions program programme programs programmes".split())


def attribute_terms_from(text: str) -> list[str]:
    """Attribute words (fee, timing, faculty ...), the specific ones first."""
    found = list(dict.fromkeys(w for w in words(text) if w in ATTRIBUTES))
    return sorted(found, key=lambda w: w in _GENERIC)


def _tail_effective(text: str, today: date | None = None) -> tuple[str | None, str]:
    """The effective date and the text without it. "from today"/"now"/"immediately" mean "the current fact": no date is written into the document. "tomorrow" is turned
    into the calendar date, because a relative word stops being true the day after it is written."""
    m = _RELATIVE.search(text)
    if m:
        word = (m.group("r") or m.group("r2") or "").lower()
        rest = text[:m.start()] + " " + text[m.end():]
        if word == "tomorrow":
            d = (today or date.today()) + timedelta(days=1)
            return f"{d.day} {d.strftime('%B %Y')}", rest
        return None, rest
    m = _EFFECTIVE.search(text)
    return (m.group("d").strip(), text[:m.start()] + " " + text[m.end():]) if m else (None, text)


def _pair_values(text: str) -> tuple[str, str] | None:
    """Two values of the same kind in the text (two amounts, or two time ranges): the first is the old one, the second the new one."""
    if len(re.findall(r"\b(?:to|is|are|as|be|at|now)\s+(?:rs\.?\s*|₹\s*|inr\s*)?\d", text, re.I)) >= 2:
        return None                         # "X to 20000 and Y to 12000": two NEW values of two intents, never "20000 -> 12000"
    for rx, ok in ((_MONEY, lambda m: m.group("cur") or m.group("k") or len(m.group("num").replace(",", "")) >= 4), (_TIME, lambda m: True)):
        found = [m.group(0).strip() for m in rx.finditer(text) if ok(m)]
        found = [f for f in found if kind_of(f) != "text"]
        if len(found) >= 2 and found[0] != found[1]:
            return found[0], found[1]
    return None


_FROM_TO = re.compile(r"\bfrom\s+(?P<old>.+?)\s+to\s+(?P<new>.+?)(?=\s+(?:starting|effective|beginning|w\.?e\.?f|for|as|across)\b|[.;\n]|$)", re.I)
_REPLACE = re.compile(r"\b(?:replace[sd]?|replacing|swap(?:ped)?)\s+(?P<old>.+?)\s+(?:with|by)\s+(?P<new>.+?)(?=\s+(?:starting|effective|beginning|for|as|across)\b|[.;\n]|$)", re.I)
_TITLES = re.compile(r"\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St)\.")
_NAME = r"(?:(?:Dr|Mr|Mrs|Ms|Prof)\u2024?\s+)?[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,2}"
_REPLACED_BY = re.compile(rf"(?P<old>{_NAME})\s+(?:is\s+|was\s+|has\s+been\s+|will\s+be\s+|being\s+)?replaced\s+by\s+(?P<new>{_NAME})")
_CHANGED_TO = re.compile(r"\b(?P<old>[^.;\n]+?)\s+(?:→|->)\s+(?P<new>[^.;\n]+?)(?=[.;\n]|$)")


_VERB = r"(?:changed|updated|revised|replaced|moved|switched|set|modified|shifted|reassigned|assigned)"
_SLOT_PATTERNS = [re.compile(p, re.I) for p in (
    rf"^(?P<subj>.+?)\s+(?:is|are|was|will be|has been|have been|should be|to be|got)\s+(?:now\s+)?{_VERB}\s+(?:to|as|with|by)\s+(?P<new>.+?)\s*$",
    rf"^(?P<subj>.+?)\s+{_VERB}\s+(?:to|as)\s+(?P<new>.+?)\s*$",
    rf"^(?:please\s+)?(?:change|update|set|revise|modify|reassign)\s+(?:the\s+)?(?P<subj>.+?)\s+(?:to|as)\s+(?P<new>.+?)\s*$",
    r"^(?P<subj>.+?)\s+(?:is|are|will be)\s+now\s+(?P<new>.+?)\s*$",
    r"^(?:the\s+)?new\s+(?P<subj>.+?)\s+(?:is|will be)\s+(?P<new>.+?)\s*$",
    # a plain statement in a change description ("Machine Learning faculty is Santhosh sir"): last, and only for a subject that carries an attribute word (checked by the caller)
    r"^(?P<subj>.+?)\s+(?:is|are|will be)\s+(?!(?:being|going|not|no|the same|still|also|to\b|changed|updated)\b)(?P<new>[^,;]{1,60})\s*$",
)]
_INSTEAD = re.compile(r"^(?P<head>.+?)[,\s]+(?:instead of|in place of|rather than|replacing|and not)\s+(?P<old>.+?)\s*$", re.I)
_LEAD = re.compile(r"^\s*(?:onwards?|henceforth|going forward|from now on)\b[,\s]*", re.I)


def _slot_change(rest: str) -> tuple[str, str, str] | None:
    """"The machine learning faculty is changed to santhosh sir": the subject, its attribute and the NEW value. There is no old value in such a sentence: it is whatever
    the document currently says. Needs a known attribute word in the subject, otherwise the analysis would have nothing to look for."""
    text = rest.strip(" .;,")
    for rx in _SLOT_PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        subj, new = m.group("subj").strip(" ,"), m.group("new").strip(" .,;\"'")
        attrs = attribute_terms_from(subj)
        if subj and new and attrs:
            return subj, attrs[0], new
    return None


def _same_head(slot: tuple[str, str, str]) -> tuple[str, str, str] | None:
    subj, _, new = slot
    a = re.sub(r"^(?:the|all|our)\s+", "", subj.strip(), flags=re.I).split()
    b = new.split()
    if len(a) >= 2 and len(b) >= 1 and a[-1].lower().rstrip("s") == b[-1].lower().rstrip("s") and " ".join(a).lower() != new.lower() and len(a) <= 4:
        return " ".join(a), new, a[-1]
    return None


def parse_rules(description: str, today: date | None = None) -> ChangeSpec:
    """Deterministic parse of "X changed from A to B (starting D)". Finds no facts (and the caller asks staff to rephrase) when it cannot tell old from new."""
    text = " ".join((description or "").split())
    protected = _TITLES.sub(lambda m: m.group(1) + "\u2024", text)                     # "Dr. Mehta": that dot does not end the sentence
    eff, rest = _tail_effective(protected, today)
    rest = _LEAD.sub("", rest.strip()).strip(" ,")
    ins = _INSTEAD.match(rest.replace("\u2024", "."))
    if ins and not _pair_values(rest):
        sl = _slot_change(ins.group("head"))
        old_i = ins.group("old").strip(" .,;\"'")
        if sl and old_i and sl[2].lower() != old_i.lower():
            subj, attr, new_v = sl
            return ChangeSpec(summary=text[:300], facts=[Fact(attribute=attr, old_value=old_i.replace("\u2024", "."), new_value=new_v.replace("\u2024", "."), effective=eff)],
                              entity_terms=entity_terms_from(subj.replace("\u2024", ".")), wants_all=bool(re.search(r"\b(all|every|both|each|everywhere)\b", text, re.I)))
    pair = _pair_values(rest)
    old = new = None
    if pair:
        old, new = pair
    else:
        for rx in (_FROM_TO, _REPLACE, _REPLACED_BY, _CHANGED_TO):
            m = rx.search(rest)
            if m:
                old, new = m.group("old").strip(" ,"), m.group("new").strip(" ,")
                break
    old, new = (x.replace("\u2024", ".") if x else x for x in (old, new))
    facts: list[Fact] = []
    slot = None
    if old and new and old.lower() != new.lower():
        facts.append(Fact(attribute=(attribute_terms_from(rest) or [""])[0], old_value=old, new_value=new, effective=eff))
    elif not (old and new):
        slot = _slot_change(rest.replace("\u2024", "."))
        pair2 = _same_head(slot) if slot else None
        if pair2:
            # "change online classes to hybrid classes": both sides end in the same noun, so the left side is the OLD value, not a subject (it was read as subject "online")
            o2, n2, head = pair2
            facts.append(Fact(attribute=head, old_value=o2, new_value=n2, effective=eff))
            return ChangeSpec(summary=text[:300], facts=facts, entity_terms=[], wants_all=bool(re.search(r"\b(all|every|both|each|everywhere)\b", text, re.I)))
        if slot:
            subj, attr, new_v = slot
            facts.append(Fact(attribute=attr, old_value=None, new_value=new_v.replace("\u2024", "."), effective=eff))
            return ChangeSpec(summary=text[:300], facts=facts, entity_terms=entity_terms_from(subj.replace("\u2024", ".")), wants_all=bool(re.search(r"\b(all|every|both|each|everywhere)\b", text, re.I)))
    scoped = rest
    scoped = scoped.replace("\u2024", ".")
    for v in (old, new):
        if v:
            scoped = scoped.replace(v, " ")
    return ChangeSpec(summary=text[:300], facts=facts, entity_terms=entity_terms_from(scoped), wants_all=bool(re.search(r"\b(all|every|both|each|everywhere)\b", text, re.I)))


_HAS_VALUE = re.compile(r"\d|\b(?:to|as|now|into|instead|no longer)\b", re.I)
_ACTION_START = re.compile(r"^(?:please\s+)?(?:add|remove|delete|drop|change|update|replace|set|mention|include)\b", re.I)
_ANAPHOR = re.compile(r"^\s*(?:and\s+)?(?:that|those|the same|the one|it)\s+(?=for|of|in|at)\b", re.I)
_CLAUSE_SPLIT = re.compile(r"\s*(?:;|\.\s+(?=[A-Za-z])|,?\s+and\s+also\s+|,\s+also\s+|,?\s+and\s+(?=(?:that|those|the|for|in|of|add|remove|delete|drop|change|update|replace|set|mention|include|[A-Z0-9])\b)|,?\s+while\s+|,?\s+whereas\s+)\s*")


_FINE_SPLIT = re.compile(r"\s*(?:;|\.\s+(?=[A-Za-z])|,?\s+and\s+also\s+|,\s+also\s+|,?\s+while\s+|,?\s+whereas\s+|,?\s+and\s+|,\s+)\s*", re.I)
_CONTENT = re.compile(r"\b(?!(?:rs|rupees?|inr|and|or|the|a|an|to|is|are|now)\b)[A-Za-z]{3,}\b", re.I)
_LEAD_VERB = re.compile(r"^(?:please\s+)?(change|update|set|make|revise|modify|increase|reduce|correct)\b", re.I)


def split_intents(description: str) -> list[str]:
    """Independent change intents in one request.
        "The fees for gen ai is 20000 rs and that for machine learning is 12000 rs"  -> ["The fees for gen ai is 20000 rs", "fees for machine learning is 12000 rs"]
        "change the gen ai course fees to 20000 rupees and machine learning course fees to 12000 rupees"
                                                                                 -> ["change the gen ai course fees to 20000 rupees", "change machine learning course fees to 12000 rupees"]
    The text is cut at every clause boundary ("and", ",", ";", ".", "also", "while"); a piece that states no value / target / action of its own is glued back to the piece after it
    ("Python and Java fees are now 9000" stays ONE intent). A piece that refers back ("that for ...") borrows the first piece's attribute words, and a piece without a verb borrows
    the first piece's verb ("change"), so every intent can be read on its own. Returns [description] when there is nothing to split."""
    text = " ".join((description or "").split())
    protected = _TITLES.sub(lambda m: m.group(1) + "\u2024", text)
    # split keeping each separator, so a piece glued back gets its ORIGINAL separator ("From today onwards, Machine Learning ..." stays exactly that)
    bits = re.split(f"({_FINE_SPLIT.pattern})", protected, flags=re.I)
    pieces: list[tuple[str, str]] = []                                    # (separator before the piece, piece)
    sep = ""
    for k, bit in enumerate(bits):
        if k % 2 == 1:
            sep = bit
        elif bit.strip(" ,."):
            pieces.append((sep, bit.strip()))
            sep = ""
    parts: list[str] = []
    carry = ""
    for sep, p in pieces:
        if parts and not carry and not _CONTENT.search(re.sub(r"\d[\d,.]*", " ", p)):
            parts[-1] = parts[-1] + sep + p                  # a bare value ("8000") belongs to the list before it ("... are now 9000, 8000 and 7000")
            continue
        p = (carry + sep + p) if carry else p
        if _HAS_VALUE.search(p) or _ACTION_START.match(p):
            parts.append(p.strip(" ,."))
            carry = ""
        else:
            carry = p
    if carry:
        if parts:
            parts[-1] = parts[-1] + " " + carry
        else:
            parts.append(carry)
    if len(parts) < 2:
        return [text]
    out: list[str] = []
    for p in parts:
        p = p.replace("\u2024", ".")
        if out and _ANAPHOR.match(p):
            attr = attribute_terms_from(out[0])
            if attr:
                p = _ANAPHOR.sub(attr[0] + " ", p, count=1)
        lead = _LEAD_VERB.match(out[0]) if out else None
        if out and lead and not _LEAD_VERB.match(p) and not _ACTION_START.match(p) and re.search(r"\bto\b", p):
            p = f"{lead.group(1)} {p}"
        out.append(p)
    return out[:6]


def spec_from_edit(title: str, path: str, diff_body: list[dict], description: str | None = None) -> ChangeSpec:
    """Workflow A: derive the change from the staff member's own manual edit (the delete/insert pairs of the structured diff), with the document's title and
    folder as the entities it is about. A description, if given, adds to it."""
    facts: list[Fact] = []
    segs = [s for s in diff_body if s["op"] != "equal" or s["text"].strip()]
    i = 0
    while i < len(segs) and len(facts) < MAX_FACTS:
        s = segs[i]
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if s["op"] == "delete" and nxt and nxt["op"] == "insert":
            o, n = " ".join(s["text"].split()), " ".join(nxt["text"].split())
            if o and n and len(o) <= 200 and len(n) <= 200 and o.lower() != n.lower() and not any(f.old_value == o and f.new_value == n for f in facts):
                facts.append(Fact(attribute=(attribute_terms_from(title) or [""])[0], old_value=o, new_value=n))
            i += 2
        else:
            i += 1
    base = parse_rules(description) if description else ChangeSpec()
    if not facts:
        facts = base.facts
    terms = list(dict.fromkeys(entity_terms_from(f"{title} {path}") + base.entity_terms))[:12]
    return ChangeSpec(summary=(description or f"Change to {title}")[:300], facts=facts, entity_terms=terms, wants_all=base.wants_all)


# Alternatives of ONE thing that a description often fails to tell apart (several weekend batches, morning vs evening, online vs classroom ...). Two passages or documents
# that carry different members of the same set are SIBLINGS; the analysis asks which one rather than guessing. The list is deliberate, small and deterministic.
SIBLING_SETS = (
    {"weekend", "weekday"}, {"morning", "afternoon", "evening", "night"}, {"online", "offline", "classroom", "hybrid"}, {"regular", "fasttrack", "crash"},
    {"beginner", "intermediate", "advanced"}, {"fulltime", "parttime"},
)
_SERIAL = re.compile(r"\b(batch|section|group|cohort|campus|branch|slot|shift)\s+([a-z]|\d{1,2})\b", re.I)


def sibling_marks(text: str) -> set[tuple[str, str]]:
    t = (text or "").lower()
    ws = set(words(t.replace("fast-track", "fasttrack").replace("fast track", "fasttrack").replace("full-time", "fulltime").replace("part-time", "parttime")
                   .replace("full time", "fulltime").replace("part time", "parttime")))
    out: set[tuple[str, str]] = {(f"set{i}", w) for i, grp in enumerate(SIBLING_SETS) for w in ws if w in grp}
    out |= {(f"serial:{m.group(1).lower()}", m.group(2).lower()) for m in _SERIAL.finditer(t)}
    return out


def are_siblings(a: str, b: str) -> bool:
    ma, mb = sibling_marks(a), sibling_marks(b)
    return any(ka == kb and va != vb for ka, va in ma for kb, vb in mb)
