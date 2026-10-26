"""Schema constraints and seed data, on the fast SQLite store (FKs switched on in conftest)."""

from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Customer, Order, OrderItem, Tenant
from app.db.seed import seed
from app.db.seed_data import build_seed
from app.db.session import session_scope
from tests.conftest import AS_OF


def _order(**kw):
    base = dict(tenant_id="acme_store", id="A9999", customer_id="cust_alice", status="processing",
                placed_on=AS_OF, total=1, currency="USD")
    return Order(**{**base, **kw})


def test_seed_creates_expected_rows():
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Tenant)) == 2
        assert db.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == "acme_store")) == 12
        assert db.scalar(select(func.count()).select_from(Customer).where(Customer.tenant_id == "acme_store")) == 5


def test_seed_dates_are_relative_to_as_of():
    with session_scope() as db:
        o = db.get(Order, ("acme_store", "A1002"))
        assert o.delivered_on == date(2026, 10, 10) and o.placed_on == date(2026, 10, 6)
        assert db.get(Order, ("acme_store", "A1001")).eta == date(2026, 10, 27)


def test_order_totals_always_equal_sum_of_items():
    with session_scope() as db:
        for o in db.scalars(select(Order)):
            assert o.total == sum(i.unit_price * i.quantity for i in o.items), o.id


def test_seed_is_idempotent():
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    with session_scope() as db:
        expected = build_seed(include_isolation_fixture=True)
        assert db.scalar(select(func.count()).select_from(Order)) == sum(len(t.orders) for t in expected)
        assert db.scalar(select(func.count()).select_from(OrderItem)) == sum(
            len(o.items) for t in expected for o in t.orders)  # no duplicated rows after seeding twice


def test_reseeding_with_another_date_moves_every_date():
    with session_scope() as db:
        seed(db, as_of=date(2027, 1, 1))
    with session_scope() as db:
        assert db.get(Order, ("acme_store", "A1002")).delivered_on == date(2026, 12, 18)


def test_database_rejects_an_order_pointing_at_another_tenants_customer():
    # cust_zed exists only at other_store. A composite FK (tenant_id, customer_id) makes this impossible.
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(_order(customer_id="cust_zed"))


def test_same_order_id_may_exist_in_two_tenants_but_not_twice_in_one():
    with session_scope() as db:  # A1001 already exists in both tenants (seeded), so this is the proof
        assert db.get(Order, ("acme_store", "A1001")) and db.get(Order, ("other_store", "A1001"))
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(_order(id="A1001"))


@pytest.mark.parametrize("bad", [
    dict(status="exploded"),                                 # not a known status
    dict(status="delivered", delivered_on=None),             # delivered without a delivery date
    dict(status="shipped", delivered_on=date(2026, 10, 1)),  # delivery date without being delivered
    dict(total=-5),                                          # negative total
])
def test_check_constraints_reject_bad_orders(bad):
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(_order(**bad))


def test_item_must_reference_an_existing_order_in_the_same_tenant():
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(OrderItem(tenant_id="other_store", order_id="A1002", line_no=1, sku="x", name="x",
                             quantity=1, unit_price=1))  # A1002 exists only at acme_store


def test_repo_queries_cannot_be_called_without_a_scope():
    import inspect

    from app.db import repo
    for fn in (repo.get_tenant, repo.get_customer_order, repo.list_customer_orders):
        assert list(inspect.signature(fn).parameters)[:2] == ["db", "scope"]


def test_raw_sql_sanity_check_for_the_fk_pragma(seeded_db):
    with seeded_db.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
