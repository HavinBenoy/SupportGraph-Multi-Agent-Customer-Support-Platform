"""Where paused graph runs live.

A run that is waiting for an approval is a checkpoint: the whole state of the conversation, saved after every node. It is
what lets a staff member approve hours later, from another process, after a restart, and have the SAME run continue.

  memory    InMemorySaver. Lost when the process ends. Tests, demos, single-process scripts.
  postgres  PostgresSaver on the same database as everything else. The library owns its tables (`checkpoints`,
            `checkpoint_writes`, ...); `setup_checkpointer` creates them and is safe to run repeatedly.

Privacy note: a checkpoint contains the conversation text (and the last state of every field). Treat the checkpoint tables
like the tickets table: same retention and access rules. Thread ids are `<tenant>:<student|anonymous>:<ticket>`, so one
tenant's or student's thread can never be addressed with another's ids.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings, get_settings


def _conninfo(database_url: str) -> str:
    """SQLAlchemy URL -> libpq URL (the driver suffix `+psycopg` is not understood by psycopg itself)."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def get_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    s = settings or get_settings()
    if s.checkpointer == "memory":
        return InMemorySaver()
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    # One long-lived autocommit connection (what PostgresSaver documents). For many concurrent workers use a pool.
    conn = Connection.connect(_conninfo(s.database_url), autocommit=True, prepare_threshold=0, row_factory=dict_row)
    return PostgresSaver(conn)


def setup_checkpointer(saver: BaseCheckpointSaver) -> None:
    """Create/upgrade the library's tables. No-op for the in-memory saver."""
    if hasattr(saver, "setup"):
        saver.setup()
