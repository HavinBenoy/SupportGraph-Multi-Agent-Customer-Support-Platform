"""AI-assisted change workflow (migration 0012) on real PostgreSQL 16 + pgvector: reversible migration, state-machine constraints, fenced claims under REAL concurrent
transactions, and the publication guarantee: while a multi-document release is in flight a reader sees every document at its OLD text; after the single publishing
transaction commits it sees every document at its NEW text. Skipped unless SG_TEST_DATABASE_URL is set."""

import threading

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Chunk, KbChangeRequest, KbDocument, KbVersion
from app.db.seed import seed
from app.db.session import session_scope
from app.knowledge import changes, pipeline, release, store
from app.knowledge.errors import Conflict
from app.knowledge.impact import analyse as impact
from app.knowledge.impact.evaluate import RuleImpactModel
from app.rag.retrieval import search
from tests.conftest import AS_OF
from tests.db.test_postgres import ADMIN_URL, _alembic, migrated, pg_url  # noqa: F401  (fixtures)
from tests.db.test_postgres_m6 import EMB, S, T, race

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not ADMIN_URL, reason="SG_TEST_DATABASE_URL not set")]
S.kb_ai_background, S.kb_ai_mode = False, "rules"
DOCS = {
    "python-fees": ("Python Full Stack Course Fees", "# Fees\n\n## Current fee\nThe Python Full Stack course fee is ₹45,000 inclusive of GST.\n"),
    "brochure": ("Institute Brochure", "# Brochure\n\n## Programmes\n- Python Full Stack: Rs. 45,000/- for 8 months\n- Java Full Stack: Rs. 50,000/- for 8 months\n"),
    "faq": ("Admissions FAQ", "# FAQ\n\nQ: What is the fee for Python Full Stack?\nA: The Python Full Stack fee is INR 45000.\n"),
    "java-fees": ("Java Full Stack Course Fees", "# Fees\n\nThe Java Full Stack course fee is ₹50,000.\n"),
}
CHANGE = "The Python Full Stack course fee changed from ₹45,000 to ₹48,000."
AFFECTED = ("python-fees", "brochure", "faq")


@pytest.fixture
def pg(migrated):  # noqa: F811
    with session_scope() as db:
        seed(db, as_of=AS_OF, include_isolation_fixture=True)
    for doc_id, (title, body) in DOCS.items():
        with session_scope() as db:
            store.create_document(db, T, "admin1", S, doc_id=doc_id, title=title, category="fees", content=body)
        pipeline.process_version(T, "admin1", doc_id, 1, EMB, S)
        with session_scope() as db:
            pipeline.publish_version(db, T, "admin1", doc_id, 1, EMB.name)
    return migrated


def approved_request():
    with session_scope() as db:
        rid = changes.start_ai(db, T, "staff1", S, description=CHANGE)["request_id"]
    with session_scope() as db:
        _, att = changes.begin_analysis(db, T, "staff1", S, rid)
    impact.run(T, rid, att, S, RuleImpactModel(), EMB)
    with session_scope() as db:
        view = changes.get_for_owner(db, T, "staff1", rid)
        assert view["status"] == "awaiting_decisions", view["analysis"]
    for it in view["items"]:
        if it["actionable"] and it["ai"]["edits"]:
            with session_scope() as db:
                changes.decide_item(db, T, "staff1", S, rid, it["doc_id"], "accepted")
    with session_scope() as db:
        changes.submit(db, T, "staff1", rid, description="Python fee increase")
    with session_scope() as db:
        changes.start_review(db, T, "admin1", rid)
    with session_scope() as db:
        changes.review(db, T, "admin1", S, rid, "approve")
    return rid


def live():
    with session_scope() as db:
        return {d: (db.get(KbDocument, (T, d)).published_version, tuple(sorted(c.text for c in db.execute(select(Chunk).where(Chunk.tenant_id == T, Chunk.doc_id == d)).scalars())))
                for d in DOCS}


def release_now(rid, actor="admin1"):
    with session_scope() as db:
        out = release.begin(db, T, actor, S, rid)
    if out["outcome"] == "started":
        release.run(T, actor, rid, out["attempt"], S, EMB)
    with session_scope() as db:
        return db.get(KbChangeRequest, (T, rid))


def test_migration_0012_is_additive_reversible_and_keeps_part1_requests(pg_url):  # noqa: F811
    cfg = _alembic(pg_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0011")
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, timezone, attendance_threshold_pct, is_sample) VALUES ('keep_me','keep','Asia/Kolkata',75,true)"))
        c.execute(text("INSERT INTO kb_change_requests (tenant_id, request_id, status, source, description, row_version, created_by, created_at, updated_at) "
                       "VALUES ('keep_me','cr_old','submitted','manual','kept',3,'staff1',now(),now())"))
    command.upgrade(cfg, "0012")
    cols = {c["name"] for c in inspect(engine).get_columns("kb_change_requests")}
    assert {"analysis", "analysis_attempt", "release_attempt", "release", "failure", "return_note"} <= cols
    with engine.begin() as c:
        c.execute(text("UPDATE kb_change_requests SET status='awaiting_decisions' WHERE request_id='cr_old'"))                       # the widened state machine accepts the new states
        c.execute(text("UPDATE kb_change_requests SET status='publishing' WHERE request_id='cr_old'"))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("UPDATE kb_change_requests SET status='teleported' WHERE request_id='cr_old'"))
    command.downgrade(cfg, "0011")                                                                                                   # states map back, data stays
    with engine.connect() as c:
        assert c.execute(text("SELECT status, description, row_version FROM kb_change_requests")).one() == ("approved", "kept", 3)
    assert "analysis" not in {c["name"] for c in inspect(engine).get_columns("kb_change_requests")}
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_change_requests")).scalar() == 1
    engine.dispose()


def test_item_check_constraints_hold_on_postgres(pg):  # noqa: F811
    rid = approved_request()
    for col, bad in (("classification", "maybe"), ("admin_decision", "perhaps"), ("process_status", "half"), ("publish_status", "sort_of")):
        with pytest.raises(IntegrityError):
            with session_scope() as db:
                db.execute(text(f"UPDATE kb_change_items SET {col}=:v WHERE request_id=:r"), {"v": bad, "r": rid})


def test_full_flow_publishes_every_document_and_pgvector_serves_the_new_text(pg):  # noqa: F811
    rid = approved_request()
    before = live()
    r = release_now(rid)
    assert r.status == "published" and r.release["versions"] == {d: 2 for d in AFFECTED} and r.failure is None
    after = live()
    assert after["java-fees"] == before["java-fees"]
    for d in AFFECTED:
        assert after[d][0] == 2 and any("48" in t for t in after[d][1]) and not any("45" in t for t in after[d][1])
    with session_scope() as db:
        got = search(db, tenant_id=T, query_vector=EMB.embed_query("Python Full Stack course fee"), model_name=EMB.name, top_k=8)
    texts = " ".join(c.text for c in got)
    assert "48,000" in texts and "₹45,000" not in texts


def test_a_reader_sees_every_document_old_until_the_single_publish_transaction_commits(pg):  # noqa: F811
    rid = approved_request()
    old = live()
    with session_scope() as db:
        out = release.begin(db, T, "admin1", S, rid)
    in_tx, go = threading.Event(), threading.Event()
    real = release.pipeline.publish_version
    calls = []

    def paused(db, tenant, actor, doc_id, version_no, embedder_name, **kw):
        res = real(db, tenant, actor, doc_id, version_no, embedder_name, **kw)
        calls.append(doc_id)
        if len(calls) == 2:                                                           # two documents already rewritten INSIDE the open transaction
            in_tx.set()
            assert go.wait(20)
        return res
    release.pipeline.publish_version = paused
    t = threading.Thread(target=release.run, args=(T, "admin1", rid, out["attempt"], S, EMB))
    try:
        t.start()
        assert in_tx.wait(30)
        mid = live()                                                                  # a concurrent reader (separate connection, read committed)
        assert mid == old, "a reader saw a partially published release"
        with session_scope() as db:
            st = db.get(KbChangeRequest, (T, rid)).status
        assert st == "publishing"
        go.set()
        t.join(30)
    finally:
        release.pipeline.publish_version = real
        go.set()
    new = live()
    assert all(new[d][0] == 2 for d in AFFECTED) and new["java-fees"] == old["java-fees"]
    with session_scope() as db:
        assert db.get(KbChangeRequest, (T, rid)).status == "published"


def test_a_failure_in_the_publish_transaction_leaves_postgres_exactly_as_it_was(pg):  # noqa: F811
    rid = approved_request()
    before = live()
    real = release.pipeline.publish_version
    n = []

    def boom(db, *a, **k):
        n.append(1)
        if len(n) == 3:
            raise Conflict("simulated")
        return real(db, *a, **k)
    release.pipeline.publish_version = boom
    try:
        r = release_now(rid)
    finally:
        release.pipeline.publish_version = real
    assert r.status == "failed" and r.failure["stage"] == "publishing" and live() == before
    with session_scope() as db:
        assert all(v.status == "ready" for v in db.execute(select(KbVersion).where(KbVersion.version_no == 2, KbVersion.doc_id.in_(AFFECTED))).scalars())
    r2 = release_now(rid)
    assert r2.status == "published" and r2.release_attempt == 2
    with session_scope() as db:
        assert db.execute(select(KbVersion).where(KbVersion.version_no == 3)).first() is None


def test_racing_release_clicks_start_exactly_one_release_on_postgres(pg):  # noqa: F811
    rid = approved_request()

    def click(i):
        with session_scope() as db:
            return release.begin(db, T, f"admin{i}", S, rid)["attempt"]
    out, errs = race(6, click)
    assert out == [1] and len(errs) == 5 and all(isinstance(e, Conflict) for e in errs), (out, errs)
    release.run(T, "admin1", rid, 1, S, EMB)
    with session_scope() as db:
        assert db.get(KbChangeRequest, (T, rid)).status == "published"
        assert [v.version_no for v in db.execute(select(KbVersion).where(KbVersion.doc_id == "python-fees").order_by(KbVersion.version_no)).scalars()] == [1, 2]


def test_replayed_workers_of_one_attempt_never_publish_twice_or_publish_a_mixture(pg):  # noqa: F811
    """Attempt numbers are unique per claim, so two live workers for one attempt cannot happen; this replays the same attempt four times anyway. The invariants must hold
    whatever the interleaving: at most one publication, every document old or every document new, and a retry always finishes the job."""
    rid = approved_request()
    old = live()
    with session_scope() as db:
        att = release.begin(db, T, "admin1", S, rid)["attempt"]
    out, errs = race(4, lambda i: release.run(T, f"admin{i}", rid, att, S, EMB) or "ran")
    assert not errs
    mid = live()
    assert mid == old or all(mid[d][0] == 2 for d in AFFECTED)                            # never a mixture
    with session_scope() as db:
        status = db.get(KbChangeRequest, (T, rid)).status
        assert status in ("published", "failed")
        assert db.execute(text("SELECT count(*) FROM kb_events WHERE action='change_release_published' AND resource_id=:r"), {"r": rid}).scalar() == (1 if status == "published" else 0)
    if status == "failed":
        assert mid == old                                                                     # a failed release changed nothing
        assert release_now(rid).status == "published"
    final = live()
    assert all(final[d][0] == 2 for d in AFFECTED) and final["java-fees"] == old["java-fees"]
    with session_scope() as db:
        assert db.execute(text("SELECT count(*) FROM kb_events WHERE action='change_release_published' AND resource_id=:r"), {"r": rid}).scalar() == 1
        assert [v.version_no for v in db.execute(select(KbVersion).where(KbVersion.doc_id == "brochure").order_by(KbVersion.version_no)).scalars()] == [1, 2]


def test_racing_analysis_starts_have_one_winner_on_postgres(pg):  # noqa: F811
    with session_scope() as db:
        rid = changes.start_ai(db, T, "staff1", S, description=CHANGE)["request_id"]

    def go(i):
        with session_scope() as db:
            return changes.begin_analysis(db, T, "staff1", S, rid)[1]
    out, errs = race(5, go)
    assert out == [1] and len(errs) == 4 and all(isinstance(e, Conflict) for e in errs), (out, errs)


def test_racing_staff_decisions_on_one_item_serialise_on_postgres(pg):  # noqa: F811
    with session_scope() as db:
        rid = changes.start_ai(db, T, "staff1", S, description=CHANGE)["request_id"]
    with session_scope() as db:
        _, att = changes.begin_analysis(db, T, "staff1", S, rid)
    impact.run(T, rid, att, S, RuleImpactModel(), EMB)
    with session_scope() as db:
        rv = next(i for i in changes.get_for_owner(db, T, "staff1", rid)["items"] if i["doc_id"] == "brochure")["row_version"]

    def decide(i):
        with session_scope() as db:
            return changes.decide_item(db, T, "staff1", S, rid, "brochure", "accepted" if i % 2 else "discarded", expected_row_version=rv)["decision"]
    out, errs = race(6, decide)
    assert len(out) == 1 and len(errs) == 5 and all(isinstance(e, Conflict) for e in errs), (out, errs)
