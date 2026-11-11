"""Final M6 on real PostgreSQL 16: document deletion through an approved request (no hard delete, out of retrieval, versions kept), racing publish clicks archive
once, and the governance analytics query. Skipped unless SG_TEST_DATABASE_URL is set."""

import pytest
from sqlalchemy import select

from app.api import governance
from app.db.models import Chunk, KbDocument, KbEvent, KbVersion
from app.db.seed import seed
from app.db.session import session_scope
from app.knowledge import changes, release
from app.knowledge.errors import Conflict
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)
from tests.db.test_postgres_m6 import S, T, go_live, new_doc, race

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]


@pytest.fixture
def pg(migrated):  # noqa: F811
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    return migrated


def test_an_approved_deletion_archives_once_keeps_versions_and_leaves_retrieval_on_postgres(pg):  # noqa: F811
    new_doc()
    go_live()
    with session_scope() as db:
        rid = changes.request_deletion(db, T, "staff1", S, "fees-test", reason="course discontinued", expected_version=1)["request_id"]
    with session_scope() as db:
        assert db.get(KbDocument, (T, "fees-test")).status == "published"                                   # nothing removed on request
        assert db.execute(select(Chunk).where(Chunk.tenant_id == T, Chunk.doc_id == "fees-test")).first() is not None
        changes.review(db, T, "admin1", S, rid, "approve")

    def pub(i):
        with session_scope() as db:
            return release.begin(db, T, f"admin{i}", S, rid)["outcome"]
    out, errs = race(4, pub)
    assert out.count("deleted") == 1 and all(o == "already_published" for o in out if o != "deleted") and all(isinstance(e, Conflict) for e in errs), (out, errs)
    with session_scope() as db:
        d = db.get(KbDocument, (T, "fees-test"))
        assert (d.status, d.published_version) == ("archived", None)
        assert db.execute(select(Chunk).where(Chunk.tenant_id == T, Chunk.doc_id == "fees-test")).first() is None
        assert [v.status for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == "fees-test")).scalars()] == ["archived"]
        acts = [e.action for e in db.execute(select(KbEvent).where(KbEvent.tenant_id == T)).scalars()]
        assert acts.count("document_deletion_published") == 1 and "document_deletion_requested" in acts
        g = governance.compute(db, T, days=7)["knowledge"]
        assert g["by_kind"].get("deletion") == 1 and g["published"] == 1


def test_a_stale_deletion_is_refused_on_postgres(pg):  # noqa: F811
    new_doc()
    go_live()
    with session_scope() as db:
        rid = changes.request_deletion(db, T, "staff1", S, "fees-test", reason="course discontinued", expected_version=1)["request_id"]
    from app.knowledge import store
    with session_scope() as db:
        store.create_version(db, T, "admin1", S, "fees-test", content="# Fees\n\nThree instalments now.\n", change_note="edit")
    go_live(ver=2)
    with session_scope() as db, pytest.raises(Conflict):
        changes.review(db, T, "admin1", S, rid, "approve")
    with session_scope() as db:
        assert db.get(KbDocument, (T, "fees-test")).status == "published"
