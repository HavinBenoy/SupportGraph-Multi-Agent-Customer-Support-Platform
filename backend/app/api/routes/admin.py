"""Admin API: analytics for the admin's own tenant (support operations, GET /api/admin/analytics; knowledge governance, GET /api/admin/analytics/knowledge)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api import analytics, governance
from app.api.deps import require_admin
from app.auth.tokens import Principal
from app.db.session import session_scope
from app.tenants.loader import load_tenant_config

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/analytics")
def get_analytics(days: Annotated[int, Query(ge=1, le=90)] = 14, p: Principal = Depends(require_admin)) -> dict:
    try:
        cfg = load_tenant_config(p.tenant_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "this institute has no configuration") from None
    with session_scope() as db:
        return analytics.compute(db, cfg, days=days)


@router.get("/analytics/knowledge")
def get_knowledge_analytics(days: Annotated[int, Query(ge=1, le=365)] = 30, p: Principal = Depends(require_admin)) -> dict:
    """Knowledge-governance metrics of the administrator's own tenant (counts, rates, timings; no document text, no customer data)."""
    with session_scope() as db:
        return governance.compute(db, p.tenant_id, days=days)
