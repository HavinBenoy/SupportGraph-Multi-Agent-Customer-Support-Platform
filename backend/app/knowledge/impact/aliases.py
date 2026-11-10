"""Different words for the same thing, without a fact database: aliases of a SUBJECT (ML = Machine Learning) and the several spellings of a PERSON (Jyothi sir = Jyothi = Dr. Jyothi).

Why this exists. A change names its subject once ("Machine Learning faculty ..."), while the documents say "ML sessions", "the machine-learning module" or "Jyothi handles ...". The
older checks compared single words, so a passage that said "ML" was judged to be about a different subject. Two small, deterministic mechanisms close that gap:

  * SUBJECT ALIASES. Groups of equivalent phrases come from (a) the tenant's `entity_aliases` (institute vocabulary, data not code), (b) the acronym of the subject itself
    ("machine learning" -> "ml"), and (c) the documents' own "Machine Learning (ML)" spellings. `expand()` appends the subject's canonical words to a text in which an
    alias occurs, so every existing word-based scope check works unchanged on the expanded text.
  * PERSON VALUES. "Jyothi sir", "Jyothi", "Mr. Jyothi" and the common respelling "Jyoti" are one person. `find_person()` finds all of them; a differently SPELT name is reported
    as a similar match (never as an exact one), so it can only ever lead to a question for a human.

Nothing here calls a model."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

from app.knowledge.impact.spec import ChangeSpec, words
from app.knowledge.impact.values import Span, find_value, kind_of

PREFIXES = ("dr", "mr", "mrs", "ms", "prof", "professor", "miss", "shri", "smt")
SUFFIXES = ("sir", "madam", "mam", "ma'am", "garu", "ji")
HONORIFICS = set(PREFIXES) | set(SUFFIXES)
SIMILAR = 0.84                                                           # spelling similarity that counts as "probably the same name" (Jyothi / Jyoti = 0.91)


# ----------------------------------------------------------------------------------------------------------------------------------- people
def person_core(value: str) -> list[str]:
    """The name tokens of a person value without honorifics: "Jyothi sir" -> ["jyothi"], "Dr. A. P. Rao" -> ["a", "p", "rao"] is NOT a name token set we trust (see is_person)."""
    return [t for t in (w.strip(".").lower() for w in re.split(r"\s+", (value or "").strip())) if t and t not in HONORIFICS]


def is_person(value: str | None, attribute: str = "", person_attributes: frozenset | set = frozenset()) -> bool:
    """A value that names a person: letters only, one to four name tokens, and either the attribute is about people or the value carries an honorific."""
    v = " ".join((value or "").split())
    if not v or kind_of(v) != "text" or not re.fullmatch(r"[A-Za-z][A-Za-z.'\- ]*", v):
        return False
    core = person_core(v)
    if not core or len(core) > 4 or any(len(t) < 2 for t in core):
        return False
    has_honorific = any(w.strip(".").lower() in HONORIFICS for w in v.split())
    return has_honorific or (attribute or "").lower() in person_attributes


@dataclass(frozen=True)
class PersonHit:
    span: Span
    exact: bool                                                          # False: a similar spelling, not the same letters


@lru_cache(maxsize=256)
def _person_rx(core: tuple[str, ...]) -> re.Pattern:
    name = r"\s+".join(re.escape(t) for t in core)
    pre = rf"(?:(?:{'|'.join(PREFIXES)})\.?\s+)?"
    suf = rf"(?:\s+(?:{'|'.join(re.escape(s) for s in SUFFIXES)})(?![\w']))?"
    return re.compile(rf"(?<![\w]){pre}{name}(?![\w]){suf}", re.I)


_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def find_person(text: str, value: str, *, similar: bool = True) -> list[PersonHit]:
    """Every mention of the person `value` names, with or without an honorific, plus (when `similar`) differently spelt look-alikes. Spans never overlap."""
    core = tuple(person_core(value))
    if not core:
        return []
    hits: list[PersonHit] = [PersonHit(Span(m.start(), m.end(), m.group(0)), True) for m in _person_rx(core).finditer(text)]
    if similar and all(len(t) >= 4 for t in core):
        ws = list(_WORD.finditer(text))
        n = len(core)
        for i in range(len(ws) - n + 1):
            window = ws[i:i + n]
            if any(a.start() < h.span.end and h.span.start < window[-1].end() for h in hits for a in window[:1]):
                continue
            if all(SequenceMatcher(None, w.group(0).lower(), t).ratio() >= SIMILAR for w, t in zip(window, core)) and \
               not all(w.group(0).lower() == t for w, t in zip(window, core)):
                a, b = window[0].start(), window[-1].end()
                suffix = re.match(rf"\s+(?:{'|'.join(re.escape(s) for s in SUFFIXES)})(?![\w'])", text[b:], re.I)
                hits.append(PersonHit(Span(a, b + (suffix.end() if suffix else 0), text[a:b + (suffix.end() if suffix else 0)]), False))
    hits.sort(key=lambda h: h.span.start)
    return hits


def find_old(text: str, old_value: str, attribute: str = "", person_attributes: frozenset | set = frozenset()) -> list[PersonHit]:
    """Where the OLD value is written in `text`, in whatever spelling. Amounts and times use the exact value finder; a person also matches without the honorific and (as a
    non-exact hit) under a similar spelling. Overlapping hits are merged, the longest kept."""
    spans = [PersonHit(s, True) for s in find_value(text, old_value)]
    if is_person(old_value, attribute, person_attributes):
        spans += find_person(text, old_value)
    spans.sort(key=lambda h: (h.span.start, -(h.span.end - h.span.start)))
    out: list[PersonHit] = []
    for h in spans:
        if out and h.span.start < out[-1].span.end:
            if h.span.end > out[-1].span.end and h.span.start == out[-1].span.start:
                out[-1] = h
            continue
        out.append(h)
    return out


# --------------------------------------------------------------------------------------------------------------------------------- subjects
def _tokens(phrase: str) -> tuple[str, ...]:
    return tuple(words(phrase.replace("-", " ").replace("_", " ")))


def acronym(terms: list[str]) -> str | None:
    t = [x for x in terms if x]
    return "".join(x[0] for x in t) if 2 <= len(t) <= 4 else None


_LONG_ABBR = re.compile(r"(?P<long>[A-Z][A-Za-z]+(?:\s+(?:of|and|with|for|&)?\s*[A-Z][A-Za-z]+){1,3})\s*\(\s*(?P<ab>[A-Z][A-Za-z0-9]{1,7})\s*\)")
_ABBR_LONG = re.compile(r"\b(?P<ab>[A-Z]{2,7})\s*\(\s*(?P<long>[A-Z][A-Za-z]+(?:\s+(?:of|and|with|for|&)?\s*[A-Z][A-Za-z]+){1,3})\s*\)")


def groups_from_documents(texts: list[str]) -> list[list[str]]:
    """Equivalent phrases the documents spell out themselves: "Machine Learning (ML)", "ML (Machine Learning)"."""
    out: list[list[str]] = []
    for t in texts:
        for rx in (_LONG_ABBR, _ABBR_LONG):
            for m in rx.finditer(t):
                out.append([m.group("long"), m.group("ab")])
    return out


def build_aliases(spec: ChangeSpec, config_groups: dict[str, list[str]] | None = None, doc_texts: list[str] | None = None, *,
                  with_acronym: bool = True) -> list[tuple[str, list[str]]]:
    """Alias expansions for the subject of `spec`: pairs (surface form that may appear in a text, subject words it stands for). A group of equivalent phrases applies when
    ANY of its members consists only of subject words ("machine learning" in the subject "machine learning faculty" terms [machine, learning]; "ml" in [ml])."""
    subject = set(spec.entity_terms)
    if not subject:
        return []
    groups: list[list[str]] = [[k, *v] for k, v in (config_groups or {}).items()]
    groups += groups_from_documents(doc_texts or [])
    ac = acronym(spec.entity_terms)
    if ac and with_acronym:
        groups.append([" ".join(spec.entity_terms), ac])
    pairs: dict[str, list[str]] = {}
    for g in groups:
        toks = [(m, _tokens(m)) for m in g if _tokens(m)]
        mine = [t for _, t in toks if set(t) <= subject]
        if not mine:
            continue
        stands_for = sorted(mine, key=len, reverse=True)[0]
        for form, t in toks:
            if t != stands_for and not set(t) <= subject:
                pairs[" ".join(t)] = list(stands_for)
    return sorted(pairs.items())


@lru_cache(maxsize=512)
def _form_rx(form: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + r"[\s\-_/]+".join(re.escape(t) for t in form.split()) + r"(?![a-z0-9])")


def expand(text: str, aliases: list[tuple[str, list[str]]]) -> str:
    """`text` plus, for every alias that occurs in it, the subject words it stands for. "Jyothi sir handles ML sessions" -> "... machine learning"."""
    text = text or ""
    if "-" in text:
        text = f"{text} {text.replace('-', ' ')}"                      # "machine-learning" is also "machine learning"
    if not aliases:
        return text
    low = text.lower()
    extra = [" ".join(words_) for form, words_ in aliases if _form_rx(form).search(low)]
    return f"{text} {' '.join(extra)}" if extra else text


def alias_hits(text: str, aliases: list[tuple[str, list[str]]]) -> list[str]:
    low = (text or "").lower()
    return [form for form, _ in aliases if _form_rx(form).search(low)]
