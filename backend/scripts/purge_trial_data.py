"""Operator tool (M5): run the trial-data retention job once for one tenant.

    python scripts/purge_trial_data.py --tenant qspiders_sample --dry-run      # report only
    python scripts/purge_trial_data.py --tenant qspiders_sample                # delete what is past its retention period

Idempotent and safe to repeat. Recordings under a legal hold are never deleted; a deletion that fails is kept and retried on the next run.
Calls, tickets and knowledge documents are never touched. The audit entry holds counts only."""

import argparse
import json
import sys

from app.config import get_settings
from app.db.session import session_scope
from app.trial.recording import get_provider
from app.trial.retention import run_purge


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    with session_scope() as db:
        out = run_purge(db, get_provider(get_settings()), a.tenant, "operator-cli", dry_run=a.dry_run)
    print(json.dumps(out))
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
