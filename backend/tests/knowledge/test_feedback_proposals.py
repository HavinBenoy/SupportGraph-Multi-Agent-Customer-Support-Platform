"""M6 feedback -> knowledge: approved feedback is permission to START an update, never to publish one."""

import threading

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import Chunk, Document, InterventionFeedback, KbDocument, KbProposal, KbVersion
from app.db.session import session_scope
from app.knowledge import proposals
from app.knowledge.errors import Conflict
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import (BASE, FEES_V1, FEES_V2, admin, admin2, create, go_live, live_chunks, live_doc, mk_feedback, process, publish, retrieve_ids)
from tests.rag.conftest import embedder, ingested  # noqa: F401

NEW = {"doc_id": "from-feedback", "title": "Instalment rule", "category": "fees", "tags": ["feedback"]}


def propose(api, call_id, h=None, expect=201, **kw):
    body = {"call_id": call_id, "content": FEES_V2, **kw}
    r = api.post(f"{BASE}/proposals", json=body, headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def review(api, call_id, decision="approve", h=None, expect=200, note=None):
    r = api.post(f"{BASE}/proposals/{call_id}/review", json={"decision": decision, "note": note}, headers=h or admin(api))
    assert r.status_code == expect, r.text
    return r.json()


def state():
    with session_scope() as db:
        return (db.execute(select(func.count()).select_from(Chunk)).scalar(), db.execute(select(func.count()).select_from(Document)).scalar())


def test_only_approved_feedback_is_listed_and_unprocessed_filter_hides_the_ones_already_used(api):  # noqa: F811
    mk_feedback("c-ok", "approved_for_update")
    mk_feedback("c-pending", "pending_review")
    mk_feedback("c-rej", "rejected")
    h = admin(api)
    got = api.get(f"{BASE}/feedback", headers=h).json()
    assert [i["call_id"] for i in got["items"]] == ["c-ok"] and got["items"][0]["proposal"] is None and got["items"][0]["category"] == "incorrect_information"
    propose(api, "c-ok", new_document=NEW)
    assert api.get(f"{BASE}/feedback?only_unprocessed=true", headers=h).json()["total"] == 0
    assert api.get(f"{BASE}/feedback", headers=h).json()["items"][0]["proposal"]["status"] == "proposed"


@pytest.mark.parametrize("status", ["pending_review", "rejected", "reviewed"])
def test_feedback_that_is_not_approved_cannot_start_a_proposal(api, status):  # noqa: F811
    mk_feedback("c-x", status)
    r = api.post(f"{BASE}/proposals", json={"call_id": "c-x", "content": FEES_V2, "new_document": NEW}, headers=admin(api))
    assert r.status_code == 409 and api.get(f"{BASE}/documents", headers=admin(api)).json()["total"] == 0


def test_unknown_feedback_is_404_and_a_bad_target_is_422(api):  # noqa: F811
    mk_feedback("c-1")
    assert api.post(f"{BASE}/proposals", json={"call_id": "nope", "content": FEES_V2, "new_document": NEW}, headers=admin(api)).status_code == 404
    assert api.post(f"{BASE}/proposals", json={"call_id": "c-1", "content": FEES_V2}, headers=admin(api)).status_code == 422                       # no target
    assert api.post(f"{BASE}/proposals", json={"call_id": "c-1", "content": FEES_V2, "doc_id": "x", "new_document": NEW}, headers=admin(api)).status_code == 422  # two targets
    assert api.post(f"{BASE}/proposals", json={"call_id": "c-1", "content": FEES_V2, "doc_id": "missing"}, headers=admin(api)).status_code == 404


def test_a_proposal_creates_only_a_draft_and_changes_nothing_that_customers_can_see(api):  # noqa: F811
    mk_feedback("c-1")
    before = state()
    r = propose(api, "c-1", new_document=NEW, change_note="from the call")
    assert r["proposal"]["status"] == "proposed" and r["proposal"]["proposed_by"] == "admin1" and r["version"]["status"] == "draft"
    assert state() == before and live_doc("from-feedback") is None
    assert not [1 for d, *_ in retrieve_ids(api, "programme fee three instalments") if d == "from-feedback"]
    d = api.get(f"{BASE}/documents/from-feedback", headers=admin(api)).json()
    assert d["status"] == "draft" and d["created_by"] == "admin1"
    v = api.get(f"{BASE}/documents/from-feedback/versions/1", headers=admin(api)).json()
    assert v["proposal"] == {"call_id": "c-1", "status": "proposed"} and v["change_note"] == "from the call"


def test_a_proposal_for_an_existing_document_adds_a_new_version_and_leaves_the_published_one_live(api):  # noqa: F811
    go_live(api)
    mk_feedback("c-1")
    r = propose(api, "c-1", doc_id="fees-test")
    assert r["version"]["version"] == 2
    assert live_doc("fees-test")["kb_version"] == 1 and any("two instalments" in t for d, _v, _s, t in retrieve_ids(api, "programme fee two instalments") if d == "fees-test")


def test_a_proposed_version_cannot_be_published_until_a_human_approves_the_proposal(api):  # noqa: F811
    mk_feedback("c-1")
    propose(api, "c-1", new_document=NEW)
    process(api, "from-feedback", 1)
    assert "approve" in publish(api, "from-feedback", 1, expect=409)["detail"].lower()
    assert live_doc("from-feedback") is None
    review(api, "c-1", "approve", note="looks right")
    publish(api, "from-feedback", 1)
    assert live_doc("from-feedback")["kb_version"] == 1


def test_a_rejected_proposal_can_never_be_published_or_reviewed_again(api):  # noqa: F811
    mk_feedback("c-1")
    propose(api, "c-1", new_document=NEW)
    process(api, "from-feedback", 1)
    assert review(api, "c-1", "reject", note="not accurate")["status"] == "rejected"
    assert "rejected" in publish(api, "from-feedback", 1, expect=409)["detail"]
    review(api, "c-1", "approve", expect=409)
    assert live_doc("from-feedback") is None


def test_approval_alone_publishes_nothing(api):  # noqa: F811
    mk_feedback("c-1")
    propose(api, "c-1", new_document=NEW)
    process(api, "from-feedback", 1)
    before = state()
    assert review(api, "c-1", "approve")["status"] == "approved"
    assert state() == before and live_doc("from-feedback") is None


def test_the_proposer_reviewer_and_publisher_are_all_recorded_and_the_feedback_row_is_unchanged(api):  # noqa: F811
    mk_feedback("c-1")
    with session_scope() as db:
        fb0 = {c.name: getattr(db.get(InterventionFeedback, (T, "c-1")), c.name) for c in InterventionFeedback.__table__.columns}
    h1, h2 = admin(api), admin2(api)
    propose(api, "c-1", h=h1, new_document=NEW)
    process(api, "from-feedback", 1, h=h1)
    review(api, "c-1", "approve", h=h2, note="ok")
    publish(api, "from-feedback", 1, h=h2)
    p = api.get(f"{BASE}/proposals", headers=h1).json()["items"][0]
    assert (p["status"], p["proposed_by"], p["reviewed_by"], p["published_by"], p["review_note"]) == ("published", "admin1", "admin2", "admin2", "ok") and p["published_at"]
    assert api.get(f"{BASE}/proposals?status=published", headers=h1).json()["total"] == 1 and api.get(f"{BASE}/proposals?status=proposed", headers=h1).json()["total"] == 0
    fb = api.get(f"{BASE}/feedback", headers=h1).json()["items"][0]
    assert fb["proposal"]["status"] == "published" and fb["written_by"] == "staff1" and fb["what_was_wrong"]
    with session_scope() as db:
        fb1 = {c.name: getattr(db.get(InterventionFeedback, (T, "c-1")), c.name) for c in InterventionFeedback.__table__.columns}
    assert fb0 == fb1                                                                                              # the original record, its review status and history are untouched


def test_one_feedback_record_can_start_only_one_proposal(api):  # noqa: F811
    mk_feedback("c-1")
    propose(api, "c-1", new_document=NEW)
    propose(api, "c-1", expect=409, new_document={**NEW, "doc_id": "second"})
    assert api.get(f"{BASE}/documents/second", headers=admin(api)).status_code == 404
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(KbProposal)).scalar() == 1


def test_concurrent_proposals_for_the_same_feedback_create_exactly_one(api):  # noqa: F811
    mk_feedback("c-1")
    s, out = Settings(), []

    def go(i):
        try:
            with session_scope() as db:
                proposals.create_proposal(db, T, f"admin{i}", s, "c-1", content=FEES_V2, new_document={"doc_id": f"doc-{i}", "title": "T", "category": "fees"})
            out.append("ok")
        except Conflict:
            out.append("conflict")
        except Exception as e:                       # noqa: BLE001
            out.append(repr(e))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("ok") == 1 and out.count("conflict") == 3, out
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(KbProposal)).scalar() == 1
        assert db.execute(select(func.count()).select_from(KbDocument)).scalar() == 1                               # the losers' drafts were rolled back, not left behind


def test_separate_reviewer_can_be_required(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api.app.state.settings, "kb_require_separate_reviewer", True)
    mk_feedback("c-1")
    propose(api, "c-1", new_document=NEW)
    assert review(api, "c-1", "approve", expect=403)
    assert review(api, "c-1", "approve", h=admin2(api))["reviewed_by"] == "admin2"


def test_the_feedback_review_flow_itself_never_touches_the_knowledge_tables(api):  # noqa: F811
    go_live(api)
    before = state()
    mk_feedback("c-1", "pending_review")
    with session_scope() as db:
        db.get(InterventionFeedback, (T, "c-1")).review_status = "approved_for_update"
    assert state() == before and live_doc("fees-test")["kb_version"] == 1
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(KbProposal)).scalar() == 0


def test_a_proposal_cannot_target_another_tenants_feedback(api):  # noqa: F811
    from tests.knowledge.kbhelp import OTHER, other_admin
    oh = other_admin(api)
    mk_feedback("c-1")
    assert api.post(f"{BASE}/proposals", json={"call_id": "c-1", "content": FEES_V2, "new_document": NEW}, headers=oh).status_code == 404
    assert api.get(f"{BASE}/feedback", headers=oh).json()["total"] == 0 and api.get(f"{BASE}/proposals", headers=oh).json()["total"] == 0
    assert api.post(f"{BASE}/proposals/c-1/review", json={"decision": "approve"}, headers=oh).status_code == 404


def test_review_input_is_validated_and_unknown_proposals_are_404(api):  # noqa: F811
    mk_feedback("c-1")
    propose(api, "c-1", new_document=NEW)
    assert api.post(f"{BASE}/proposals/c-1/review", json={"decision": "maybe"}, headers=admin(api)).status_code == 422
    assert api.post(f"{BASE}/proposals/c-1/review", json={}, headers=admin(api)).status_code == 422
    assert api.post(f"{BASE}/proposals/none/review", json={"decision": "approve"}, headers=admin(api)).status_code == 404
    assert api.get(f"{BASE}/proposals?status=zzz", headers=admin(api)).status_code == 422
