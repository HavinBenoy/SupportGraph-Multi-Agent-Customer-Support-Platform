"""Aggregate the per-scenario JSON written by `SG_EVAL_OUT=<dir> pytest tests/knowledge/test_ai_eval.py` into one table. Prints only what the files contain."""
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: eval_impact_summary.py <dir>")
        return 2
    rows = [json.loads(p.read_text()) for p in sorted(Path(argv[1]).glob("*.json"))]
    if not rows:
        print("no results found")
        return 1
    print(f"{'scenario':58} {'prec':>5} {'rec':>5} {'exact':>7} {'wrong':>5} {'unsup':>5} {'hist':>4}")
    for r in rows:
        if "precision" not in r:
            print(f"{r['scenario']:58} " + " ".join(f"{k}={v}" for k, v in r.items() if k != "scenario"))
            continue
        print(f"{r['scenario']:58} {r['precision']:5.2f} {r['recall']:5.2f} {r['replacement_exact']:>3}/{r['replacement_total']:<3} {r['wrong_edits']:5d} {r['unsupported']:5d} {r['historical_guarded']:4d}")
    scored = [r for r in rows if "precision" in r]
    if scored:
        n = len(scored)
        ex, tot = sum(r["replacement_exact"] for r in scored), sum(r["replacement_total"] for r in scored)
        print(f"\n{len(rows)} scenarios ({n} scored) · mean precision {sum(r['precision'] for r in scored) / n:.3f} · mean recall {sum(r['recall'] for r in scored) / n:.3f} · "
              f"exact replacements {ex}/{tot} · wrong edits {sum(r['wrong_edits'] for r in scored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
