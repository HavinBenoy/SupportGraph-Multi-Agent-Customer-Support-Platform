"""Engine + session handling. One lazily-created engine per process; tests and scripts can swap it."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def configure_engine(engine_or_url: Engine | str) -> Engine:
    """Use this engine from now on (tests, scripts). Disposes the previous one."""
    global _engine, _factory
    reset_engine()
    _engine = create_engine(engine_or_url, pool_pre_ping=True) if isinstance(engine_or_url, str) else engine_or_url
    _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    return _engine or configure_engine(get_settings().database_url)


def reset_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transaction: commits on success, rolls back on error, always closes."""
    get_engine()
    assert _factory is not None
    session = _factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
