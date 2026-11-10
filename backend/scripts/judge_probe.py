"""Try the generalized section editor with YOUR Ollama model, exactly as the application does, and see every step. Also a ten-scenario suite for Qwen3 1.7B / 4B.

One request on your own text:
    cd backend
    ../.venv/bin/python scripts/judge_probe.py -d "remove the age restriction from eligibility" --file section.md
    ../.venv/bin/python scripts/judge_probe.py -d "..." --text $'Machine Learning faculty is Santhosh sir\\nHe has a work experience of 3 years.'
The thirteen realistic scenarios (incl. the three reported Santhosh-sir cases) (replace a value, add a qualification, online -> hybrid, remove a restriction, add a course, several statements, history kept, unrelated text unchanged,
ambiguous request, tables / headings / lists preserved), against the real model, with the real validator:
    ../.venv/bin/python scripts/judge_probe.py --suite
    SG_LLM_MODEL_IMPACT=qwen3:4b ../.venv/bin/python scripts/judge_probe.py --suite --runs 2          # another model; SG_KB_AI_JUDGE_THINKING=false to compare without thinking
    SG_KB_AI_JUDGE_NUM_CTX=12288 SG_KB_AI_JUDGE_MAX_TOKENS=6144 SG_KB_AI_CONTEXT_TOKENS=3000 ../.venv/bin/python scripts/judge_probe.py --suite     # a larger window, for experiments

It needs `ollama serve` and the model pulled (nothing is sent anywhere else). It never touches the database or any document. Each scenario prints the model's raw answer length, how long the
call took, what the validator did (accepted / dropped changes with reasons, review flags) and PASS / FAIL against what a person would expect. A small model may fail some scenarios:
that is information about the model, not a failure of the safeguards (a wrong answer is dropped or flagged, never applied)."""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.knowledge.impact import context as C  # noqa: E402
from app.knowledge.impact import edit as E  # noqa: E402
from app.knowledge.impact import judge as J  # noqa: E402
from app.knowledge.impact.analyse import vague  # noqa: E402
from app.knowledge.impact.evaluate import apply_edits  # noqa: E402
from app.knowledge.impact.spec import parse_rules  # noqa: E402
from app.llm.gateway import get_check_chat, get_judge_chat  # noqa: E402

DS = """# Data Science

## Faculty

Machine Learning faculty is Santhosh sir.
He has a work experience of 3 years. Previously he worked in IT.

## Eligibility

- Any graduate
- Age under 30
- Online classes only

## Fees

| Course | Fee |
|---|---|
| Data Science | 15000 |
"""
WEB = "# Web Development\n\n## Faculty\n\nWeb faculty is Rao sir. He has a work experience of 7 years.\n\n## Eligibility\n\n- 12th pass\n- Classes are conducted offline\n"
MULTI = DS.replace("- Online classes only", "- Online classes only\n- Weekend batch: online classes")

ONE = "# Data Science\n\n## Faculty\n\nMachine Learning faculty is Santhosh sir. He has a work experience of 3 years. Previously he worked in IT. He do have a M.Tech in Data Science.\n"

# (name, description, document, a line inside the part that matters, predicate on (state, patched text, original text), what is expected in words)
SUITE = [
    ("1 replace a value", "change the work experience of santhosh sir from 3 years to 5 years", DS, "He has a work", lambda st, new, old: st == "revised" and "5 years" in new and "3 years" not in new and "Previously he worked in IT." in new, "3 -> 5 years, history kept"),
    ("2 add a qualification", "Santhosh sir also holds an MTech in AI", DS, "He has a work", lambda st, new, old: st == "revised" and "MTech" in new and "3 years" in new, "a new line / sentence about the MTech"),
    ("3 online -> hybrid", "change online classes to hybrid classes", DS, "- Online", lambda st, new, old: st == "revised" and "ybrid" in new and "Any graduate" in new, "the list item says hybrid"),
    ("4 remove a restriction", "remove the age restriction from the eligibility criteria", DS, "- Age", lambda st, new, old: st == "revised" and "Age under 30" not in new and "Any graduate" in new and "Online classes only" in new, "only the age item removed"),
    ("5 add a course", "add a new course Cloud Computing with fee 20000", DS, "| Data Science", lambda st, new, old: st == "revised" and "Cloud Computing" in new and "20000" in new and "| Data Science | 15000 |" in new, "a new table row"),
    ("6 several statements", "online classes are now hybrid, for both the eligibility list and the weekend batch", MULTI, "- Online", lambda st, new, old: st == "revised" and new.lower().count("hybrid") >= 2, "both statements updated"),
    ("7 history kept", "change the work experience of santhosh sir from 3 years to 5 years", DS, "He has a work", lambda st, new, old: st == "revised" and "Previously he worked in IT." in new, "current value updated, the 'Previously ...' sentence untouched"),
    ("8 unrelated unchanged", "change online classes to hybrid classes", WEB, "- Classes", lambda st, new, old: st == "unchanged" or new == old, "the section is returned unchanged"),
    ("9 ambiguous request", "We changed the Data Science eligibility.", DS, "- Any", lambda st, new, old: vague("We changed the Data Science eligibility.") and st in ("unchanged", "invalid", "revised"), "the request is vague: the application asks a question before any model call"),
    ("11 (reported A) remove education", "Remove the education details of santhosh sir", ONE, "Machine Learning",
     lambda st, new, old: st == "revised" and "M.Tech" not in new and "Previously he worked in IT." in new and "3 years" in new, "the M.Tech sentence removed, the rest kept"),
    ("12 (reported B) correct the past industry", "Change the industry in which santhosh sir worked to Mechanical. That is whatever mentioned as the field in which santhosh sir "
     "worked previously in should be changed towards Mechanical Field rather than any other field.", ONE, "Machine Learning",
     lambda st, new, old: st == "revised" and "IT." not in new and "echanical" in new and "M.Tech" in new, "IT -> Mechanical in the 'Previously' sentence, nothing else"),
    ("13 (reported C) correct several statements", "Santhosh sir actually has only a B.Tech in AI and worked in a mechanical company as a support officer.", ONE, "Machine Learning",
     lambda st, new, old: st == "revised" and "M.Tech" not in new and "B.Tech in AI" in new and "support officer" in new and "3 years" in new, "M.Tech replaced by B.Tech, IT replaced"),
    ("10 structure preserved", "change online classes to hybrid classes", DS, "- Online", lambda st, new, old: st == "revised" and new.count("## ") == 3 and "| Course | Fee |\n|---|---|" in new, "headings, list, table intact"),
]


def read(chat, check, s, desc, doc, at, title="Data Science", quiet=False):
    budget = C.Budget.from_settings(s)
    secs = C.sections_for(doc, [doc.index(at)], budget)
    if not secs:
        raise SystemExit("could not locate the line in the document")
    sec = secs[0]
    spec = parse_rules(desc)
    rules = J.rules_for(spec.model_copy(update={"summary": desc}), title, s, [], date.today())
    t0 = time.monotonic()
    rd = J.read_section(chat, check, sec, desc, title, rules, s, "probe", expect_change=True)
    dt = time.monotonic() - t0
    if not quiet:
        print(f"  section: {sec.kind}, ~{sec.tokens} tokens ({budget.describe()})")
    new = sec.text
    if rd.outcome and rd.outcome.hunks:
        patched = apply_edits(doc, [{"start": h.start, "end": h.end, "original": h.original, "replacement": h.replacement} for h in rd.outcome.hunks])
        new = patched
    return rd, sec, new, dt


def report(rd, dt):
    print(f"  model call + validation: {dt:.1f}s -> {rd.state}" + (f" ({rd.why})" if rd.why else ""))
    o = rd.outcome
    if o:
        for h in o.hunks:
            print(f"    ACCEPTED {h.kind}: {h.original!r}\n           -> {h.replacement!r}" + (f"\n           flags: {h.flags}" if h.flags else ""))
        for r in o.rejected:
            print(f"    DROPPED  {r}")
        for r in o.review:
            print(f"    REVIEW   {r}")
        for n in o.notes:
            print(f"    NOTE     {n}")
    if rd.semantic[0] != "none":
        print(f"    second check: {rd.semantic[0].upper()} {rd.semantic[1]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--description")
    ap.add_argument("--file")
    ap.add_argument("--text")
    ap.add_argument("--title", default="Document")
    ap.add_argument("--suite", action="store_true")
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()
    s = get_settings()
    if s.kb_ai_judge == "off":
        s = s.model_copy(update={"kb_ai_judge": "uncertain"})
    if s.llm_provider != "ollama":
        print(f"SG_LLM_PROVIDER is {s.llm_provider!r}: set SG_LLM_PROVIDER=ollama (and SG_LLM_MODEL_IMPACT=qwen3:1.7b or qwen3:4b) to use a real model.")
        return 2
    chat, check = get_judge_chat(s), get_check_chat(s)
    print(f"model: {s.llm_model_impact or s.llm_model}  thinking={s.kb_ai_judge_thinking}  {C.Budget.from_settings(s).describe()}")
    if a.suite:
        J._CACHE.clear()
        passed = total = 0
        for name, desc, doc, at, ok, expect in SUITE:
            for run in range(a.runs):
                J._CACHE.clear()
                print(f"\n[{name}] {desc!r}   expected: {expect}")
                if vague(desc):
                    print("  (vague: the application asks the staff member to restate it; no model call)")
                    passed += 1
                    total += 1
                    continue
                rd, sec, new, dt = read(chat, check, s, desc, doc, at)
                report(rd, dt)
                good = bool(ok(rd.state, new, doc))
                passed += good
                total += 1
                print("  RESULT:", "PASS" if good else "FAIL")
        print(f"\n{passed}/{total} scenarios behaved as a person would expect (model: {s.llm_model_impact or s.llm_model}).")
        return 0
    if not a.description:
        print("give --description with --file/--text, or --suite")
        return 2
    text = Path(a.file).read_text() if a.file else (a.text or "")
    if not text.strip():
        print("give --file or --text")
        return 2
    first = next((ln for ln in text.split("\n") if ln.strip() and not ln.lstrip().startswith("#")), text.split("\n")[0])
    rd, sec, new, dt = read(chat, check, s, a.description, text, first, a.title)
    print("=== PROMPT ===\n" + E.prompt(a.description, a.title, sec))
    report(rd, dt)
    print("\n=== RESULT ===\n" + new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
