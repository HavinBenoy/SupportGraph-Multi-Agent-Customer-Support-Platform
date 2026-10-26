"""All store queries. Every function takes a Scope, so a query without tenant_id is not expressible here.

Customer-owned data (orders) is additionally filtered by customer_id: a customer can only see their own.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Order, Tenant
from app.tenants.scope import Scope


def get_tenant(db: Session, scope: Scope) -> Tenant | None:
    return db.get(Tenant, scope.tenant_id)


def get_customer_order(db: Session, scope: Scope, order_id: str) -> Order | None:
    stmt = (
        select(Order)
        .where(Order.tenant_id == scope.tenant_id, Order.customer_id == scope.customer_id, Order.id == order_id)
        .options(selectinload(Order.items))
    )
    return db.execute(stmt).scalar_one_or_none()


def list_customer_orders(db: Session, scope: Scope, limit: int = 5) -> list[Order]:
    stmt = (
        select(Order)
        .where(Order.tenant_id == scope.tenant_id, Order.customer_id == scope.customer_id)
        .order_by(Order.placed_on.desc(), Order.id.desc())
        .limit(limit)
        .options(selectinload(Order.items))
    )
    return list(db.execute(stmt).scalars())
