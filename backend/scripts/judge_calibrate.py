"""Measure how well the answer judge agrees with human labels, and (optionally) label the cases yourself.

    make judge-calibrate                      # judge vs the shipped labels (drafted by Claude: a harness check, not ground truth)
    make judge-calibrate ARGS="--relabel"     # you label each case; saves evals/datasets/judge_labels_v1.human.jsonl, then calibrates against YOUR labels
    make judge-calibrate ARGS="--file evals/datasets/judge_labels_v1.human.jsonl"

With the fake provider the judge is the offline RuleJudge; with a real provider (SG_LLM_PROVIDER=ollama|openai_compat) it is the LLM judge.
Only trust a judge's groundedness numbers when this reports trusted_for_groundedness = True. Exit code 1 if it is not.
"""

import argparse
import json
import sys
from pathlib import Path

from app.evals.calibration import calibrate, kappa, load_cases
from app.evals.judge import get_judge
from app.evals.run import DATASETS_DIR, LABELS

HUMAN = DATASETS_DIR / "judge_labels_v1.human.jsonl"


def ask(prompt: str, default: bool) -> bool:
    while True:
        a = input(f"{prompt} [{'Y/n' if default else 'y/N'}, Enter keeps draft] ").strip().lower()
        if not a:
            return default
        if a in ("y", "n"):
            return a == "y"


def relabel(cases: list[dict]) -> list[dict]:
    print("For each answer: is EVERY factual claim supported by the passage? Is the answer relevant to the question?\n")
    out = []
    for i, c in enumerate(cases, 1):
        print(f"--- {i}/{len(cases)}  {c['id']}\nQ: {c['question']}\nPASSAGE: {c['sources'][0]['text']}\nANSWER: {c['answer']}")
        g = ask("grounded?", c["human"]["grounded"])
        r = ask("relevant?", c["human"]["relevant"])
        out.append({**c, "human": {"grounded": g, "relevant": r}, "labeler": "human", "needs_human_review": False})
    HUMAN.write_text("\n".join(json.dumps(o) for o in out) + "\n", encoding="utf-8")
    drafts = {c["id"]: c for c in cases}
    k = kappa([(drafts[o["id"]]["human"]["grounded"], o["human"]["grounded"]) for o in out])
    print(f"\nsaved {HUMAN}\nagreement between the draft labels and yours (grounded, kappa): {k}")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, default=DATASETS_DIR / LABELS)
    p.add_argument("--relabel", action="store_true")
    a = p.parse_args()
    cases = load_cases(a.file)
    if a.relabel:
        cases = relabel(cases)
    r = calibrate(get_judge(), cases)
    g, rel = r["grounded"], r["relevant"]
    print(f"\njudge `{r['judge']}` on {r['cases']} cases ({r['judge_errors']} judge errors)")
    print(f"  groundedness: agreement {g['agreement']:.0%}  kappa {g['kappa']}  precision {g['flag_precision']}  recall {g['flag_recall']}"
          f"  (missed hallucinations: {g['missed']}, false alarms: {g['false_alarms']}, human-labelled ungrounded: {g['human_negatives']})")
    print(f"  relevance   : agreement {rel['agreement']:.0%}  kappa {rel['kappa']}  (only {rel['human_negatives']} irrelevant examples)")
    print(f"  trusted for groundedness: {r['trusted_for_groundedness']}   trusted for relevance: {r['trusted_for_relevance']}")
    for d in r["disagreements"]:
        print(f"  disagree {d['id']}: human {d['human']} judge {d['judge']} ({d['why'][:80]})")
    return 0 if r["trusted_for_groundedness"] else 1


if __name__ == "__main__":
    sys.exit(main())
