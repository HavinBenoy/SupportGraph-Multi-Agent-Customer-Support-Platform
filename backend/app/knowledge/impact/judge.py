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
import re
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date

from app.config import Settings
from app.knowledge.impact import context as C
from app.knowledge.impact import edit as E
from app.knowledge.impact import trace as T
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


_NO_THINKING: set[str] = set()          # models that told us they cannot think (an "instruct" build): asked without thinking from then on


def _config(chat) -> dict:
    return {k: getattr(chat, k) for k in ("model", "reasoning", "num_ctx", "num_predict", "temperature") if getattr(chat, k, None) is not None}


def _without_thinking(chat):
    try:
        return chat.model_copy(update={"reasoning": False}) if getattr(chat, "reasoning", None) else chat
    except Exception:                                                # noqa: BLE001
        return chat


def _failure(e: Exception, timeout: float | None) -> str:
    msg = str(e).lower()
    name = type(e).__name__
    if "timeout" in name.lower() or "timed out" in msg:
        return f"the model did not answer within {int(timeout or 0)} s (SG_KB_AI_JUDGE_TIMEOUT_S); on a CPU a large section with thinking can take longer"
    if "connect" in msg or "connection" in name.lower() or "refused" in msg:
        return "the language model could not be reached (is `ollama serve` running?)"
    if "not found" in msg and "model" in msg:
        return "the configured model is not installed in Ollama (ollama pull it, or fix SG_LLM_MODEL_IMPACT)"
    return f"the model call failed ({name}: {str(e)[:120]})"


def _invoke(chat, text: str, model_name: str, timeout: float | None):
    """One call. A model that cannot think (Ollama answers 'does not support thinking', e.g. an *-instruct build) is retried once without thinking and remembered: before this, the
    error made every section 'unavailable' and the request looked like 'no change needed'."""
    from langchain_core.messages import HumanMessage

    use = _without_thinking(chat) if model_name in _NO_THINKING else chat
    try:
        return use.invoke([HumanMessage(text)]), use, None
    except Exception as e:                                          # noqa: BLE001
        if "think" in str(e).lower() and getattr(use, "reasoning", None):
            log.warning("impact editor: %s does not support thinking; asking without it", model_name)
            _NO_THINKING.add(model_name)
            T.event("model_call", "retry_without_thinking", model=model_name, why=str(e)[:200])
            plain = _without_thinking(use)
            try:
                return plain.invoke([HumanMessage(text)]), plain, None
            except Exception as e2:                                 # noqa: BLE001
                return None, plain, _failure(e2, timeout)
        return None, use, _failure(e, timeout)


def ask(chat, description: str, title: str, section: Section, model_name: str, feedback: str = "", doc: str | None = None,
        timeout: float | None = None) -> tuple[E.Extraction, bool]:
    """(the extraction of the model's reply, cache hit). A provider error is a 'none' extraction with the reason; the caller then relies on the rules."""
    key = cache_key("edit", model_name, description, title, section.text, section.header, feedback)
    hit = cache_get(key)
    if hit is not None:
        T.event("model_call", "cached", doc, section=section.heading, state=hit.state)
        return hit, True
    text = E.prompt(description, title, section, feedback)
    t0 = time.monotonic()
    out, used, failed = _invoke(chat, text, model_name, timeout)
    dt = round(time.monotonic() - t0, 1)
    if failed:
        log.warning("impact editor: call failed after %.1fs: %s", dt, failed)
        T.event("model_call", "model_failed", doc, section=section.heading, seconds=dt, config=_config(used), why=failed, prompt_tokens=C.est_tokens(text))
        return E.Extraction("none", why=failed), False
    raw = getattr(out, "content", out)
    finish = _finish(out)
    ex = E.extract(raw, finish)
    log.info("impact editor: %d lines, ~%d tokens in %.1fs, answer %s", len(section.rows), section.tokens, dt, ex.state)
    outcome = {"ok": "answered", "truncated": "truncated", "malformed": "malformed", "empty": "malformed", "none": "malformed"}[ex.state]
    T.event("model_call", outcome, doc, section=section.heading, seconds=dt, config=_config(used), finish=finish, prompt_tokens=C.est_tokens(text),
            answer_chars=len(str(raw or "")), retry=bool(feedback), why=ex.why or None, text={"prompt": text, "answer": raw})
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
    out, _, failed = _invoke(chat, E.semantic_prompt(description, original, revised), model_name + ":check", None)
    if failed:
        log.warning("impact editor: semantic check failed (%s)", failed)
        return "none", ""
    res = E.parse_semantic(getattr(out, "content", out))
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
    retried: bool = False


_REMOVAL = re.compile(r"\b(?:remov\w*|delet\w*|drop\w*|take out|no longer|discontinu\w*|withdraw\w*|should not be mentioned)\b", re.I)


def _feedback(rd: "Reading") -> str:
    o = rd.outcome
    if rd.state == "invalid":
        return rd.why
    if o is not None and rd.semantic[0] == "fail":
        return f"a reviewer found: {rd.semantic[1] or 'it does not carry out the request'}"
    if o is not None and o.rejected:
        return "; ".join(o.rejected[:3])
    return ""


def _attempt(chat, check_chat, section: Section, description: str, title: str, rules: E.Rules, s: Settings, model_name: str, doc: str | None, feedback: str) -> "Reading":
    ex, cached = ask(chat, description, title, section, model_name, feedback, doc, s.kb_ai_judge_timeout_s)
    if ex.state != "ok":
        return Reading(section, "unavailable", ex.why, cached=cached)
    out = E.revise(section, ex.text, rules)
    rd = Reading(section, out.state, out.why, out, cached=cached)
    T.event("validation", {"revised": "review" if (out.review or any(h.flags for h in out.hunks)) else "proposed", "unchanged": "no_change", "invalid": "rejected"}[out.state], doc,
            section=section.heading, accepted=[{"kind": h.kind, "removed": len(h.removed), "added": len(h.added), "flags": h.flags} for h in out.hunks],
            rejected=out.rejected, review=out.review, notes=out.notes, why=out.why or None,
            checks=["extraction", "line comparison", "headings", "table header and columns", "history", "invented figures", "relevance to the request", "duplicates",
                    "deleted share", "rewritten share"])
    if out.state != "revised":
        return rd
    if check_chat is not None and s.kb_ai_semantic_check != "off":
        risky = bool(out.review or out.rejected or any(h.flags for h in out.hunks)) or bool(E.CORRECTION_CUES.search(description) or _REMOVAL.search(description)
                                                                                           or re.search(r"\bonly\b", description, re.I))
        if s.kb_ai_semantic_check == "all" or risky:
            rd.semantic = semantic(check_chat, description, section.text, out.revised, model_name)
            T.event("semantic_check", rd.semantic[0], doc, section=section.heading, why=rd.semantic[1] or None)
    return rd


def read_section(chat, check_chat, section: Section, description: str, title: str, rules: E.Rules, s: Settings, model_name: str, expect_change: bool,
                 doc: str | None = None, budget_left=lambda: True) -> Reading:
    """Ask, validate, double-check one section; when a check rejects the revision or the second check FAILS, ask ONCE more with the reasons (`SG_KB_AI_EDIT_RETRIES`). The better
    reading wins (a clean revision over a flagged one over none); a failed model call is not retried (a timeout would only double the wait)."""
    if "```" in section.text:
        T.event("context", "not_sent", doc, section=section.heading, why="the section contains a ``` code fence, which the answer format cannot carry")
        return Reading(section, "unavailable", "the section contains a code fence, which the answer format cannot carry")
    rd = _attempt(chat, check_chat, section, description, title, rules, s, model_name, doc, "")
    tries = 0
    if (rd.state == "unchanged" and s.kb_ai_recheck_unchanged and expect_change is not None and section.focus and s.kb_ai_edit_retries > 0 and budget_left()
            and (_REMOVAL.search(description) or E.CORRECTION_CUES.search(description) or not rules.news)):
        # small models often return "unchanged" for removals / corrections worded unlike the text (1.7B: "remove the age restriction" 2 of 4 runs). One second look, with
        # the lines search found; a false change it might then make still has to pass every check, the second opinion and two people.
        tries += 1
        lines = "; ".join(r.text.strip()[:120] for r in section.rows if r.start in section.focus)[:400]
        fb = (f"you returned the section unchanged. Read the request again and check these lines: {lines}. If the request really does not apply to them, return the "
              "section unchanged again.")
        T.event("retry", "asking_again", doc, section=section.heading, why="the model returned the section unchanged although search found lines the request may concern")
        again = _attempt(chat, check_chat, section, description, title, rules, s, model_name, doc, fb)
        if again.state == "revised" and _rank(again) >= 2:
            rd = again
        rd.retried = True
    while tries < s.kb_ai_edit_retries and rd.state in ("invalid", "revised") and _feedback(rd) and budget_left():
        if rd.state == "revised" and rd.semantic[0] != "fail":
            break                     # a usable revision whose bad parts were dropped is not worth another CPU-minute; only "nothing usable" or a FAILED second check is
        tries += 1
        T.event("retry", "asking_again", doc, section=section.heading, why=_feedback(rd)[:300])
        again = _attempt(chat, check_chat, section, description, title, rules, s, model_name, doc, _feedback(rd))
        if _rank(again) > _rank(rd):
            rd = again
        rd.retried = True
    verdict, why = rd.semantic
    out = rd.outcome
    if out is not None and rd.state == "revised":
        if verdict == "fail":
            out.review.append(f"a second check of the revision disagrees: {why or 'it does not match the request'}")
        elif verdict == "unsure":
            out.review.append(f"a second check of the revision is unsure: {why or 'it could not tell'}")
        for h in out.hunks:
            if verdict == "pass":
                h.notes.append("a second check confirmed that the revision does what was requested")
            h.flags += [f for f in out.review if f not in h.flags]
            h.notes += [n for n in out.notes if n not in h.notes]
    return rd


def _rank(rd: "Reading") -> int:
    if rd.state == "revised":
        flagged = bool(rd.outcome.review or rd.outcome.rejected or any(h.flags for h in rd.outcome.hunks))
        return 0 if rd.semantic[0] == "fail" else (2 if flagged else 3)
    return 1 if rd.state == "unchanged" else 0


def rules_for(spec: ChangeSpec, title: str, s: Settings, extra_olds: list[str], today: date | None = None) -> E.Rules:
    news, olds = hints(spec, extra_olds)
    return E.Rules(spec.summary or "", title, news, olds, today, s.kb_ai_max_changed_fraction, s.kb_ai_max_removed_fraction)


def plan(content: str, starts: list[int], s: Settings) -> tuple[list[Section], Budget]:
    budget = Budget.from_settings(s)
    return sections_for(content, starts, budget), budget
