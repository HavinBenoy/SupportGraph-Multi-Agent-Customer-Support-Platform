"""Run the full evaluation: dev set, held-out set, baseline comparison, security suite, judge calibration (build step 6).

    make eval                                       # prints a summary, writes evals/reports/latest.{json,md}
    make eval ARGS="--reference evals/reports/reference_fake_v1.json"     # also fail on regressions against a stored report
    make eval ARGS="--save-reference"               # store this run as the new reference (do this deliberately, after review)

Needs a migrated, seeded, INGESTED database (make db-setup ingest). For the cross-tenant security cases to mean anything, the database should
also hold the second-tenant fixture (`python scripts/seed_store.py --isolation-fixture`): use a THROWAWAY database for that, see docs/STEP6.md.
Creates tickets/requests/audit rows under `eval-<id>-<run>` ticket ids and deletes exactly those afterwards. Never approves anything.
Exit code 1 on any gate failure: leaks, unauthorized or unexpected tool calls, writes without a decision, crashes, failed security
cases, or a regression beyond --tolerance against --reference.
"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

from app.evals import report
from app.evals.run import DATASETS_DIR, run_all

REPORTS = DATASETS_DIR.parent / "reports"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=REPORTS)
    p.add_argument("--name", default="latest")
    p.add_argument("--reference", type=Path, help="stored report to compare against (regressions fail the run)")
    p.add_argument("--tolerance", type=float, default=0.02, help="allowed worsening of a tracked metric, absolute (default 0.02)")
    p.add_argument("--save-reference", action="store_true", help="also write the result to reference_<provider>.json")
    p.add_argument("--today")
    p.add_argument("--no-baseline", action="store_true")
    a = p.parse_args()
    ref = json.loads(a.reference.read_text()) if a.reference else None
    try:
        rep = run_all(today=a.today, reference=ref, tolerance=a.tolerance, with_baseline=not a.no_baseline)
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup ingest", file=sys.stderr)
        return 2
    j, m = report.write(rep, a.out, a.name)
    if a.save_reference:
        provider = f"{'fake' if rep['meta']['offline_fakes'] else rep['meta']['llm_provider']}_v1"
        report.write(rep, a.out, f"reference_{provider}")
    print(report.full_markdown(rep))
    print(f"full report: {m} and {j}")
    return 1 if rep["gate_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
