"""Load the mock store into a database. Idempotent: re-seeding a tenant replaces its rows."""

from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Customer, Order, OrderItem, Tenant
from app.db.seed_data import SeedTenant, build_seed


def _delete_tenant(db: Session, tenant_id: str) -> None:
    # Child tables first (foreign keys).
    for model in (OrderItem, Order, Customer):
        db.execute(delete(model).where(model.tenant_id == tenant_id))
    db.execute(delete(Tenant).where(Tenant.id == tenant_id))


def _insert_tenant(db: Session, t: SeedTenant, as_of: date) -> int:
    c = t.config
    db.add(Tenant(id=c.tenant_id, name=c.name, currency=c.currency, return_window_days=c.return_window_days))
    db.flush()
    for cid, name, email in t.customers:
        db.add(Customer(tenant_id=c.tenant_id, id=cid, name=name, email=email))
    db.flush()
    for o in t.orders:
        db.add(Order(
            tenant_id=c.tenant_id, id=o.id, customer_id=o.customer_id, status=o.status,
            carrier=o.carrier, tracking=o.tracking,
            placed_on=as_of - timedelta(days=o.placed_days_ago),
            eta=None if o.eta_in_days is None else as_of + timedelta(days=o.eta_in_days),
            delivered_on=None if o.delivered_days_ago is None else as_of - timedelta(days=o.delivered_days_ago),
            total=sum(i.unit_price * i.quantity for i in o.items),  # derived, so it always matches the items
            currency=c.currency,
        ))
    db.flush()
    for o in t.orders:
        for n, i in enumerate(o.items, start=1):
            db.add(OrderItem(tenant_id=c.tenant_id, order_id=o.id, line_no=n, sku=i.sku, name=i.name,
                             quantity=i.quantity, unit_price=i.unit_price))
    db.flush()
    return len(t.orders)


def seed(db: Session, as_of: date | None = None, include_isolation_fixture: bool = False) -> dict[str, int]:
    """Returns {tenant_id: number_of_orders}. Caller commits."""
    as_of = as_of or date.today()
    result = {}
    for t in build_seed(include_isolation_fixture):
        _delete_tenant(db, t.config.tenant_id)
        result[t.config.tenant_id] = _insert_tenant(db, t, as_of)
    return result
