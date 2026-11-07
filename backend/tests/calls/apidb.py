"""A file-backed SQLite database for the tests that drive real sockets AND REST from several threads (the shared in-memory connection cannot)."""

import pytest

from app.db.session import session_scope


@pytest.fixture(autouse=True)
def seeded_db(tmp_path):  # noqa: F811
    """These tests drive real sockets AND REST at once from several threads. SQLite's shared in-memory connection cannot do that
    ("cannot commit - SQL statements in progress"), so here the database is a temp file with separate connections, like production."""
    from sqlalchemy import create_engine, event
    from app.db.base import Base
    from app.db.seed import seed
    from app.db.session import configure_engine, reset_engine
    from tests.conftest import AS_OF

    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(c, _):
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    configure_engine(engine)
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    yield engine
    reset_engine()
    engine.dispose()
