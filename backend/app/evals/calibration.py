"""Calibrate a judge against human labels (build step 6).

Why: the judge decides whether answers are grounded. If it disagrees with people, its scores are noise. So before its numbers are quoted we
measure agreement on a labelled set: raw agreement, Cohen's kappa (agreement beyond chance; 0.6 is the usual bar for "substantial"), and
precision/recall on the label that matters most, UNGROUNDED (a missed hallucination is the costly error).

Label file: JSONL, one case per line: {id, question, answer, sources:[{text}], human:{grounded, relevant}, labeler, note}.
The shipped file `evals/datasets/judge_labels_v1.jsonl` was DRAFTED BY CLAUDE, not by a person: treat its agreement numbers as a harness
check until you have reviewed/relabelled it (scripts/judge_calibrate.py --relabel writes your labels to a separate file).
"""

import json
from pathlib import Path

from app.evals.judge import Judge

MIN_KAPPA = 0.6
MIN_NEGATIVES = 8  # an axis needs at least this many human "bad" labels before its kappa means anything


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two binary raters; None if undefined (no items, or both raters constant and identical)."""
    n = len(pairs)
    if not n:
        return None
    agree = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return None if expected == 1 else round((agree - expected) / (1 - expected), 4)


def _axis(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs = (human, judge). Positive class for precision/recall is the UNHEALTHY label (False = ungrounded / irrelevant)."""
    n = len(pairs)
    tp = sum((not h) and (not j) for h, j in pairs)
    fp = sum(h and (not j) for h, j in pairs)
    fn = sum((not h) and j for h, j in pairs)
    return {"n": n, "agreement": round(sum(h == j for h, j in pairs) / n, 4) if n else None, "kappa": kappa([(h, j) for h, j in pairs]),
            "flag_precision": round(tp / (tp + fp), 4) if tp + fp else None, "flag_recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "missed": fn, "false_alarms": fp}


def calibrate(judge: Judge, cases: list[dict]) -> dict:
    g, r, errors, disagreements = [], [], 0, []
    for c in cases:
        v = judge.judge(question=c["question"], answer=c["answer"], sources=c["sources"])
        if v.error:
            errors += 1
            continue
        g.append((c["human"]["grounded"], v.grounded))
        r.append((c["human"]["relevant"], v.relevant))
        if c["human"]["grounded"] != v.grounded or c["human"]["relevant"] != v.relevant:
            disagreements.append({"id": c["id"], "human": c["human"], "judge": {"grounded": v.grounded, "relevant": v.relevant},
                                  "why": v.rationale})
    grounded, relevant = _axis(g), _axis(r)
    for ax, pairs in ((grounded, g), (relevant, r)):
        ax["human_negatives"] = sum(not h for h, _ in pairs)  # kappa on a handful of negatives is noise: see MIN_NEGATIVES
    def ok(ax: dict) -> bool:
        return errors == 0 and ax["kappa"] is not None and ax["kappa"] >= MIN_KAPPA and ax["human_negatives"] >= MIN_NEGATIVES

    return {"judge": judge.name, "cases": len(cases), "judge_errors": errors, "grounded": grounded, "relevant": relevant,
            "trusted_for_groundedness": ok(grounded), "trusted_for_relevance": ok(relevant), "min_kappa": MIN_KAPPA,
            "disagreements": disagreements}
