"""Sign-in and public configuration."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import client_key, require_principal
from app.api.schemas import ConfigOut, LoginIn, LoginOut, MeOut
from app.auth.accounts import authenticate
from app.auth.tokens import Principal, issue_token
from app.db.session import session_scope
from app.tenants.loader import load_tenant_config

router = APIRouter(prefix="/api", tags=["auth"])


def tenant_or_404(tenant: str | None, default: str):
    """Load a tenant's config or answer 404. An unknown tenant and a malformed one look the same."""
    try:
        return load_tenant_config(tenant or default)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "unknown institute") from None


@router.get("/config", response_model=ConfigOut)
def public_config(request: Request, tenant: str | None = None) -> ConfigOut:
    """What the widget needs before anyone signs in: the institute's name and whether its data is sample data."""
    cfg = tenant_or_404(tenant, request.app.state.settings.default_tenant)
    return ConfigOut(tenant_id=cfg.tenant_id, tenant_name=cfg.name, is_sample=cfg.is_sample)


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, request: Request) -> LoginOut:
    s = request.app.state.settings
    cfg = tenant_or_404(body.tenant, s.default_tenant)
    key = f"login:{cfg.tenant_id}:{body.username.strip().lower()}:{client_key(request)}"
    limiter = request.app.state.login_limiter
    if limiter.blocked(key):
        raise HTTPException(429, "too many failed attempts, try again later", headers={"Retry-After": str(s.login_lockout_seconds)})
    with session_scope() as db:
        principal = authenticate(db, cfg.tenant_id, body.username, body.password)
    if principal is None:
        limiter.record(key)
        raise HTTPException(401, "wrong username or password")  # same message for unknown user, wrong password, disabled account
    limiter.reset(key)
    ttl = s.token_ttl_minutes * 60
    token = issue_token(request.app.state.secret, principal, ttl_seconds=ttl)
    return LoginOut(token=token, role=principal.role, username=principal.subject, tenant_id=principal.tenant_id,
                    student_id=principal.student_id, expires_in=ttl)


@router.get("/auth/me", response_model=MeOut)
def me(p: Principal = Depends(require_principal)) -> MeOut:
    return MeOut(role=p.role, username=p.subject, tenant_id=p.tenant_id, student_id=p.student_id)
