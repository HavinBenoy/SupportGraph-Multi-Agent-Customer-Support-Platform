"""Refinement 2.2: when an administrator returns a change request, the author can revise it either with the AI (re-analysis) or by MANUAL editing of the full
returned proposal. A manual revision is a revision of the same request (one lifecycle): it is attributed, audited (marked as made after the return), compared
against the original and the AI proposal, resubmitted for review, and never published by itself. The published document and its versions are untouched."""

from sqlalchemy import select

from app.db.models import KbEvent, KbVersion
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.kbhelp import BASE, admin, other_admin
from tests.knowledge.kwhelp import ST, drafted, live_doc, other_staff, staff
from tests.knowledge.test_ai_release import make, live_state
from tests.rag.conftest import embedder, ingested  # noqa: F401


def returned(api, rid, note="Please reword the fee paragraph"):
    A.a_post(api, rid, "start-review")
    r = A.a_post(api, rid, "review", {"decision": "return", "note": note})
    assert r["status"] == "needs_revision"
    return A.get(api, rid)


def versions(doc_id):
    with session_scope() as db:
        return [(v.version_no, v.checksum) for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == doc_id)).scalars()]


def test_manual_revision_of_a_returned_ai_request_is_attributed_resubmitted_and_never_published(api):  # noqa: F811
    rid = make(api)
    req = returned(api, rid)
    it = A.item(req, "python-fees")
    assert it["revision_source"] == "ai" and it["revised_after_return"] is False
    before_live, before_versions = live_state(), versions("python-fees")
    current = it["proposed_content"]
    rewritten = current.replace("48,000", "48,000 (inclusive of GST)") + "\nInstalments are available on request.\n"   # add, replace and restructure freely
    out = A.decide(api, rid, "python-fees", "manual", content=rewritten, expected_row_version=it["row_version"])
    assert out["revision_source"] == "manual" and out["revised_after_return"] is True
    assert out["proposed_content"].rstrip() == rewritten.rstrip()
    assert out["staff_edited_by"] == "staff1" and out["last_edited_by"] == "staff1" and out["last_edit_role"] == "author"
    assert out["ai"]["edits"], "the AI proposal is kept for comparison, never overwritten"
    assert live_state() == before_live and versions("python-fees") == before_versions                      # nothing published, no version created
    assert A.submit(api, rid)["status"] == "submitted"                                                     # the same request goes back to review
    adm = A.item(A.a_get(api, rid), "python-fees")
    assert adm["revision_source"] == "manual" and adm["revised_after_return"] is True and adm["staff_edited_by"] == "staff1"
    assert live_state() == before_live
    with session_scope() as db:
        ev = db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.resource_id == rid, KbEvent.action == "change_decision_recorded",
                                              KbEvent.doc_id == "python-fees").order_by(KbEvent.id)).scalars().all()
        assert ev[-1].summary["decision"] == "manual" and ev[-1].summary["revision"] is True and ev[0].summary["revision"] is False
        assert "GST" not in str([e.summary for e in ev])                                                   # the audit holds no text


def test_manual_revision_of_a_returned_manual_request_uses_the_draft_editor_with_its_concurrency_token(api):  # noqa: F811
    live_doc(api)
    rid, it = drafted(api)
    assert it["revision_source"] == "manual"
    from tests.knowledge.kwhelp import submit
    submit(api, rid)
    api.post(f"{BASE}/changes/{rid}/start-review", headers=admin(api))
    assert api.post(f"{BASE}/changes/{rid}/review", json={"decision": "return", "note": "shorter please"}, headers=admin(api)).json()["status"] == "needs_revision"
    cur = A.item(A.get(api, rid), "fees-test")
    new = cur["proposed_content"] + "\nContact the office for help.\n"
    r = api.put(f"{ST}/changes/{rid}/items/fees-test", json={"content": new, "expected_row_version": cur["row_version"] - 1}, headers=staff(api))
    assert r.status_code == 409                                                                             # a stale editor never overwrites
    r = api.put(f"{ST}/changes/{rid}/items/fees-test", json={"content": new, "expected_row_version": cur["row_version"]}, headers=staff(api))
    assert r.status_code == 200 and r.json()["revised_after_return"] is True
    assert submit(api, rid)["status"] == "submitted"
    with session_scope() as db:
        ev = db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.resource_id == rid, KbEvent.action == "change_draft_saved")
                        .order_by(KbEvent.id)).scalars().all()
        assert ev[-1].summary["revision"] is True and ev[-1].actor == "staff1"


def test_only_the_author_can_revise_and_only_while_returned(api):  # noqa: F811
    rid = make(api)
    it = A.item(A.get(api, rid), "python-fees")
    body = {"decision": "manual", "content": it["proposed_content"] + "\nx\n"}
    assert api.post(f"{ST}/changes/{rid}/items/python-fees/decision", json=body, headers=staff(api)).status_code == 409   # submitted: not editable
    returned(api, rid)
    assert api.post(f"{ST}/changes/{rid}/items/python-fees/decision", json=body, headers=other_staff(api)).status_code == 404   # IDOR: another staff member
    assert api.post(f"{ST}/changes/{rid}/items/python-fees/decision", json=body, headers=admin(api)).status_code in (403, 404)   # not the author: never editable by someone else
    assert api.post(f"{ST}/changes/{rid}/items/python-fees/decision", json=body, headers=other_admin(api)).status_code in (403, 404)   # other tenant
    bad = {"decision": "manual", "content": "\x00bad"}
    assert api.post(f"{ST}/changes/{rid}/items/python-fees/decision", json=bad, headers=staff(api)).status_code == 422
    assert api.post(f"{ST}/changes/{rid}/items/python-fees/decision", json=body, headers=staff(api)).status_code == 200


def test_choosing_the_ai_option_after_a_manual_revision_clears_the_manual_attribution(api):  # noqa: F811
    rid = make(api)
    req = returned(api, rid)
    it = A.item(req, "python-fees")
    A.decide(api, rid, "python-fees", "manual", content=it["proposed_content"] + "\nNote.\n")
    back = A.decide(api, rid, "python-fees", "accepted")
    assert back["revision_source"] == "ai" and back["staff_edited_by"] is None and back["revised_after_return"] is False
