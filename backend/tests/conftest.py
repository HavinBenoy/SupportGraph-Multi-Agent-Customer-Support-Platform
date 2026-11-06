import os

# Tests must never send traces anywhere, even if a developer's real .env has tracing switched on.
# Real environment variables outrank the .env file in pydantic-settings, so this wins.
os.environ["SG_TRACING_ENABLED"] = "false"
os.environ["SG_LLM_PROVIDER"] = "fake"
# Same for embeddings: a developer who switched their .env to the real Ollama stack (M1b) must still get the offline fakes in tests,
# otherwise code that reads get_settings() looks for vectors under the real embedding name and finds none.
os.environ["SG_EMBEDDING_PROVIDER"] = "fake"

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.db.session import configure_engine, reset_engine, session_scope  # noqa: E402

AS_OF = date(2026, 10, 24)  # the date every test pins; seed offsets are relative to it


@pytest.fixture(autouse=True)
def seeded_db():
    """Fresh in-memory SQLite database (both tenants, dates pinned to AS_OF) for every test. No Docker needed.
    Postgres-specific behaviour is covered separately in tests/db/test_postgres.py."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    configure_engine(engine)
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    yield engine
    reset_engine()


@pytest.fixture
def asha_cfg():
    """Trusted scope for student Asha (active in PYF-101) at qspiders_sample, with a frozen 'today'."""
    return {"configurable": {"tenant_id": "qspiders_sample", "student_id": "stu_asha", "today": "2026-10-24"}}


def scope_cfg(tenant: str, student: str, today: str = "2026-10-24", **extra):
    return {"configurable": {"tenant_id": tenant, "student_id": student, "today": today}, **extra}
