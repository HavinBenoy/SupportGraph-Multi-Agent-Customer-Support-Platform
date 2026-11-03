"""Create the LangGraph checkpoint tables in the configured Postgres database (idempotent; run by `make db-migrate`).
Does nothing for SQLite URLs. The tables belong to the checkpointer library, not to our Alembic migrations."""

import sys

from app.config import get_settings
from app.graph.checkpointer import get_checkpointer, setup_checkpointer


def main() -> int:
    s = get_settings()
    if not s.database_url.startswith("postgresql"):
        print("checkpointer: database is not Postgres, nothing to set up")
        return 0
    saver = get_checkpointer(s.model_copy(update={"checkpointer": "postgres"}))
    try:
        setup_checkpointer(saver)
    finally:
        saver.conn.close()
    print("checkpointer: tables ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
