"""Account storage and login. Writes happen only here (and in scripts/create_account.py)."""

from sqlalchemy.orm import Session

from app.auth.passwords import burn_time, hash_password, verify_password
from app.auth.tokens import Principal
from app.db.models import Account, Student

ROLES = ("student", "staff", "admin")


def create_account(db: Session, tenant_id: str, username: str, password: str, role: str, student_id: str | None = None) -> Account:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if (role == "student") != bool(student_id):
        raise ValueError("a student account needs a student_id; staff/admin accounts must not have one")
    if student_id and db.get(Student, (tenant_id, student_id)) is None:
        raise ValueError(f"no student {student_id!r} in tenant {tenant_id!r}")
    acct = Account(tenant_id=tenant_id, username=username.strip().lower(), role=role, student_id=student_id,
                   password_hash=hash_password(password), disabled=False)
    db.merge(acct)  # re-running with the same username resets the password: that is the documented way to change it
    db.flush()
    return acct


def authenticate(db: Session, tenant_id: str, username: str, password: str) -> Principal | None:
    """The principal for a correct login, else None. The same work is done whether or not the user exists."""
    acct = db.get(Account, (tenant_id, username.strip().lower()))
    if acct is None:
        burn_time(password)
        return None
    if not verify_password(password, acct.password_hash) or acct.disabled:
        return None
    if acct.role == "student" and db.get(Student, (tenant_id, acct.student_id)) is None:
        return None  # the student record was removed: the login stops working
    return Principal(tenant_id=tenant_id, role=acct.role, subject=acct.username, student_id=acct.student_id)
