"""Real-Postgres checks: migrations, constraints, and the tools running against the actual schema.

Skipped unless SG_TEST_DATABASE_URL points at a Postgres server where the user may CREATE DATABASE, e.g.
    SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres make test-pg
Each test module gets its own throwaway database, so your dev data is never touched.
"""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.models import Order
from app.db.seed import seed
from app.db.session import configure_engine, session_scope
from tests.conftest import AS_OF, scope_cfg

ADMIN_URL = os.environ.get("SG_TEST_DATABASE_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]

BACKEND = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"tenants", "customers", "orders", "order_items"}


def _alembic(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(scope="module")
def pg_url():
    name = f"sg_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def migrated(pg_url):
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = configure_engine(pg_url)
    yield engine


def test_migration_upgrades_and_downgrades_cleanly(pg_url):
    cfg = _alembic(pg_url)
    command.upgrade(cfg, "head")
    engine = create_engine(pg_url)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")  # and up again
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_models_and_migrations_are_in_sync(migrated):
    """If someone edits models.py without writing a migration, this fails."""
    with migrated.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"models differ from migrations: {diff}"


def test_foreign_keys_include_tenant_id(migrated):
    fks = inspect(migrated).get_foreign_keys("orders") + inspect(migrated).get_foreign_keys("order_items")
    assert fks and all("tenant_id" in fk["constrained_columns"] for fk in fks)


def test_postgres_rejects_cross_tenant_reference_and_bad_rows(migrated):
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    base = dict(tenant_id="acme_store", id="A9999", customer_id="cust_alice", status="processing",
                placed_on=AS_OF, total=1, currency="USD")
    for override in (dict(customer_id="cust_zed"),                 # other tenant's customer
                     dict(status="exploded"),
                     dict(status="delivered"),                     # delivered without delivered_on
                     dict(total=-1)):
        with pytest.raises(IntegrityError):
            with session_scope() as db:
                db.add(Order(**{**base, **override}))


def test_money_is_exact_numeric(migrated):
    with session_scope() as db:
        seed(db, as_of=AS_OF)
        assert str(db.get(Order, ("acme_store", "A1004")).total) == "33.99"
        assert db.execute(text("SELECT data_type FROM information_schema.columns "
                               "WHERE table_name='orders' AND column_name='total'")).scalar() == "numeric"


def test_tools_and_graph_work_against_postgres(migrated):
    from langchain_core.messages import HumanMessage

    from app.graph.builder import build_graph
    from app.tools.read_tools import check_return_eligibility, list_my_orders

    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    alice = scope_cfg("acme_store", "cust_alice")
    assert [o["order_id"] for o in list_my_orders.invoke({}, alice)["orders"]] == ["A1004", "A1001", "A1002"]
    assert check_return_eligibility.invoke({"order_id": "A1002"}, alice)["eligible"] is True
    # isolation on the real engine: same order id, two tenants, each sees only its own
    other = scope_cfg("other_store", "cust_alice")
    from app.tools.read_tools import get_order_status
    assert get_order_status.invoke({"order_id": "A1001"}, alice)["items"] == ["Wireless Mouse"]
    assert get_order_status.invoke({"order_id": "A1001"}, other)["items"] == ["SECRET OTHER-TENANT ITEM"]
    assert get_order_status.invoke({"order_id": "A1003"}, alice) == {"error": "Order not found for this account."}
    result = build_graph().invoke({"messages": [HumanMessage("Where is my order A1001?")]}, alice)
    assert "shipped" in result["messages"][-1].content


def test_pgvector_extension_is_available_for_step_2(migrated):
    with migrated.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_available_extensions WHERE name='vector'")).scalar() == 1
