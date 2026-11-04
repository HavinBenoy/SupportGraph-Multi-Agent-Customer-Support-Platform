"""Create demo accounts for the SAMPLE tenant so the web UI can be tried. Refuses any tenant that is not marked is_sample.

    python scripts/demo_accounts.py            # password from SG_DEMO_PASSWORD, or a random one printed once

Creates: student `asha` (stu_asha), student `meera` (stu_meera), staff `staff1`, admin `admin1`."""

import os
import secrets
import sys

from app.auth.accounts import create_account
from app.config import get_settings
from app.db.session import session_scope
from app.tenants.loader import load_tenant_config

DEMO = [("asha", "student", "stu_asha"), ("meera", "student", "stu_meera"), ("staff1", "staff", None), ("admin1", "admin", None)]


def main() -> int:
    tenant = get_settings().default_tenant
    if not load_tenant_config(tenant).is_sample:
        print(f"refusing: tenant {tenant!r} is not sample data", file=sys.stderr)
        return 1
    pw = os.environ.get("SG_DEMO_PASSWORD") or secrets.token_urlsafe(12)
    with session_scope() as db:
        for user, role, sid in DEMO:
            create_account(db, tenant, user, pw, role, sid)
    print(f"created demo accounts in {tenant}: " + ", ".join(u for u, _, _ in DEMO))
    if not os.environ.get("SG_DEMO_PASSWORD"):
        print(f"password (shown once): {pw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
