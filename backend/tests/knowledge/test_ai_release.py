"""Admin review, approval and the integrated process-and-publish release (Part 2): consistency, failure, retry, idempotency, staleness, overlap, audit, isolation."""

import threading

import pytest
from sqlalchemy import select

from app.db.models import Chunk, Document, KbDocument, KbVersion
from app.db.session import session_scope
from app.knowledge import release
from app.knowledge.errors import Conflict
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.institute import DOCS, seed
from tests.knowledge.kbhelp import BASE, SECRET_TEXT, admin, live_chunks, other_admin
from tests.knowledge.kwhelp import ST, live_texts, staff, student
from tests.knowledge.test_ai_flow import events, republish, snapshot
from tests.rag.conftest import embedder, ingested  # noqa: F401

FEE_DOCS = {"python-fees", "brochure", "admissions-faq", "fee-archive", "java-fees", "python-faculty"}
AFFECTED = ("python-fees", "brochure", "admissions-faq")


def live_state():
    """What retrieval sees: for every document its published pointer and its live chunk texts."""
    with session_scope() as db:
        return {d.doc_id: (d.kb_version, d.content_hash, tuple(t for _, t in live_chunks(d.doc_id))) for d in db.execute(select(Document).where(Document.tenant_id == T)).scalars()}


def make(api, only=FEE_DOCS, description=A.FEE_CHANGE, discard=()):
    seed(api, only=only)
    req = A.analysed(api, description)
    rid = req["request_id"]
    for it in req["items"]:
        if it["actionable"] and it["ai"]["edits"]:
            if it["doc_id"] in discard:
                A.decide(api, rid, it["doc_id"], "discarded", reason="not now")
            else:
                A.decide(api, rid, it["doc_id"], "accepted")
    A.submit(api, rid)
    return rid


def approved(api, **kw):
    rid = make(api, **kw)
    A.a_post(api, rid, "start-review")
    assert A.approve(api, rid)["status"] == "approved"
    return rid


class Flaky:
    """Wraps the real (fake-provider) embedder; fails for any text containing `marker`."""

    def __init__(self, inner, marker):
        self.inner, self.marker, self.name = inner, marker, inner.name

    def embed_documents(self, texts):
        if any(self.marker in t for t in texts):
            raise RuntimeError("embedding service unavailable")
        return self.inner.embed_documents(texts)

    def embed_query(self, t):
        return self.inner.embed_query(t)


def base_embedder(api):
    from app.llm.gateway import get_embedding_client
    return get_embedding_client(api.app.state.settings)


# ---- review ----------------------------------------------------------------------------------------------------------------------------
def test_request_list_shows_everything_the_administrator_scans_for(api):  # noqa: F811
    rid = make(api, discard=("admissions-faq",))
    row = next(r for r in api.get(f"{BASE}/changes", headers=admin(api)).json()["items"] if r["request_id"] == rid)
    assert row["status"] == "submitted" and row["description"] == "Python fee increase"
    assert row["submitted_by"] == "staff1" and row["submitted_at"] and row["created_at"]
    assert row["document_count"] == 5 and row["counts"]["accepted"] == 2 and row["counts"]["discarded"] == 1 and row["counts"]["edited"] == 0 and row["counts"]["related"] == 2
    assert api.get(f"{BASE}/changes?status=submitted", headers=admin(api)).json()["total"] == 1
    assert api.get(f"{BASE}/changes?status=draft", headers=admin(api)).status_code == 422


def test_opening_a_request_starts_the_review_once(api):  # noqa: F811
    rid = make(api)
    assert A.a_get(api, rid)["status"] == "submitted"
    assert A.a_post(api, rid, "start-review")["status"] == "under_review"
    assert A.a_post(api, rid, "start-review")["status"] == "under_review"
    assert [e[0] for e in events(rid)].count("change_request_review_started") == 1


def test_admin_sees_original_proposed_diff_ai_reasoning_and_staff_decisions(api):  # noqa: F811
    rid = make(api, discard=("admissions-faq",))
    ad = A.a_get(api, rid)
    pf = A.item(ad, "python-fees")
    assert pf["proposed_content"] != DOCS["python-fees"][1] and pf["diff"]["body"] and pf["ai"]["reason"] and pf["ai"]["passages"]
    assert pf["base_version"] == 1 and pf["current_published_version"] == 1
    faq = A.item(ad, "admissions-faq")
    assert faq["decision"] == "discarded" and faq["rationale"] == "not now" and faq["proposed_content"] == DOCS["admissions-faq"][1]


def test_admin_final_edit_is_rebuilt_from_the_base_and_marked(api):  # noqa: F811
    rid = make(api)
    A.a_post(api, rid, "start-review")
    r = api.put(f"{BASE}/changes/{rid}/items/brochure/edit", json={"replacements": [{"index": 0, "replacement": "- Python Full Stack: Rs. 48,000/- for 8 months (GST extra)"}], "note": "add GST"},
                headers=admin(api))
    assert r.status_code == 200, r.text
    it = r.json()
    assert it["admin_edited"] is True and it["admin_decision"] == "confirmed" and it["admin_by"] == "admin1" and it["admin_note"] == "add GST" and "GST extra" in it["proposed_content"]
    assert it["decision"] == "accepted"                                                      # the staff decision is kept as it was
    assert A.item(A.a_get(api, rid), "brochure")["admin_edited"] is True


def test_administrator_cannot_resurrect_a_discarded_document_or_edit_what_staff_never_proposed(api):  # noqa: F811
    rid = make(api, discard=("admissions-faq",))
    A.a_post(api, rid, "start-review")
    assert api.put(f"{BASE}/changes/{rid}/items/admissions-faq/edit", json={"replacements": [{"index": 0, "replacement": "A: The fee is INR 48000."}]}, headers=admin(api)).status_code == 409
    A.a_post(api, rid, "items/admissions-faq/review", {"decision": "confirmed"}, expect=409)
    A.a_post(api, rid, "items/fee-archive/review", {"decision": "confirmed"}, expect=409)
    A.a_post(api, rid, "items/admissions-faq/review", {"decision": "excluded", "note": "x"}, expect=409)


def test_exclusion_needs_a_reason_and_removes_the_document_from_publication(api):  # noqa: F811
    rid = make(api)
    A.a_post(api, rid, "start-review")
    A.a_post(api, rid, "items/brochure/review", {"decision": "excluded"}, expect=422)
    it = A.a_post(api, rid, "items/brochure/review", {"decision": "excluded", "note": "brochure is reprinted"})
    assert it["admin_decision"] == "excluded" and it["included"] is False and it["staff_included"] is True
    ap = A.approve(api, rid)
    assert ap["counts"]["included"] == 2 and ap["counts"]["excluded_by_admin"] == 1
    before = snapshot({"brochure"})
    A.publish(api, rid)
    assert snapshot({"brochure"}) == before                                                    # excluded: not even a draft version
    assert "48,000" in "".join(live_texts(api, "Python Full Stack course fee", "python-fees"))


def test_approving_with_nothing_left_is_refused(api):  # noqa: F811
    rid = make(api)
    A.a_post(api, rid, "start-review")
    for d in AFFECTED:
        A.a_post(api, rid, f"items/{d}/review", {"decision": "excluded", "note": "no"})
    assert "nothing is left to publish" in A.approve(api, rid, expect=422)["detail"]


def test_return_for_revision_needs_a_note_and_lets_staff_change_decisions_and_resubmit(api):  # noqa: F811
    rid = make(api)
    A.a_post(api, rid, "start-review")
    A.a_post(api, rid, "review", {"decision": "return"}, expect=422)
    r = A.a_post(api, rid, "review", {"decision": "return", "note": "Please reconsider the FAQ"})
    assert r["status"] == "needs_revision" and r["return_note"] == "Please reconsider the FAQ"
    req = A.get(api, rid)
    assert req["status"] == "needs_revision" and req["return_note"] == "Please reconsider the FAQ"
    A.decide(api, rid, "admissions-faq", "discarded", reason="agreed")
    assert A.submit(api, rid)["status"] == "submitted"
    assert A.a_get(api, rid)["counts"]["discarded"] == 1


def test_reject_ends_the_request_keeps_its_audit_and_publishes_nothing(api):  # noqa: F811
    rid = make(api)
    before = live_state()
    A.a_post(api, rid, "start-review")
    r = A.a_post(api, rid, "review", {"decision": "reject", "note": "wrong fee"})
    assert r["status"] == "rejected" and r["review_note"] == "wrong fee" and r["reviewed_by"] == "admin1"
    assert live_state() == before
    A.publish(api, rid, expect=409)
    A.a_post(api, rid, "review", {"decision": "approve"}, expect=409)
    acts = [e[0] for e in events(rid)]
    assert "change_request_rejected" in acts and "change_request_submitted" in acts and "change_analysis_completed" in acts
    assert api.get(f"{BASE}/changes/{rid}/audit", headers=admin(api)).json()["items"]


def test_cancel_by_staff_keeps_the_history_visible_to_administrators(api):  # noqa: F811
    rid = make(api)
    assert api.post(f"{ST}/changes/{rid}/cancel", headers=staff(api)).json()["status"] == "cancelled"
    assert api.get(f"{BASE}/changes/{rid}", headers=admin(api)).json()["status"] == "cancelled"          # it had been submitted, so administrators keep seeing it
    assert [a for a in api.get(f"{BASE}/changes/{rid}/audit", headers=admin(api)).json()["items"] if a["action"] == "change_request_cancelled"]


def test_cancel_by_an_administrator_after_approval_stops_publication(api):  # noqa: F811
    rid = approved(api)
    assert A.a_post(api, rid, "cancel")["status"] == "cancelled"
    A.publish(api, rid, expect=409)
    A.a_post(api, rid, "cancel", expect=409)


def test_a_withdrawn_draft_never_shows_up_for_administrators(api):  # noqa: F811
    seed(api, only=FEE_DOCS)
    req = A.analysed(api)
    api.post(f"{ST}/changes/{req['request_id']}/cancel", headers=staff(api))
    assert api.get(f"{BASE}/changes/{req['request_id']}", headers=admin(api)).status_code == 404
    assert api.get(f"{BASE}/changes?status=cancelled", headers=admin(api)).json()["total"] == 0


def test_separate_reviewer_rule_is_enforced_for_approval(api):  # noqa: F811
    from tests.knowledge.kbhelp import admin2
    s = A.inline(api)
    s.kb_require_separate_reviewer = True
    try:
        seed(api, only=FEE_DOCS)
        h = admin(api)                                                                       # an administrator who also authors changes
        req = A.analysed(api, h=h)
        A.accept_all(api, req, h=h)
        A.submit(api, req["request_id"], h=h)
        assert A.approve(api, req["request_id"], expect=403)["detail"].startswith("a different administrator")
        assert A.approve(api, req["request_id"], h=admin2(api))["status"] == "approved"
    finally:
        s.kb_require_separate_reviewer = False


def test_rbac_and_tenant_isolation_of_review_and_publication(api):  # noqa: F811
    rid = make(api)
    for h in (staff(api), student(api), {}):
        assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=h).status_code in (401, 403)
        assert api.post(f"{BASE}/changes/{rid}/publish", headers=h).status_code in (401, 403)
    oh = other_admin(api)
    for path, method in (("review", "post"), ("publish", "post"), ("start-review", "post"), ("cancel", "post"), ("reopen", "post")):
        r = getattr(api, method)(f"{BASE}/changes/{rid}/{path}", json={"decision": "approve"} if path == "review" else {}, headers=oh)
        assert r.status_code == 404, (path, r.status_code)
    assert api.get(f"{BASE}/changes/{rid}/audit", headers=oh).status_code == 404
    assert api.put(f"{BASE}/changes/{rid}/items/brochure/edit", json={"content": "x"}, headers=oh).status_code == 404


# ---- release: the happy path --------------------------------------------------------------------------------------------------------------
def test_approval_publishes_nothing(api):  # noqa: F811
    rid = make(api)
    before = (live_state(), snapshot())
    A.a_post(api, rid, "start-review")
    assert A.approve(api, rid)["status"] == "approved"
    assert (live_state(), snapshot()) == before


def test_process_and_publish_makes_every_document_live_as_one_unit(api):  # noqa: F811
    rid = approved(api)
    others_before = {k: v for k, v in live_state().items() if k not in AFFECTED}
    out = A.publish(api, rid)
    req = out["request"]
    assert out["outcome"] == "started" and req["status"] == "published" and req["published_by"] == "admin1" and req["published_at"] and req["failure"] is None
    rows = {i["doc_id"]: i for i in req["items"] if i["included"]}
    assert set(rows) == set(AFFECTED) and all(i["process_status"] == "ready" and i["publish_status"] == "published" and i["result_version"] == 2 for i in rows.values())
    assert req["release"]["versions"] == {d: 2 for d in AFFECTED} and req["release"]["previous"] == {d: 1 for d in AFFECTED} and req["release"]["attempts"] == 1
    st = live_state()
    for d in AFFECTED:
        assert st[d][0] == 2 and "48" in "".join(st[d][2]) and "45" not in "".join(st[d][2]).replace("₹38,000", "")
    assert {k: v for k, v in st.items() if k not in AFFECTED} == others_before                   # the others are byte-for-byte what they were
    with session_scope() as db:
        for d in AFFECTED:
            vs = {v.version_no: v for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == d)).scalars()}
            assert vs[1].status == "superseded" and vs[2].status == "published" and vs[2].created_by == "staff1" and vs[2].published_by == "admin1"
            assert vs[2].change_note.startswith("Python fee increase")


def test_retrieval_serves_the_new_text_and_never_a_discarded_or_unpublished_one(api):  # noqa: F811
    rid = make(api, discard=("admissions-faq",))
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    # before publication retrieval serves only the old text
    assert not any("48" in t for d in AFFECTED for t in live_texts(api, "Python Full Stack fee", d))
    A.publish(api, rid)
    assert any("48,000" in t for t in live_texts(api, "Python Full Stack course fee", "python-fees"))
    assert any("INR 45000" in t for t in live_texts(api, "What is the fee for Python Full Stack?", "admissions-faq"))      # discarded: the old text stays live
    assert not any("INR 48000" in t for t in live_texts(api, "What is the fee for Python Full Stack?", "admissions-faq"))
    with session_scope() as db:
        assert db.execute(select(Chunk).where(Chunk.tenant_id == T, Chunk.text.contains("effective 1 December"), Chunk.doc_id == "admissions-faq")).first() is None


def test_after_publication_the_request_is_complete_and_audited_with_the_final_versions(api):  # noqa: F811
    rid = approved(api)
    A.publish(api, rid)
    acts = [e[0] for e in events(rid)]
    for need in ("change_request_submitted", "change_request_review_started", "change_request_approved", "change_release_started", "change_item_processed", "change_release_published"):
        assert need in acts, need
    assert acts.index("change_request_approved") < acts.index("change_release_started") < acts.index("change_item_processed") < acts.index("change_release_published")
    pub = next(e for e in events(rid) if e[0] == "change_release_published")[2]
    assert set(pub["documents"].split(",")) == {f"{d}@2" for d in AFFECTED} and pub["status"] == "published"
    aud = api.get(f"{BASE}/changes/{rid}/audit", headers=admin(api)).json()["items"]
    assert any(a["action"] == "version_published" and a["doc_id"] == "brochure" and a["version"] == 2 for a in aud)               # the M6 events of the versions it made
    assert SECRET_TEXT not in str(aud) and "48,000" not in str(aud)


def test_publishing_twice_is_idempotent(api):  # noqa: F811
    rid = approved(api)
    A.publish(api, rid)
    snap, evs = snapshot(), len(events(rid))
    out = A.publish(api, rid)
    assert out["outcome"] == "already_published" and out["request"]["status"] == "published"
    assert snapshot() == snap and len(events(rid)) == evs


def test_publish_before_approval_is_refused(api):  # noqa: F811
    rid = make(api)
    A.publish(api, rid, expect=409)
    A.a_post(api, rid, "start-review")
    A.publish(api, rid, expect=409)
    before = snapshot()
    assert snapshot() == before


# ---- release: failure and retry ----------------------------------------------------------------------------------------------------------
def test_one_document_failing_to_process_publishes_nothing_and_retry_finishes_the_job(api):  # noqa: F811
    rid = approved(api)
    before = live_state()
    good = api.app.state.embedder if getattr(api.app.state, "embedder", None) else base_embedder(api)
    api.app.state.embedder = Flaky(good, "Rs. 48,000/-")                                         # only the brochure's new text fails to embed
    out = A.publish(api, rid)["request"]
    assert out["status"] == "failed" and out["failure"]["stage"] == "processing" and out["failure"]["documents"] == ["brochure"]
    by = {i["doc_id"]: i for i in out["items"] if i["included"]}
    assert by["brochure"]["process_status"] == "failed" and by["brochure"]["process_error"] and by["brochure"]["publish_status"] == "pending"
    assert by["python-fees"]["process_status"] == "ready" and by["admissions-faq"]["process_status"] == "ready"
    assert out["release"] is None and out["published_at"] is None
    assert live_state() == before                                                                # nothing partially published
    first_versions = {d: by[d]["result_version"] for d in AFFECTED}
    # retry with a healthy embedder
    api.app.state.embedder = good
    out2 = A.publish(api, rid)["request"]
    assert out2["status"] == "published" and out2["release_attempt"] == 2 and out2["release"]["attempts"] == 2
    assert {i["doc_id"]: i["result_version"] for i in out2["items"] if i["included"]} == first_versions       # versions were reused, not duplicated
    with session_scope() as db:
        assert [v.version_no for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == "brochure").order_by(KbVersion.version_no)).scalars()] == [1, 2]
    acts = [e[0] for e in events(rid)]
    assert "change_release_failed" in acts and "change_release_retried" in acts and acts.count("change_release_started") == 2
    assert next(e for e in events(rid) if e[0] == "change_release_failed")[2]["stage"] == "processing"


def test_a_failure_inside_the_publishing_transaction_rolls_every_document_back(api, monkeypatch):  # noqa: F811
    rid = approved(api)
    before = live_state()
    real = release.pipeline.publish_version
    calls = []

    def flaky(db, tenant, actor, doc_id, version_no, embedder_name, **kw):
        calls.append(doc_id)
        if len(calls) == 3:                                                                      # the first two already did their writes inside the transaction
            raise Conflict("simulated failure while publishing the third document")
        return real(db, tenant, actor, doc_id, version_no, embedder_name, **kw)
    monkeypatch.setattr(release.pipeline, "publish_version", flaky)
    out = A.publish(api, rid)["request"]
    assert len(calls) == 3 and out["status"] == "failed" and out["failure"]["stage"] == "publishing" and "rolled back" in out["failure"]["message"]
    assert live_state() == before                                                                # no mixture: every document still has its old text and version
    with session_scope() as db:
        assert {d.doc_id: d.published_version for d in db.execute(select(KbDocument)).scalars() if d.doc_id in AFFECTED} == {d: 1 for d in AFFECTED}
        assert all(v.status == "ready" for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.version_no == 2, KbVersion.doc_id.in_(AFFECTED))).scalars())
    assert all(i["publish_status"] == "not_published" for i in out["items"] if i["included"])
    monkeypatch.undo()
    out2 = A.publish(api, rid)["request"]
    assert out2["status"] == "published" and live_state() != before
    with session_scope() as db:
        assert db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.version_no == 3)).first() is None                       # no duplicate drafts on retry


def test_an_unexpected_crash_is_recorded_as_a_retryable_failure(api, monkeypatch):  # noqa: F811
    rid = approved(api)
    monkeypatch.setattr(release, "_process_all", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom " + SECRET_TEXT)))
    out = A.publish(api, rid)["request"]
    assert out["status"] == "failed" and out["failure"]["stage"] == "processing" and SECRET_TEXT not in str(out["failure"])
    monkeypatch.undo()
    assert A.publish(api, rid)["request"]["status"] == "published"


def test_a_running_release_cannot_be_started_again_but_a_dead_one_can_be_taken_over(api):  # noqa: F811
    rid = approved(api)
    s = api.app.state.settings
    with session_scope() as db:
        first = release.begin(db, T, "admin1", s, rid)
    assert first == {"outcome": "started", "attempt": 1}
    with pytest.raises(Conflict):
        with session_scope() as db:
            release.begin(db, T, "admin1", s, rid)
    s.kb_release_stale_s = 0
    try:
        with session_scope() as db:
            second = release.begin(db, T, "admin1", s, rid)
        assert second == {"outcome": "started", "attempt": 2}
    finally:
        s.kb_release_stale_s = 900
    emb = base_embedder(api)
    release.run(T, "admin1", rid, 1, s, emb)                                                       # the dead worker wakes up: it writes nothing and publishes nothing
    assert A.a_get(api, rid)["status"] == "processing"
    assert not any(e[0] == "change_release_published" for e in events(rid))
    release.run(T, "admin1", rid, 2, s, emb)
    assert A.a_get(api, rid)["status"] == "published"
    with session_scope() as db:
        assert [v.version_no for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == "python-fees")).scalars()] == [1, 2]


def test_two_administrators_clicking_at_once_start_exactly_one_release(api):  # noqa: F811
    rid = approved(api)
    s = api.app.state.settings
    results, barrier = [], threading.Barrier(2)

    def click():
        barrier.wait()
        try:
            with session_scope() as db:
                results.append(release.begin(db, T, "admin1", s, rid)["outcome"])
        except Conflict:
            results.append("conflict")
        except Exception as e:                                                                    # noqa: BLE001  (SQLite may report a locked database instead of waiting)
            results.append("conflict" if "locked" in str(e).lower() else repr(e))
    ts = [threading.Thread(target=click) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["conflict", "started"], results
    assert A.a_get(api, rid)["release_attempt"] == 1


# ---- staleness and overlapping requests ---------------------------------------------------------------------------------------------------
def test_approval_is_blocked_when_a_document_moved_on_since_submission(api):  # noqa: F811
    rid = make(api)
    republish(api, "brochure", DOCS["brochure"][1] + "\nNew line.\n")
    A.a_post(api, rid, "start-review")
    r = A.approve(api, rid, expect=409)
    assert "brochure" in r["detail"]
    assert A.item(A.a_get(api, rid), "brochure")["base_stale"] is True
    A.a_post(api, rid, "items/brochure/review", {"decision": "excluded", "note": "changed since"})
    assert A.approve(api, rid)["status"] == "approved"


def test_a_document_changed_between_approval_and_publication_fails_validation_and_can_be_reviewed_again(api):  # noqa: F811
    rid = approved(api)
    before = live_state()
    republish(api, "brochure", DOCS["brochure"][1] + "\nNew line.\n")
    mid = live_state()
    out = A.publish(api, rid)["request"]
    assert out["status"] == "failed" and out["failure"]["stage"] == "validation" and out["failure"]["documents"] == ["brochure"]
    assert live_state() == mid and {k: v for k, v in mid.items() if k != "brochure"} == {k: v for k, v in before.items() if k != "brochure"}
    by = {i["doc_id"]: i for i in out["items"] if i["included"]}
    assert "stale" in by["brochure"]["process_error"] and by["brochure"]["process_status"] == "failed"
    assert A.publish(api, rid)["request"]["status"] == "failed"                                     # retrying unchanged fails the same way: no silent stale patch
    assert A.a_post(api, rid, "reopen")["status"] == "under_review"
    A.a_post(api, rid, "items/brochure/review", {"decision": "excluded", "note": "changed since"})
    A.approve(api, rid)
    done = A.publish(api, rid)["request"]
    assert done["status"] == "published" and set(done["release"]["versions"]) == {"python-fees", "admissions-faq"}
    assert live_state()["brochure"] == mid["brochure"]


def test_overlapping_requests_second_one_cannot_overwrite_the_first(api):  # noqa: F811
    seed(api, only=FEE_DOCS)
    a = A.analysed(api)
    b = A.analysed(api, "The Python Full Stack course fee changed from ₹45,000 to ₹49,000.", h=staff(api, "staff2"))
    A.accept_all(api, a)
    A.accept_all(api, b, h=staff(api, "staff2"))
    A.submit(api, a["request_id"])
    A.submit(api, b["request_id"], h=staff(api, "staff2"))
    ad = A.a_get(api, a["request_id"])
    ov = A.item(ad, "python-fees")["overlapping_requests"]
    assert [o["request_id"] for o in ov] == [b["request_id"]] and ov[0]["status"] == "submitted"
    A.a_post(api, a["request_id"], "start-review")
    A.approve(api, a["request_id"])
    A.a_post(api, b["request_id"], "start-review")
    A.approve(api, b["request_id"])                                                                 # both approved while both bases are still current
    assert A.publish(api, a["request_id"])["request"]["status"] == "published"
    out = A.publish(api, b["request_id"])["request"]
    assert out["status"] == "failed" and out["failure"]["stage"] == "validation"                    # the second sees that the live text moved on
    assert "48" in live_state()["python-fees"][2][0] + "".join(live_state()["python-fees"][2]) and "49,000" not in "".join(live_state()["python-fees"][2])


# ---- legacy and compatibility -----------------------------------------------------------------------------------------------------------------
def test_the_part1_apply_action_still_makes_draft_versions_only_for_included_items(api):  # noqa: F811
    rid = make(api, discard=("admissions-faq",))
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    out = A.a_post(api, rid, "apply")
    assert out["status"] == "applied"
    done = {i["doc_id"]: i["result_version"] for i in out["items"]}
    assert done["python-fees"] == 2 and done["brochure"] == 2 and done["admissions-faq"] is None
    assert not any("48,000" in t for _, t in live_chunks("python-fees"))                           # drafts only: still not live


def test_manual_part1_requests_still_flow_through_the_same_review(api):  # noqa: F811
    from tests.knowledge.kwhelp import EDITED, drafted, live_doc, submit
    A.inline(api)
    live_doc(api)
    rid, _ = drafted(api)
    submit(api, rid)
    ad = A.a_get(api, rid)
    assert ad["status"] == "submitted" and ad["counts"]["manual_items"] == 1 and ad["counts"]["included"] == 1
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    assert A.publish(api, rid)["request"]["status"] == "published"
    assert any("three instalments" in t for _, t in live_chunks("fees-test"))


# ---- the real (background) execution path -----------------------------------------------------------------------------------------------------
def wait_for(fn, seconds=30):
    import time
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting for the background job")


def test_analysis_and_release_run_in_the_background_and_are_pollable(api):  # noqa: F811
    seed(api, only=FEE_DOCS)
    s = api.app.state.settings
    s.kb_ai_mode, s.kb_ai_background = "rules", True
    try:
        rid = A.start_ai(api)["request_id"]
        r = api.post(f"{ST}/changes/{rid}/analyse", json={}, headers=staff(api))
        assert r.status_code == 202 and r.json()["request"]["status"] in ("analysing", "awaiting_decisions")
        req = wait_for(lambda: (g := A.get(api, rid))["status"] == "awaiting_decisions" and g)
        assert req["analysis"]["stage"] == "done" and req["analysis"]["total"] == req["analysis"]["done"]
        A.accept_all(api, req)
        A.submit(api, rid)
        A.a_post(api, rid, "start-review")
        A.approve(api, rid)
        out = A.publish(api, rid)
        assert out["outcome"] == "started" and out["request"]["status"] in ("processing", "publishing", "published")
        done = wait_for(lambda: (g := A.a_get(api, rid))["status"] in ("published", "failed") and g)
        assert done["status"] == "published" and all(i["publish_status"] == "published" for i in done["items"] if i["included"])
    finally:
        s.kb_ai_background = False
