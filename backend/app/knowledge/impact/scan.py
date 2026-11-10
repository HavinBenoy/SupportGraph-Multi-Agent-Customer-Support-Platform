"""Evidence scan: find EVERY place in the published knowledge base that states what a change is about, by reading each document section by section.

Discovery used to ask "which documents look similar?" and then look for the old value in them. That misses wording the description did not use ("Jyothi sir handles ML
sessions") and it cannot tell "the faculty of Machine Learning" from "a different course Jyothi also teaches". This module reads the document itself. For every line, list item
and table row (see `sections.units`) it collects EVIDENCE, each piece recorded so a reviewer can read it:

    value    the old value in any spelling (amount formats, "Jyothi sir" / "Jyothi" / "Mr. Jyothi", a differently spelt "Jyoti" as a separate, weaker kind of match);
             or, when the change names no old value, the CURRENT value the line states for the subject (found by an attribute cue, a verb such as "handles", a table column)
    subject  the subject's words on the line itself, or in its heading / title / question above it, including aliases (ML = Machine Learning)
    cue      the attribute or role word that ties the value to the attribute ("faculty", "instructor", "handles", a table column called Faculty)
    context  historical wording, a sibling alternative (weekend vs weekday), "already the new value"

It iterates: ROUND 1 reads the description (old value, or the attribute for a change that gives no old value). When round 1 learns the old value from the documents themselves
(the value that "Machine Learning faculty is ..." lines state), ROUND 2 searches the whole base again for that learned value, which finds lines that never repeat the attribute
words. Lines that carry the value but not the subject are kept as "related, no change" evidence (the same person teaching a different course), never as edits.

Nothing here edits anything or calls a model; it decides WHERE to look and WHAT is written there. Judging and rewriting stay in evaluate.py behind the same guards."""

import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field

from app.knowledge.impact.aliases import SIMILAR, alias_hits, expand, find_old, person_core
from app.knowledge.impact.passages import Passage, plausible_slot_value, slot_passages
from app.knowledge.impact.scope import context_of, out_of_scope, scope_fraction  # noqa: F401
from app.knowledge.impact.sections import Unit, units
from app.knowledge.impact.spec import ATTRIBUTES, PERSON_ATTRS, ChangeSpec, Fact, attribute_words, words
from app.knowledge.impact.values import Span, kind_of

MAX_ROLE_HITS = 3                                                                # generic mentions of the role shown per document
_PERSON_CUE = re.compile(r"\b(?:faculty|faculties|trainers?|instructors?|teachers?|mentors?|coach(?:es)?|taught|teach(?:es|ing)?|handl(?:e|es|ed|ing)|conduct(?:s|ed|ing)?|"
                         r"lead(?:s|ing)?|in\s+charge|responsible|deliver(?:s|ed|ing)?|anchor(?:s|ed)?|instruct(?:s|ed)?)\b", re.I)
_WEAK_CUE = re.compile(r"\b(?:sessions?|classes|class|tak(?:e|es|ing)|train(?:s|ed|ing)?|cover(?:s|ed)?|module|handled)\b", re.I)
_MONEY_CUE = re.compile(r"₹|\b(?:rs|inr|rupees?)\b", re.I)
_NAME = re.compile(r"(?:(?P<pre>(?:Dr|Mr|Mrs|Ms|Prof)\.?)\s+)?(?P<n>[A-Z][a-z][A-Za-z'\-]*(?:\s+[A-Z][a-z][A-Za-z'\-]*){0,2})(?P<suf>\s+(?:sir|madam|mam|ma'am|Sir|Madam))?")
_UNIT_AFTER = re.compile(r"\s*(?:weeks?|months?|days?|hours?|years?|sessions?|classes|lectures?|modules?|batches|seats|students)\b", re.I)
_CAPWORD = re.compile(r"[A-Z][a-z][A-Za-z'\-]*")
_BEFORE = re.compile(r"(?:\b(?:is|are|was|be|by|with|under)|[:=\-–—])\s*$", re.I)
_AFTER = re.compile(r"\s*(?:also\s+|currently\s+|now\s+|will\s+)?(?:handl|teach|tak|conduct|lead|train|mentor|deliver|cover|coach|instruct|anchor|"
                    r"is\s+(?:the|our|your|responsible|in\s+charge|handling|teaching|taking|conducting)|has\s+been|\(\s*(?:faculty|trainer|instructor))", re.I)
_NOT_NAME = set("""the our this that these those all every each any students student classes class sessions session faculty faculties trainer trainers instructor instructors
teacher teachers mentor mentors coach course courses batch batches module modules online offline weekend weekday morning evening afternoon night he she they it we you for in on at by
with and or but if when who what which where how q a ans answer note sample content programme programmes program programs institute qspiders pyspiders python java data science
machine learning software testing full stack web development cloud devops aws sql selenium javascript react spring boot django flask fee fees timing timings duration schedule
one two three four five six seven eight nine ten eleven twelve mon tue wed thu fri sat sun monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september october november december
new current present previous former also please see read meet visit contact call email apply enrol enroll join admission admissions placement placements syllabus curriculum
year years month months week weeks day days hours hour live recorded demo free paid certificate certification project projects mock interview interviews""".split())


@dataclass
class Hit:
    """One place in one document that states (or may state) what the change is about, with the evidence for it."""
    doc_id: str
    passage: Passage
    fact_index: int
    fact: Fact                                   # the fact as THIS line states it (a change without an old value: old_value = this line's current value)
    how: str                                     # old | old_core | similar | slot | other | already | multi | role
    subject: str                                 # line | context | none
    round: int = 1
    cue: str = ""
    column: str = ""
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    conflict: tuple[str, ...] = ()               # the documents disagree about the current value: the distinct values they state
    shared: str = ""                             # the line also covers another subject ("Machine Learning and Statistics"): replacing the value would change both

    @property
    def strong(self) -> bool:
        return self.how in ("old", "old_core", "slot") and self.subject != "none"

    def explain(self) -> str:
        how = {"old": "the old value is written here", "old_core": "the old value is written here without/with a different title",
               "similar": "a similar spelling of the old value (not identical)", "slot": f"the current value of the attribute: {', '.join(self.values)}",
               "other": f"a value ({', '.join(self.values)}) that is neither the old nor the new one", "already": "it already says the new value",
               "multi": f"several different values on this line ({', '.join(self.values)})", "role": "the role is mentioned without naming a value"}[self.how]
        subj = {"line": "subject on the line", "context": "subject from the heading/title", "none": "subject not found here"}[self.subject]
        extra = [f"alias {a!r}" for a in self.aliases] + ([f"shared with other subjects ({self.shared!r} is listed with others)"] if self.shared else [])
        extra = extra + ([f"cue {self.cue!r}"] if self.cue else []) + ([f"table column {self.column!r}"] if self.column else [])
        return "; ".join([how, subj, *extra]) + (f" (found in round {self.round})" if self.round > 1 else "")


@dataclass
class ScanReport:
    hits: dict[str, list[Hit]] = field(default_factory=dict)
    rounds: list[dict] = field(default_factory=list)
    learned: list[dict] = field(default_factory=list)


def _norm(x: str) -> str:
    return " ".join((x or "").lower().split())


def _same_person(a: str, b: str) -> bool:
    ca, cb = person_core(a), person_core(b)
    return bool(ca) and ca == cb or _norm(a) == _norm(b)


def _cue_of(attr: str, text: str, strong_only: bool) -> str:
    rx = _PERSON_CUE if attr in PERSON_ATTRS else re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(attribute_words(attr))) + r")\b", re.I)
    m = rx.search(text)
    if m:
        return m.group(0).lower()
    if attr not in PERSON_ATTRS and attr in {"fee", "fees", "price", "prices", "cost", "costs", "tuition", "charge", "charges", "amount"} and _MONEY_CUE.search(text):
        return "₹"
    if not strong_only and attr in PERSON_ATTRS:
        w = _WEAK_CUE.search(text)
        return w.group(0).lower() if w else ""
    return ""


def _exclude(spec: ChangeSpec, doc, u: Unit) -> set[str]:
    ex = set(words(f"{doc.title} {doc.path} {u.heading}")) | set(spec.entity_terms) | ATTRIBUTES | _NOT_NAME
    for form, target in spec.alias_pairs:
        ex |= set(words(form)) | set(target)
    return ex


def _names(u: Unit, exclude: set[str]) -> list[tuple[Span, bool]]:
    """Person-name expressions in a unit, with whether they are tied to a role by an honorific, a preceding "is/by/:" or a following verb."""
    out: list[tuple[Span, bool]] = []
    for m in _NAME.finditer(u.text):
        toks = [(t.start() + m.start("n"), t.end() + m.start("n")) for t in _CAPWORD.finditer(m.group("n"))]
        kept = [t for t in toks if u.text[t[0]:t[1]].lower() not in exclude]
        if not kept:
            continue
        a, b = kept[0][0], kept[-1][1]
        if any(u.text[x:y].lower() in exclude for x, y in toks[toks.index(kept[0]):toks.index(kept[-1]) + 1]):
            continue
        pre = m.group("pre") if m.group("pre") and a == m.start("n") else None
        suf = m.group("suf") if m.group("suf") and b == m.end("n") else None
        start = m.start("pre") if pre else a
        end = m.end("suf") if suf else b
        before, after = u.text[max(0, start - 30):start], u.text[end:end + 60]
        if _UNIT_AFTER.match(after):
            continue                                                          # "Six weeks": a quantity, not a person
        tied = bool(pre or suf or _BEFORE.search(before) or _AFTER.match(after))
        out.append((Span(u.start + start, u.start + end, u.text[start:end]), tied))
    return out


def _current_values(doc, u: Unit, fact: Fact, spec: ChangeSpec, slot_lines: dict, attr: str, role_in_scope: bool) -> tuple[list[Span], str]:
    """The value(s) a unit states today for the change's attribute, and the table column they came from. Only values tied to the attribute count."""
    found: list[Span] = []
    column = ""
    if u.start in slot_lines:
        found += [s for s in slot_lines[u.start][0].matches]
    if u.cells and u.columns:
        want = attribute_words(attr)
        for i, c in enumerate(u.cells):
            if i < len(u.columns) and want & set(words(u.columns[i])) and plausible_slot_value(fact, c.text) and (attr not in PERSON_ATTRS or c.text[:1].isupper()):
                found.append(Span(c.start, c.end, c.text))
                column = u.columns[i]
    if attr in PERSON_ATTRS and role_in_scope:
        for span, tied in _names(u, _exclude(spec, doc, u)):
            if tied and plausible_slot_value(fact, span.text) and not any(f.start <= span.start and span.end <= f.end for f in found):
                found.append(span)
    found = [s for s in found if plausible_slot_value(fact, s.text)]
    found.sort(key=lambda s: (s.start, -(s.end - s.start)))
    out: list[Span] = []
    for s in found:
        if not out or s.start >= out[-1].end:
            out.append(s)
    return out, column


_LISTED = r"(?:\s*(?:&|/|\+|\band\b|\bor\b)\s*[A-Za-z]|\s*,\s*[A-Z][a-z])"


def _shared_subject(spec: ChangeSpec, u: Unit) -> str:
    """The subject written as one item of a list on a line ("Statistics and Machine Learning", "Machine Learning, Statistics"): a value on such a line belongs to ALL the items."""
    forms = [" ".join(spec.entity_terms)] + [f for f, _ in spec.alias_pairs]
    for f in forms:
        if not f:
            continue
        body = r"[\s\-_]+".join(re.escape(t) for t in f.split())
        if re.search(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9]){_LISTED}", u.text, re.I) or re.search(rf"[A-Za-z]\s*(?:&|/|\+|\band\b|\bor\b|,)\s*{body}(?![A-Za-z0-9])", u.text, re.I):
            return f
    return ""


def _subject(spec: ChangeSpec, doc, u: Unit) -> tuple[str, tuple[str, ...]]:
    own = f"{u.text} {u.lead}"
    used = tuple(alias_hits(own, spec.alias_pairs))
    if not spec.entity_terms:
        return "line", used
    if not out_of_scope(spec, own) and scope_fraction(spec.entity_terms, expand(own, spec.alias_pairs)) >= 0.99:
        return "line", used
    if not out_of_scope(spec, f"{doc.title} {doc.path} {doc.category} {u.context_text} {own}"):
        return "context", tuple(alias_hits(f"{u.context_text} {own}", spec.alias_pairs))
    return "none", ()


def scan_doc(doc, spec: ChangeSpec, fi: int, fact: Fact, *, rnd: int = 1) -> list[Hit]:
    attr = (fact.attribute or "").lower()
    slot_lines = {p.start: (p, r) for p, r in slot_passages(doc.content, fact)} if not fact.old_value else {}
    out: list[Hit] = []
    roles = 0
    for u in units(doc.content):
        subj, used = _subject(spec, doc, u)
        text_cue = _cue_of(attr, f"{u.text} {u.context_text}", strong_only=False)
        spans: list[Span] = []
        how, resolved, column = None, fact, ""
        if fact.old_value:
            hits = find_old(u.text, fact.old_value, attr, PERSON_ATTRS)
            if hits:
                spans = [Span(u.start + h.span.start, u.start + h.span.end, h.span.text) for h in hits]
                if not all(h.exact for h in hits):
                    how = "similar"
                else:
                    how = "old" if all(_norm(s.text) == _norm(fact.old_value) for s in spans) else "old_core"
        if how is None:
            cur, column = _current_values(doc, u, fact, spec, slot_lines, attr, role_in_scope=subj != "none" and bool(text_cue))
            if cur and subj != "none":
                vals = tuple(dict.fromkeys(s.text.strip() for s in cur))
                distinct = {tuple(person_core(v)) or (_norm(v),) for v in vals}
                if fact.new_value and all(_same_person(v, fact.new_value) for v in vals):
                    how, spans = "already", cur
                elif len(distinct) > 1:
                    how, spans = "multi", cur
                elif fact.old_value:
                    how, spans = "other", cur
                else:
                    how, spans, resolved = "slot", cur, fact.model_copy(update={"old_value": vals[0]})
                if how in ("already", "multi", "other"):
                    resolved = fact.model_copy(update={"old_value": vals[0]}) if how != "multi" else fact
        if how is None:
            line_cue = _cue_of(attr, f"{u.text} {' '.join(u.columns)}", strong_only=True)
            if subj == "line" and line_cue and roles < MAX_ROLE_HITS:
                roles += 1
                out.append(Hit(doc.doc_id, Passage(u.start, u.end, u.text, u.heading, (), u.lead), fi, fact, "role", subj, rnd, line_cue, "", used))
            continue
        shared = _shared_subject(spec, u) if (how in ("old", "old_core", "slot", "similar") and subj != "none") else ""
        out.append(Hit(doc.doc_id, Passage(u.start, u.end, u.text, u.heading, tuple(spans), u.lead), fi, resolved, how, subj, rnd, text_cue, column, used,
                       tuple(dict.fromkeys(s.text.strip() for s in spans)), shared=shared))
    return out


def _sim_core(a: tuple, b: tuple) -> bool:
    return a == b or SequenceMatcher(None, " ".join(a), " ".join(b)).ratio() >= SIMILAR


def _value_groups(rep: ScanReport, fi: int) -> list[dict]:
    """The distinct current values the documents state for one fact that names no old value, most frequent first. Spellings of one person (Jyothi / Jyoti) form one group."""
    counts: dict[str, int] = {}
    for hs in rep.hits.values():
        for h in hs:
            if h.fact_index == fi and h.how == "slot" and h.subject == "line" and h.fact.old_value:
                counts[h.fact.old_value] = counts.get(h.fact.old_value, 0) + 1
    groups: list[dict] = []
    for value, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        core = tuple(person_core(value)) or (_norm(value),)
        for g in groups:
            if _sim_core(g["core"], core):
                g["count"] += n
                g["variants"].append(value)
                break
        else:
            groups.append({"core": core, "value": value, "count": n, "variants": [value]})
    return groups


def _settle_values(rep: ScanReport, facts: list[Fact]) -> list[dict]:
    """Decide what "the old value" is for each change that gave none. A clear majority among the documents' current values IS the old value (the others are reported as
    different values for the same subject, and a respelling as a similar name). Without a clear majority nothing may be called the old value: every such line is marked as
    conflicting, so a person decides. Returns the values to search the whole base for in the next round."""
    learned: list[dict] = []
    for fi, f in enumerate(facts):
        if f.old_value:
            continue
        groups = _value_groups(rep, fi)
        if not groups:
            continue
        top, rest = groups[0], sum(g["count"] for g in groups[1:])
        clear = len(groups) == 1 or (top["count"] >= 2 and top["count"] >= 2 * rest)
        others = [g["value"] for g in groups[1:]]
        for hs in rep.hits.values():
            for h in hs:
                if h.fact_index != fi or h.how != "slot" or not h.fact.old_value:
                    continue
                core = tuple(person_core(h.fact.old_value)) or (_norm(h.fact.old_value),)
                if clear:
                    if _sim_core(top["core"], core):
                        if core != top["core"]:
                            h.how = "similar"                             # the same person spelt differently
                    else:
                        h.how = "other"                                   # a different value for the same subject
                else:
                    h.conflict = tuple(f"{g['value']} (x{g['count']})" for g in groups)
        for g in (groups[:1] if clear else [g for g in groups if g["count"] >= 2][:3]):
            learned.append({"fact": fi, "attribute": f.attribute, "value": g["value"], "count": g["count"], "other_values": others if clear else []})
    return learned


def scan_corpus(corpus: list, spec: ChangeSpec, *, max_rounds: int = 2) -> ScanReport:
    """Round 1 reads the description; round 2 (when round 1 learned the old value from the documents) searches the whole base for that value."""
    rep = ScanReport()
    facts = spec.actionable()
    seen: set[tuple[str, int, int]] = set()

    def add(hits: list[Hit], tag: str, rnd: int) -> int:
        fresh = [h for h in hits if (h.doc_id, h.passage.start, h.fact_index) not in seen]
        for h in fresh:
            seen.add((h.doc_id, h.passage.start, h.fact_index))
            rep.hits.setdefault(h.doc_id, []).append(h)
        rep.rounds.append({"round": rnd, "signal": tag, "hits": len(fresh), "documents": len({h.doc_id for h in fresh})})
        return len(fresh)

    add([h for c in corpus for fi, f in enumerate(facts) for h in scan_doc(c, spec, fi, f, rnd=1)], "the description (old value, or the attribute and subject)", 1)
    rep.learned = _settle_values(rep, facts)
    if max_rounds >= 2 and rep.learned:
        extra: list[Hit] = []
        for e in rep.learned[:3]:
            f2 = facts[e["fact"]].model_copy(update={"old_value": e["value"]})
            extra += [h for c in corpus for h in scan_doc(c, spec, e["fact"], f2, rnd=2)]
        add(extra, f"the value the documents state today ({', '.join(e['value'] for e in rep.learned[:3])})", 2)
    return rep
