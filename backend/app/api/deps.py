"""Request-scoped dependencies: who is calling, and what they may do.

Roles: `student` (own chat, signed in), `staff` (escalations + approvals of THEIR tenant), `admin` (staff powers + analytics).
Anonymous visitors may only use the public chat. The tenant of every authenticated call comes from the signed token, never
from the request body, so a staff member of one tenant cannot name another tenant."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.tokens import Principal, TokenError, verify_token
from app.db.models import Account
from app.db.session import session_scope

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request):
    return request.app.state.settings


def secret_of(request: Request) -> str:
    return request.app.state.secret


def principal_from_token(secret: str, token: str) -> Principal:
    """Verify a bearer token AND that its account still exists, is enabled and has the same role/student. Raises HTTPException(401).
    Shared by the HTTP dependency below and the voice WebSocket (which cannot send an Authorization header from a browser)."""
    try:
        principal = verify_token(secret, token)
    except TokenError:
        raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from None
    # A valid signature is not enough: the account must still exist and be enabled, so disabling it, or changing its role or student, cuts off a live token at once.
    with session_scope() as db:
        acct = db.get(Account, (principal.tenant_id, principal.subject))
        if acct is None or acct.disabled or acct.role != principal.role or acct.student_id != principal.student_id:
            raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return principal


def optional_principal(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal | None:
    if creds is None:
        return None
    return principal_from_token(secret_of(request), creds.credentials)


def require_principal(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise HTTPException(401, "sign in required", headers={"WWW-Authenticate": "Bearer"})
    return p


def require_staff(p: Principal = Depends(require_principal)) -> Principal:
    if p.role not in ("staff", "admin"):
        raise HTTPException(403, "staff access required")
    return p


def require_admin(p: Principal = Depends(require_principal)) -> Principal:
    if p.role != "admin":
        raise HTTPException(403, "admin access required")
    return p


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
