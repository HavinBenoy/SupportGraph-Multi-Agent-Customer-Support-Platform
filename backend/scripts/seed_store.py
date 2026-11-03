"""Load the institute sample data into the configured database (SG_DATABASE_URL).

    python scripts/seed_store.py                       # dates relative to today
    python scripts/seed_store.py --as-of 2026-10-24    # pin the date (what the tests use)
    python scripts/seed_store.py --isolation-fixture   # also seed the second tenant used by isolation tests

Re-running replaces the seeded tenants' rows. Run `make db-migrate` first.
"""

import argparse
from datetime import date

from app.db.seed import CasesExistError, seed
from app.db.session import session_scope


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--as-of", type=date.fromisoformat, default=None)
    p.add_argument("--isolation-fixture", action="store_true")
    p.add_argument("--reset-cases", action="store_true", help="also DELETE tickets, escalations, requests and audit rows of the seeded tenants")
    a = p.parse_args()
    try:
        with session_scope() as db:
            counts = seed(db, as_of=a.as_of, include_isolation_fixture=a.isolation_fixture, reset_cases=a.reset_cases)
    except CasesExistError as e:
        raise SystemExit(f"{e}")
    for tenant, n in counts.items():
        print(f"seeded {tenant}: {n} enrolments")


if __name__ == "__main__":
    main()
