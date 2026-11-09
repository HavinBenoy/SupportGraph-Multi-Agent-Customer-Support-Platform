"""Manual change workflow: drafts, structured diffs, submit, review, apply, conflicts, audit, isolation, M6 compatibility."""

import json
import threading

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import Chunk, KbChangeItem, KbDocument, KbEvent, KbVersion
from app.db.session import session_scope
from app.knowledge import changes
from app.knowledge.errors import Conflict
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge.kbhelp import BASE, FEES_V1, SECRET_TEXT, add_version, admin, admin2, live_chunks, other_admin, process, publish, retrieve_ids
from tests.knowledge.kwhelp import EDITED, ST, drafted, live_doc, live_texts, save, staff, start, student, submit
from tests.rag.conftest import embedder, ingested  # noqa: F401


def snapshot(doc_id="fees-test"):
    with session_scope() as db:
        d = db.get(KbDocument, (T, doc_id))
        vs = [(v.version_no, v.status, v.checksum) for v in db.execute(select(KbVersion).where(KbVersion.doc_id == doc_id).order_by(KbVersion.version_no)).scalars()]
        return (d.published_version, d.latest_version, d.status, vs, live_chunks(doc_id))


# ---- start / save / diff ----------------------------------------------------------------------------------------------------------
def test_make_changes_creates_a_draft_that_preserves_the_original_and_shows_no_diff_yet(api):  # noqa: F811
    live_doc(api)
    out = start(api)
    r = out["request"]
    assert out["existing"] is False and r["status"] == "draft" and r["created_by"] == "staff1" and r["source"] == "manual"
    it = r["items"][0]
    assert it["base_version"] == 1 and it["proposed_content"] == FEES_V1 and it["stats"]["added_chars"] == 0 and {s["op"] for s in it["diff"]["body"]} == {"equal"}


def test_make_changes_twice_returns_the_same_draft(api):  # noqa: F811
    live_doc(api)
    a, b = start(api), start(api)
    assert b["existing"] is True and a["request"]["request_id"] == b["request"]["request_id"]
    other = start(api, h=staff(api, "staff2"))
    assert other["request"]["request_id"] != a["request"]["request_id"]


def test_only_published_documents_can_be_edited(api):  # noqa: F811
    from tests.knowledge.kbhelp import create
    create(api, doc_id="unpub")
    assert api.post(f"{ST}/changes", json={"doc_id": "unpub"}, headers=staff(api)).status_code == 409
    assert api.post(f"{ST}/changes", json={"doc_id": "missing"}, headers=staff(api)).status_code == 404


def test_saving_an_edit_stores_structured_segments_not_html_and_leaves_published_knowledge_untouched(api):  # noqa: F811
    live_doc(api)
    before, retr = snapshot(), retrieve_ids(api, "instalments")
    rid, it = drafted(api)
    ops = {s["op"] for s in it["diff"]["body"]}
    assert ops == {"equal", "delete", "insert"}
    assert any("two" in s["text"] for s in it["diff"]["body"] if s["op"] == "delete")
    assert any("three" in s["text"] for s in it["diff"]["body"] if s["op"] == "insert")
    assert all(set(s) == {"op", "text"} for s in it["diff"]["body"])
    with session_scope() as db:
        row = db.get(KbChangeItem, (T, rid, "fees-test"))
        assert set(row.diff) == {"title", "body"} and all(set(x) == {"op", "text"} for x in row.diff["body"])
        assert row.proposed_content == EDITED and row.base_version == 1
    assert snapshot() == before
    assert retrieve_ids(api, "instalments") == retr


def test_draft_persists_and_reloads_with_the_saved_edit(api):  # noqa: F811
    live_doc(api)
    rid, _ = drafted(api)
    got = api.get(f"{ST}/changes/{rid}", headers=staff(api)).json()
    assert got["items"][0]["proposed_content"] == EDITED and got["status"] == "draft"
    lst = api.get(f"{ST}/changes", headers=staff(api)).json()
    assert [r["request_id"] for r in lst["items"]] == [rid]


def test_saving_again_recomputes_the_diff_against_the_original_not_the_previous_draft(api):  # noqa: F811
    live_doc(api)
    rid, it = drafted(api)
    again = save(api, rid, "fees-test", FEES_V1, it["row_version"])               # back to the original text
    assert again["stats"]["added_chars"] == 0 and again["stats"]["deleted_chars"] == 0
    assert again["row_version"] == it["row_version"] + 1


def test_preview_returns_the_same_diff_and_writes_nothing(api):  # noqa: F811
    live_doc(api)
    with session_scope() as db:
        n = len(db.execute(select(KbChangeItem)).all())
    r = api.post(f"{ST}/diff-preview", json={"doc_id": "fees-test", "content": EDITED}, headers=staff(api))
    assert r.status_code == 200 and r.json()["base_version"] == 1 and r.json()["unchanged"] is False
    assert api.post(f"{ST}/diff-preview", json={"doc_id": "fees-test", "content": FEES_V1}, headers=staff(api)).json()["unchanged"] is True
    with session_scope() as db:
        assert len(db.execute(select(KbChangeItem)).all()) == n


@pytest.mark.parametrize("content", ["", "   ", "---\nfront: matter\n---\nbody", "bad\x00control"])
def test_invalid_edit_text_is_rejected_with_the_m6_rules(api, content):  # noqa: F811
    live_doc(api)
    rid = start(api)["request"]
    assert api.put(f"{ST}/changes/{rid['request_id']}/items/fees-test", json={"content": content, "expected_row_version": 0}, headers=staff(api)).status_code == 422


def test_multi_document_requests_hold_one_item_per_document(api):  # noqa: F811
    live_doc(api, "doc-a")
    live_doc(api, "doc-b")
    out = start(api, "doc-a")
    rid = out["request"]["request_id"]
    save(api, rid, "doc-a", FEES_V1.replace("two", "five"), 0)
    added = api.put(f"{ST}/changes/{rid}/items/doc-b", json={"content": FEES_V1.replace("two", "six")}, headers=staff(api))
    assert added.status_code == 200 and added.json()["row_version"] == 0
    got = api.get(f"{ST}/changes/{rid}", headers=staff(api)).json()
    assert sorted(i["doc_id"] for i in got["items"]) == ["doc-a", "doc-b"] and got["document_count"] == 2
    rem = api.post(f"{ST}/changes/{rid}/items/doc-b/remove", headers=staff(api))
    assert rem.status_code == 200 and rem.json()["document_count"] == 1
    assert api.post(f"{ST}/changes/{rid}/items/doc-b/remove", headers=staff(api)).status_code == 404


# ---- concurrency ------------------------------------------------------------------------------------------------------------------
def test_second_window_with_an_old_token_gets_409_and_does_not_overwrite(api):  # noqa: F811
    live_doc(api)
    rid, it = drafted(api)
    save(api, rid, "fees-test", EDITED + "\nWindow one line.\n", it["row_version"])
    stale = api.put(f"{ST}/changes/{rid}/items/fees-test", json={"content": EDITED + "\nWindow two line.\n", "expected_row_version": it["row_version"]}, headers=staff(api))
    assert stale.status_code == 409
    assert "Window one line" in api.get(f"{ST}/changes/{rid}", headers=staff(api)).json()["items"][0]["proposed_content"]


def test_saving_over_an_existing_edit_requires_the_token(api):  # noqa: F811
    live_doc(api)
    rid, _ = drafted(api)
    assert api.put(f"{ST}/changes/{rid}/items/fees-test", json={"content": EDITED}, headers=staff(api)).status_code == 422


def test_parallel_saves_with_the_same_token_exactly_one_wins(api):  # noqa: F811
    live_doc(api)
    rid, it = drafted(api)
    s, wins, losses, other = Settings(), [], [], []

    def go(i):
        try:
            with session_scope() as db:
                changes.save_item(db, T, "staff1", s, rid, "fees-test", content=EDITED + f"\nParallel {i}\n", expected_row_version=it["row_version"])
            wins.append(i)
        except Conflict:
            losses.append(i)
        except Exception as e:                                  # noqa: BLE001
            other.append(repr(e))
    ts = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not other, other
    assert len(wins) == 1 and len(losses) == 4


def test_two_staff_editing_the_same_document_have_independent_private_drafts(api):  # noqa: F811
    live_doc(api)
    r1, _ = drafted(api, h=staff(api))
    r2, _ = drafted(api, content=FEES_V1.replace("two", "ten"), h=staff(api, "staff2"))
    assert r1 != r2
    assert "three" in api.get(f"{ST}/changes/{r1}", headers=staff(api)).json()["items"][0]["proposed_content"]
    assert "ten" in api.get(f"{ST}/changes/{r2}", headers=staff(api, "staff2")).json()["items"][0]["proposed_content"]
    assert api.get(f"{ST}/changes/{r1}", headers=staff(api, "staff2")).status_code == 404       # someone else's draft is invisible
    assert api.put(f"{ST}/changes/{r1}/items/fees-test", json={"content": EDITED, "expected_row_version": 1}, headers=staff(api, "staff2")).status_code == 404
    assert api.post(f"{ST}/changes/{r1}/cancel", headers=staff(api, "staff2")).status_code == 404


def test_description_updates_are_cas_checked(api):  # noqa: F811
    live_doc(api)
    rid, _ = drafted(api)
    cur = api.get(f"{ST}/changes/{rid}", headers=staff(api)).json()["row_version"]
    ok = api.put(f"{ST}/changes/{rid}", json={"description": "New fee rule", "expected_row_version": cur}, headers=staff(api))
    assert ok.status_code == 200 and ok.json()["description"] == "New fee rule"
    assert api.put(f"{ST}/changes/{rid}", json={"description": "x", "expected_row_version": cur}, headers=staff(api)).status_code == 409


# ---- submit -----------------------------------------------------------------------------------------------------------------------
def test_submit_requires_a_description_and_a_real_change(api):  # noqa: F811
    live_doc(api)
    out = start(api)
    rid = out["request"]["request_id"]
    assert api.post(f"{ST}/changes/{rid}/submit", json={}, headers=staff(api)).status_code == 422                  # no description
    assert api.post(f"{ST}/changes/{rid}/submit", json={"description": "nothing changed"}, headers=staff(api)).status_code == 422


def test_submit_moves_to_submitted_without_touching_published_knowledge(api):  # noqa: F811
    live_doc(api)
    before, retr = snapshot(), retrieve_ids(api, "instalments")
    rid, _ = drafted(api)
    sub = submit(api, rid)
    assert sub["status"] == "submitted" and sub["submitted_by"] == "staff1" and sub["description"] == "Update the instalment rule"
    assert snapshot() == before and retrieve_ids(api, "instalments") == retr
    assert api.put(f"{ST}/changes/{rid}/items/fees-test", json={"content": EDITED, "expected_row_version": 1}, headers=staff(api)).status_code == 409
    submit(api, rid, expect=409)


def test_unchanged_items_are_dropped_on_submit(api):  # noqa: F811
    live_doc(api, "doc-a")
    live_doc(api, "doc-b")
    rid = start(api, "doc-a")["request"]["request_id"]
    save(api, rid, "doc-a", FEES_V1.replace("two", "five"), 0)
    api.put(f"{ST}/changes/{rid}/items/doc-b", json={"content": FEES_V1}, headers=staff(api))      # same as published
    sub = submit(api, rid)
    assert [i["doc_id"] for i in sub["items"]] == ["doc-a"]


def test_submit_is_refused_when_the_published_version_moved_on(api):  # noqa: F811
    live_doc(api)
    rid, _ = drafted(api)
    add_version(api, "fees-test", FEES_V1.replace("two", "seven"), "other")
    process(api, "fees-test", 2)
    publish(api, "fees-test", 2)
    r = submit(api, rid, expect=409)
    assert "fees-test" in json.dumps(r)
    got = api.get(f"{ST}/changes/{rid}", headers=staff(api)).json()
    assert got["status"] == "draft" and got["items"][0]["base_stale"] is True


def test_cancel_a_draft_and_a_submitted_request(api):  # noqa: F811
    live_doc(api)
    rid, _ = drafted(api)
    assert api.post(f"{ST}/changes/{rid}/cancel", headers=staff(api)).json()["status"] == "cancelled"
    assert api.post(f"{ST}/changes/{rid}/cancel", headers=staff(api)).status_code == 409
    rid2 = start(api)["request"]["request_id"]                                    # a fresh draft after the old one was cancelled
    assert rid2 != rid


# ---- admin review / apply ---------------------------------------------------------------------------------------------------------
def submitted(api, doc_id="fees-test"):
    rid, _ = drafted(api, doc_id)
    submit(api, rid)
    return rid


def test_admin_sees_submitted_requests_with_diffs_but_never_drafts(api):  # noqa: F811
    live_doc(api)
    draft, _ = drafted(api, h=staff(api, "staff2"))
    rid = submitted(api)
    lst = api.get(f"{BASE}/changes", headers=admin(api)).json()
    assert [r["request_id"] for r in lst["items"]] == [rid]
    got = api.get(f"{BASE}/changes/{rid}", headers=admin(api)).json()
    assert got["items"][0]["diff"]["body"] and got["created_by"] == "staff1"
    assert api.get(f"{BASE}/changes/{draft}", headers=admin(api)).status_code == 404
    assert api.get(f"{BASE}/changes?status=draft", headers=admin(api)).status_code == 422
    assert api.get(f"{BASE}/changes?status=bogus", headers=admin(api)).status_code == 422


def test_approve_does_not_change_published_knowledge_and_apply_creates_draft_versions_only(api):  # noqa: F811
    live_doc(api)
    before, retr = snapshot(), retrieve_ids(api, "instalments")
    rid = submitted(api)
    ap = api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve", "note": "ok"}, headers=admin(api))
    assert ap.status_code == 200 and ap.json()["status"] == "approved" and ap.json()["reviewed_by"] == "admin1"
    assert snapshot() == before
    done = api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api))
    assert done.status_code == 200 and done.json()["status"] == "applied" and done.json()["items"][0]["result_version"] == 2
    pv, latest, status, versions, chunks = snapshot()
    assert pv == 1 and latest == 2 and status == "published"                       # still the old version live
    assert versions[1][1] == "draft" and versions[0][1] == "published"
    assert chunks == before[4] and retrieve_ids(api, "instalments") == retr
    v2 = api.get(f"{BASE}/documents/fees-test/versions/2", headers=admin(api)).json()
    assert v2["content"] == EDITED and v2["created_by"] == "staff1" and v2["change_note"] == "Update the instalment rule"
    process(api, "fees-test", 2)
    publish(api, "fees-test", 2)                                                   # only the ordinary M6 publish makes it live
    assert any("three instalments" in t for t in live_texts(api, "instalments", "fees-test"))


def test_reject_leaves_everything_alone_and_cannot_be_applied(api):  # noqa: F811
    live_doc(api)
    before = snapshot()
    rid = submitted(api)
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "reject", "note": "no"}, headers=admin(api)).json()["status"] == "rejected"
    assert api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api)).status_code == 409
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=admin(api)).status_code == 409
    assert snapshot() == before


def test_apply_requires_approval_and_is_not_repeatable(api):  # noqa: F811
    live_doc(api)
    rid = submitted(api)
    assert api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api)).status_code == 409
    api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=admin(api))
    assert api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api)).status_code == 200
    assert api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api)).status_code == 409
    with session_scope() as db:
        assert len(db.execute(select(KbVersion).where(KbVersion.doc_id == "fees-test")).all()) == 2


def test_apply_is_all_or_nothing_when_one_document_went_stale(api):  # noqa: F811
    live_doc(api, "doc-a")
    live_doc(api, "doc-b")
    rid = start(api, "doc-a")["request"]["request_id"]
    save(api, rid, "doc-a", FEES_V1.replace("two", "five"), 0)
    api.put(f"{ST}/changes/{rid}/items/doc-b", json={"content": FEES_V1.replace("two", "six")}, headers=staff(api))
    submit(api, rid)
    api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=admin(api))
    add_version(api, "doc-b", FEES_V1.replace("two", "eight"), "meanwhile")
    process(api, "doc-b", 2)
    publish(api, "doc-b", 2)
    assert api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api)).status_code == 409
    with session_scope() as db:
        assert len(db.execute(select(KbVersion).where(KbVersion.doc_id == "doc-a")).all()) == 1       # nothing was created for doc-a either
    assert api.get(f"{BASE}/changes/{rid}", headers=admin(api)).json()["status"] == "approved"


def test_separate_reviewer_rule_does_not_apply_to_staff_authored_requests_but_is_enforced_by_service(api):  # noqa: F811
    live_doc(api)
    rid = submitted(api)
    s = Settings()
    s.kb_require_separate_reviewer = True
    with session_scope() as db:
        from app.knowledge.errors import Forbidden
        db.get(__import__("app.db.models", fromlist=["x"]).KbChangeRequest, (T, rid)).submitted_by = "admin1"
        db.flush()
        with pytest.raises(Forbidden):
            changes.review(db, T, "admin1", s, rid, "approve")
        db.rollback()


def test_review_with_stale_request_token_conflicts(api):  # noqa: F811
    live_doc(api)
    rid = submitted(api)
    cur = api.get(f"{BASE}/changes/{rid}", headers=admin(api)).json()["row_version"]
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve", "expected_row_version": cur + 3}, headers=admin(api)).status_code == 409
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve", "expected_row_version": cur}, headers=admin(api)).status_code == 200


# ---- audit ------------------------------------------------------------------------------------------------------------------------
def test_every_step_is_audited_and_no_text_ever_reaches_the_audit_trail(api):  # noqa: F811
    secret_edit = EDITED + f"\nInternal note {SECRET_TEXT} stays out of the audit.\n"
    live_doc(api)
    out = start(api)
    rid = out["request"]["request_id"]
    save(api, rid, "fees-test", secret_edit, 0)
    submit(api, rid, desc=f"Because {SECRET_TEXT} said so")
    api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve", "note": SECRET_TEXT}, headers=admin(api))
    api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api))
    with session_scope() as db:
        evs = list(db.execute(select(KbEvent).where(KbEvent.tenant_id == T).order_by(KbEvent.id)).scalars())
    acts = [e.action for e in evs if e.resource_id == rid]
    assert acts == ["change_request_created", "change_draft_saved", "change_request_submitted", "change_request_approved", "change_request_applied"]
    assert SECRET_TEXT not in json.dumps([e.summary for e in evs])
    assert {e.actor for e in evs if e.resource_id == rid} == {"staff1", "admin1"}
    applied = [e for e in evs if e.action == "change_request_applied"][0]
    assert applied.summary["base_version"] == 1 and applied.summary["result_version"] == 2


# ---- permissions / isolation ------------------------------------------------------------------------------------------------------
def test_students_and_other_tenants_cannot_touch_requests(api):  # noqa: F811
    live_doc(api)
    rid = submitted(api)
    assert api.get(f"{ST}/changes/{rid}", headers=student(api)).status_code == 403
    oh = other_admin(api)
    assert api.get(f"{BASE}/changes/{rid}", headers=oh).status_code == 404
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=oh).status_code == 404
    assert api.post(f"{BASE}/changes/{rid}/apply", headers=oh).status_code == 404
    assert api.get(f"{BASE}/changes", headers=oh).json()["items"] == []
    assert api.post(f"{ST}/diff-preview", json={"doc_id": "fees-test", "content": "a b c"}, headers=oh).status_code == 404   # an admin passes staff routes, but only inside its own tenant


def test_staff_of_another_tenant_cannot_start_changes_on_this_tenants_documents(api):  # noqa: F811
    from tests.knowledge.kwhelp import other_staff
    live_doc(api)
    h = other_staff(api)
    assert api.post(f"{ST}/changes", json={"doc_id": "fees-test"}, headers=h).status_code == 404
    assert api.post(f"{ST}/diff-preview", json={"doc_id": "fees-test", "content": "a b c"}, headers=h).status_code == 404
    assert api.get(f"{ST}/documents/fees-test/versions", headers=h).status_code == 404


def test_extra_fields_and_bad_ids_are_rejected(api):  # noqa: F811
    live_doc(api)
    assert api.post(f"{ST}/changes", json={"doc_id": "fees-test", "tenant_id": "x"}, headers=staff(api)).status_code == 422
    assert api.get(f"{ST}/changes/not-an-id", headers=staff(api)).status_code == 422
    assert api.post(f"{ST}/changes", json={"doc_id": "../x"}, headers=staff(api)).status_code == 422


# ---- M6 compatibility -------------------------------------------------------------------------------------------------------------
def test_staff_edit_flow_leaves_restore_archive_and_unpublish_working(api):  # noqa: F811
    live_doc(api)
    rid = submitted(api)
    api.post(f"{BASE}/changes/{rid}/review", json={"decision": "approve"}, headers=admin(api))
    api.post(f"{BASE}/changes/{rid}/apply", headers=admin(api))
    process(api, "fees-test", 2)
    publish(api, "fees-test", 2)
    assert api.post(f"{BASE}/documents/fees-test/versions/1/restore", json={}, headers=admin(api)).status_code in (200, 201)
    assert api.post(f"{BASE}/documents/fees-test/unpublish", headers=admin(api)).status_code == 200
    assert live_texts(api, "instalments", "fees-test") == []
    assert api.post(f"{ST}/changes", json={"doc_id": "fees-test"}, headers=staff(api)).status_code == 409        # nothing live -> nothing to edit
