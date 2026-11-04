"""Operator tool: create (or disable) a sign-in account. There is deliberately NO public sign-up endpoint.

    SG_NEW_PASSWORD=... python scripts/create_account.py --tenant qspiders_sample --username asha --role student --student-id stu_asha
    python scripts/create_account.py --tenant qspiders_sample --username s_priya --role staff      # prompts for the password
    python scripts/create_account.py --tenant qspiders_sample --username asha --disable

The password comes from the SG_NEW_PASSWORD variable or a hidden prompt, never from a command-line argument (which would land in
shell history and `ps`)."""

import argparse
import getpass
import os
import sys

from app.auth.accounts import ROLES, create_account
from app.db.models import Account
from app.db.session import session_scope


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=sorted(ROLES))
    ap.add_argument("--student-id")
    ap.add_argument("--disable", action="store_true")
    a = ap.parse_args(argv)
    with session_scope() as db:
        if a.disable:
            acct = db.get(Account, (a.tenant, a.username.strip().lower()))
            if acct is None:
                print("no such account", file=sys.stderr)
                return 1
            acct.disabled = True
            print(f"disabled {a.tenant}/{acct.username}")
            return 0
        if not a.role:
            ap.error("--role is required when creating an account")
        pw = os.environ.get("SG_NEW_PASSWORD") or getpass.getpass("password: ")
        try:
            acct = create_account(db, a.tenant, a.username, pw, a.role, a.student_id)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"created {acct.role} account {a.tenant}/{acct.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
