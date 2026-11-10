"""The section editor's model side: build the adaptive sections, ask the model for the revised section, validate it (edit.py), optionally ask a second short question, and report
per candidate line what the model decided. (Module name kept from the earlier value-only "section judge", which this replaces.)

  1. `context.sections_for` cuts the COMPLETE logical section (whole document / sub-tree / section / blocks) that fits the configured budget;
  2. `ask` sends the generalized instruction and the section as text (thinking ON) and extracts the LAST complete fenced block; a cut-off, empty, missing or prompt-echoing answer is a
     failed response (`Extraction.state != "ok"`), never "unchanged";
  3. `edit.revise` compares the revised section with the original line by line and keeps only the changes that pass the deterministic checks (hunks);
  4. `semantic` (optional, `kb_ai_semantic_check`) asks a short second question: does the revision do what was requested and nothing else? A FAIL/UNSURE never silently removes or
     approves the edit: it flags it for a person;
  5. the hunks and the per-line `Judgement`s go back to `evaluate.LlmImpactModel`, which combines them with the rule engine's evidence.
The model alone can only PROPOSE (potential). Nothing it returns is executed, stored as an edit without these checks, or published without a human."""

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date

from app.config import Settings
from app.knowledge.impact import edit as E
from app.knowledge.impact.context import Budget, Section, sections_for
from app.knowledge.impact.spec import ChangeSpec

log = logging.getLogger("supportgraph.knowledge.impact.judge")

PROMPT_VERSION = f"judge-v2|{E.PROMPT_VERSION}"
MAX_EXTRA = 6                       # hunks the model made in lines the rules never nominated, per document


@dataclass
class Judgement:
    """What the model said about one line (attached to the passage it concerns)."""
    state: str                       # agrees | disagrees | only | unusable | unavailable
    note: str = ""
    extra: bool = False              # a change in a line that rule evidence never nominated
    hunk: int | None = None          # index into Candidate.hunks
    line: str | None = None          # the replacement line when the hunk is a one-line replacement (lets the rules' own rewrite be compared with it)
    flags: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------------------------------ cache
_CACHE: "OrderedDict[str, object]" = OrderedDict()
_CACHE_MAX = 256


def cache_key(kind: str, model: str, *parts: str) -> str:
    return hashlib.sha256("|".join((PROMPT_VERSION, kind, model, *parts)).encode()).hexdigest()


def cache_get(key: str):
    v = _CACHE.get(key)
    if v is not None:
        _CACHE.move_to_end(key)
    return v


def cache_put(key: str, value) -> None:
    _CACHE[key] = value
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


def _finish(out) -> str | None:
    meta = getattr(out, "response_metadata", None) or {}
    return meta.get("done_reason") or meta.get("finish_reason")


def ask(chat, description: str, title: str, section: Section, model_name: str) -> tuple[E.Extraction, bool]:
    """(the extraction of the model's reply, cache hit). A provider error is a 'none' extraction; the caller then relies on the rules."""
    key = cache_key("edit", model_name, description, title, section.text, section.header)
    hit = cache_get(key)
    if hit is not None:
        return hit, True
    from langchain_core.messages import HumanMessage

    t0 = time.monotonic()
    try:
        out = chat.invoke([HumanMessage(E.prompt(description, title, section))])
        ex = E.extract(getattr(out, "content", out), _finish(out))
    except Exception as e:                                           # noqa: BLE001 - an unreachable model is an outcome, not a crash
        log.warning("impact editor: call failed (%s)", type(e).__name__)
        return E.Extraction("none", why="the language model could not be reached"), False
    log.info("impact editor: %d lines, ~%d tokens in %.1fs, answer %s", len(section.rows), section.tokens, time.monotonic() - t0, ex.state)
    if ex.state == "ok":
        cache_put(key, ex)
    return ex, False


def semantic(chat, description: str, original: str, revised: str, model_name: str) -> tuple[str, str]:
    """('pass'|'fail'|'unsure'|'none', reason): the second, short opinion on a revision. Never raises."""
    if chat is None:
        return "none", ""
    key = cache_key("semantic", model_name, description, original, revised)
    hit = cache_get(key)
    if hit is not None:
        return hit
    from langchain_core.messages import HumanMessage

    try:
        out = chat.invoke([HumanMessage(E.semantic_prompt(description, original, revised))])
        res = E.parse_semantic(getattr(out, "content", out))
    except Exception as e:                                           # noqa: BLE001
        log.warning("impact editor: semantic check failed (%s)", type(e).__name__)
        return "none", ""
    if res[0] != "none":
        cache_put(key, res)
    return res


def hints(spec: ChangeSpec, extra_olds: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Optional hints of a value-style change: the values it introduces and replaces. A general change has none, and the validator does not need them."""
    facts = spec.actionable()
    news = tuple(f.new_value for f in facts if f.new_value)
    olds = tuple(dict.fromkeys([f.old_value for f in facts if f.old_value] + [o for o in extra_olds if o]))
    return news, olds


@dataclass
class Reading:
    """The outcome of reading one section."""
    section: Section
    state: str                                  # revised | unchanged | invalid | unavailable
    why: str = ""
    outcome: E.SectionOutcome | None = None
    semantic: tuple[str, str] = ("none", "")
    cached: bool = False


def read_section(chat, check_chat, section: Section, description: str, title: str, rules: E.Rules, s: Settings, model_name: str, expect_change: bool) -> Reading:
    """Ask, validate, and (when configured) double-check one section. `expect_change`: the rule evidence already says the change applies here (used to decide whether the second
    check is worth a call: it is skipped for an unchanged answer and for a revision the checks already reject)."""
    if "```" in section.text:
        return Reading(section, "unavailable", "the section contains a code fence, which the answer format cannot carry")
    ex, cached = ask(chat, description, title, section, model_name)
    if ex.state != "ok":
        return Reading(section, "unavailable", ex.why, cached=cached)
    out = E.revise(section, ex.text, rules)
    rd = Reading(section, out.state, out.why, out, cached=cached)
    if out.state != "revised":
        return rd
    if check_chat is not None and s.kb_ai_semantic_check != "off":
        risky = bool(out.review or out.rejected or any(h.flags for h in out.hunks))
        if s.kb_ai_semantic_check == "all" or risky:
            rd.semantic = semantic(check_chat, description, section.text, out.revised, model_name)
            verdict, why = rd.semantic
            if verdict == "fail":
                out.review.append(f"a second check of the revision disagrees: {why or 'it does not match the request'}")
            elif verdict == "unsure":
                out.review.append(f"a second check of the revision is unsure: {why or 'it could not tell'}")
    for h in out.hunks:
        h.flags += [f for f in out.review if f not in h.flags]
        h.notes += [n for n in out.notes if n not in h.notes]
    return rd


def rules_for(spec: ChangeSpec, title: str, s: Settings, extra_olds: list[str], today: date | None = None) -> E.Rules:
    news, olds = hints(spec, extra_olds)
    return E.Rules(spec.summary or "", title, news, olds, today, s.kb_ai_max_changed_fraction, s.kb_ai_max_removed_fraction)


def plan(content: str, starts: list[int], s: Settings) -> tuple[list[Section], Budget]:
    budget = Budget.from_settings(s)
    return sections_for(content, starts, budget), budget
