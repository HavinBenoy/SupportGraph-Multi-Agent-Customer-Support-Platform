"""Ingest a tenant's public help articles: chunk, embed, store in Postgres (pgvector).

    cd backend && python scripts/ingest_docs.py                    # default tenant, only what changed
    cd backend && python scripts/ingest_docs.py --force            # re-embed everything
    cd backend && python scripts/ingest_docs.py --tenant qspiders_sample

Safe to re-run: unchanged documents are skipped. Reads only tenants/<id>/docs/; staff-only files in internal/ are never
read here, and a doc not marked `audience: public` aborts the run. Needs: make db-up db-setup.
"""

import argparse
import sys

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db.session import session_scope
from app.llm.gateway import get_embedding_client
from app.rag.documents import IngestError
from app.rag.ingest import ingest_tenant


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", default=get_settings().default_tenant)
    p.add_argument("--force", action="store_true", help="re-chunk and re-embed every document")
    a = p.parse_args()

    embedder = get_embedding_client()
    try:
        with session_scope() as db:  # one transaction: all documents or none
            r = ingest_tenant(db, a.tenant, embedder, force=a.force)
    except IngestError as e:
        print(f"Ingest refused: {e}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup", file=sys.stderr)
        return 2

    print(f"tenant {r.tenant_id}, embedding model {r.embedding_model}")
    for label, ids in (("added", r.added), ("updated", r.updated), ("unchanged", r.unchanged), ("removed", r.removed)):
        if ids:
            print(f"  {label:9} {', '.join(ids)}")
    print(f"  {r.chunks_written} chunks written" if r.changed else "  nothing to do (already up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
