"""AI-assisted change requests (Part 2), staff side and the review gate, over HTTP with the deterministic rule model (inline execution)."""

import pytest
from sqlalchemy import select

from app.db.models import KbChangeItem, KbDocument, KbEvent, KbVersion
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.institute import DOCS, seed
from tests.knowledge.kbhelp import BASE, FEES_V1, SECRET_TEXT, admin, live_chunks, other_admin
from tests.knowledge.kwhelp import ST, other_staff, staff, student
from tests.rag.conftest import embedder, ingested  # noqa: F401

WEEKEND = "Data Science weekend batch timing changed from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM."


def snapshot(doc_ids=None):
    """Everything a publication would change: published pointer, version rows, live chunks."""
    with session_scope() as db:
        out = {}
        for d in db.execute(select(KbDocument).where(KbDocument.tenant_id == T)).scalars():
            if doc_ids is None or d.doc_id in doc_ids:
                vs = [(v.version_no, v.status, v.checksum) for v in db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == d.doc_id)
                                                                              .order_by(KbVersion.version_no)).scalars()]
                out[d.doc_id] = (d.published_version, d.latest_version, d.status, vs, live_chunks(d.doc_id))
        return out


def events(rid):
    with session_scope() as db:
        return [(e.action, e.doc_id, e.summary) for e in db.execute(select(KbEvent).where(KbEvent.tenant_id == T, KbEvent.resource_type == "request", KbEvent.resource_id == rid)
                                                                   .order_by(KbEvent.id)).scalars()]


def submitted(api, description=A.FEE_CHANGE):
    req = A.analysed(api, description)
    req = A.accept_all(api, req)
    A.submit(api, req["request_id"])
    return req["request_id"]


# ---- analysis ---------------------------------------------------------------------------------------------------------------------------
def test_analysis_classifies_documents_and_writes_suggestions_only(api):  # noqa: F811
    seed(api)
    before = snapshot()
    req = A.analysed(api)
    assert req["status"] == "awaiting_decisions" and req["source"] == "ai_assisted" and req["analysis"]["model"] == "rules:v1"
    cls = {i["doc_id"]: i["classification"] for i in req["items"]}
    assert cls == {"python-fees": "definite", "brochure": "definite", "admissions-faq": "definite", "fee-archive": "related_no_change", "python-faculty": "related_no_change"}
    pf = A.item(req, "python-fees")
    assert pf["ai"]["reason"] and 0.75 <= pf["confidence"] <= 1 and pf["decision"] is None and pf["base_version"] == 1
    assert len(pf["final_edits"]) == 2 and all("48,000" in e["replacement"] and "45,000" not in e["replacement"] for e in pf["final_edits"])
    assert pf["current_published_version"] == 1 and pf["base_stale"] is False
    assert snapshot() == before                                         # NOTHING published or staged by the analysis: it only wrote suggestions


def test_historical_and_unrelated_documents_are_not_modified(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    arch = A.item(req, "fee-archive")
    assert arch["classification"] == "related_no_change" and arch["final_edits"] == [] and not arch["actionable"]
    assert "java-fees" not in {i["doc_id"] for i in req["items"]} and "hostel-rules" not in {i["doc_id"] for i in req["items"]}
    assert req["analysis"]["summary"]["flags"]["historical"] >= 1


def test_the_old_fee_inside_the_fee_history_section_is_left_alone(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "The Python Full Stack course fee changed from ₹38,000 to ₹40,000.")
    pf = A.item(req, "python-fees")
    assert pf["classification"] == "related_no_change" and pf["final_edits"] == []      # 38,000 only appears under "Fee history", dated 2023


def test_analysis_needs_a_description_and_rejects_unknown_fields(api):  # noqa: F811
    A.inline(api)
    for body in ({"description": ""}, {"description": "x" * 1001}, {"description": "ok change from 1 to 2", "tenant_id": "other"}, {"description": "a", "request_id": "nope"}):
        assert api.post(f"{ST}/ai-changes", json=body, headers=staff(api)).status_code == 422
    assert api.post(f"{ST}/ai-changes", json={"description": "from 1 to 2", "reference_doc_id": "missing-doc"}, headers=staff(api)).status_code == 404
    rid = A.start_ai(api)["request_id"]
    assert api.post(f"{ST}/changes/{rid}/analyse", json={"answers": {"x": "y"}, "tenant": "z"}, headers=staff(api)).status_code == 422
    assert api.post(f"{ST}/changes/{rid}/analyse", json={"answers": {"zzz": "y"}}, headers=staff(api)).status_code == 422       # unknown question


def test_rbac_and_privacy_of_ai_requests(api):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    assert api.post(f"{ST}/ai-changes", json={"description": "x from 1 to 2"}).status_code == 401
    assert api.post(f"{ST}/ai-changes", json={"description": "x from 1 to 2"}, headers=student(api)).status_code == 403
    assert api.post(f"{ST}/changes/{rid}/analyse", json={}, headers=student(api)).status_code == 403
    # private until submitted: another staff member, an administrator and another tenant see nothing
    assert A.get(api, rid, h=staff(api, "staff2"), expect=404)
    assert api.get(f"{BASE}/changes/{rid}", headers=admin(api)).status_code == 404
    assert rid not in {r["request_id"] for r in api.get(f"{BASE}/changes", headers=admin(api)).json()["items"]}
    assert api.get(f"{ST}/changes/{rid}", headers=other_staff(api)).status_code == 404
    assert api.get(f"{BASE}/changes/{rid}", headers=other_admin(api)).status_code == 404
    assert api.post(f"{ST}/changes/{rid}/analyse", json={}, headers=staff(api, "staff2")).status_code == 404
    assert api.post(f"{ST}/changes/{rid}/items/brochure/decision", json={"decision": "accepted"}, headers=staff(api, "staff2")).status_code == 404


def test_other_tenants_documents_are_never_candidates(api):  # noqa: F811
    from tests.knowledge.kbhelp import create, process, publish
    seed(api, only={"python-fees"})
    h = other_admin(api)
    create(api, h=h, doc_id="other-python", title="Python Full Stack Course Fees", content="# Fees\n\nThe Python Full Stack fee is ₹45,000.\n", category="fees")
    process(api, "other-python", 1, h=h)
    publish(api, "other-python", 1, h=h)
    req = A.analysed(api)
    assert {i["doc_id"] for i in req["items"]} == {"python-fees"}


# ---- clarification ----------------------------------------------------------------------------------------------------------------------
def test_ambiguous_description_asks_staff_to_restate_instead_of_guessing(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "We changed the Python Full Stack fee.")
    assert req["status"] == "awaiting_clarification" and req["items"] == [] and req["analysis"]["questions"][0]["id"] == "restate"
    A.analyse(api, req["request_id"], expect=422)                                                      # unanswered: refused
    A.analyse(api, req["request_id"], answers={"restate": "The Python Full Stack fee changed from ₹45,000 to ₹48,000"})
    out = A.get(api, req["request_id"])
    assert out["status"] == "awaiting_decisions" and A.item(out, "python-fees")["classification"] == "definite"


def test_several_similar_batches_ask_which_one(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, WEEKEND)
    assert req["status"] == "awaiting_clarification"
    q = req["analysis"]["questions"][0]
    assert q["id"].startswith("pick_") and "All of them" in q["options"] and "Data Science Weekend Batch A" in q["options"] and "Data Science Weekend Batch B" in q["options"]
    A.analyse(api, req["request_id"], answers={q["id"]: "Data Science Weekend Batch A"})
    out = A.get(api, req["request_id"])
    assert out["status"] == "awaiting_decisions"
    assert A.item(out, "ds-weekend-a")["classification"] == "definite" and A.item(out, "ds-weekend-b")["classification"] == "related_no_change"
    assert "ds-weekday" not in {i["doc_id"] for i in out["items"]} or A.item(out, "ds-weekday")["classification"] != "definite"
    assert out["analysis"]["answers"] == {q["id"]: "Data Science Weekend Batch A"}


def test_answering_all_of_them_updates_every_sibling(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, WEEKEND)
    q = req["analysis"]["questions"][0]
    A.analyse(api, req["request_id"], answers={q["id"]: "All of them"})
    out = A.get(api, req["request_id"])
    assert {i["doc_id"] for i in out["items"] if i["classification"] == "definite"} == {"ds-weekend-a", "ds-weekend-b", "ds-batches"}


def test_saying_all_in_the_description_skips_the_question(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "Change every Data Science weekend batch timing from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM.")
    assert req["status"] == "awaiting_decisions" and {i["doc_id"] for i in req["items"] if i["classification"] == "definite"} == {"ds-weekend-a", "ds-weekend-b", "ds-batches"}


def test_a_weekend_only_change_does_not_touch_the_weekday_batch_in_the_same_document(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "Data Science weekend batch: change from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM")
    if req["status"] == "awaiting_clarification":
        q = req["analysis"]["questions"][0]
        A.analyse(api, req["request_id"], answers={q["id"]: "Data Science Weekend Batch A"})
        req = A.get(api, req["request_id"])
    mixed = A.item(req, "ds-batches")
    assert mixed["classification"] == "definite" and len(mixed["final_edits"]) == 1 and "weekend" in mixed["final_edits"][0]["original"]
    assert all("weekday" not in e["original"] for e in mixed["final_edits"])


def test_a_description_that_does_not_say_which_section_asks_for_the_section(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "Data Science batch timing changed from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM.")
    qs = req["analysis"]["questions"]
    scope = next(q for q in qs if q["id"].startswith("scope_"))
    assert scope["doc_id"] == "ds-batches" and "All of them" in scope["options"] and any("Weekend" in o for o in scope["options"])


# ---- decisions --------------------------------------------------------------------------------------------------------------------------
def test_accept_records_the_patch_decision_reviewer_and_time(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    rid = req["request_id"]
    out = A.decide(api, rid, "brochure", "accepted")
    assert out["decision"] == "accepted" and out["decided_by"] == "staff1" and out["decided_at"] and out["included"] is True
    assert "Rs. 48,000/-" in out["proposed_content"] and "Rs. 45,000/-" not in out["proposed_content"]
    assert out["diff"]["body"] and out["stats"]["added_chars"] > 0


def test_manual_edit_by_index_rebuilds_the_diff_from_the_immutable_base(api):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    out = A.decide(api, rid, "brochure", "manual", replacements=[{"index": 0, "replacement": "- Python Full Stack: Rs. 48,000/- (8 months, GST extra)"}])
    assert out["decision"] == "manual" and "GST extra" in out["proposed_content"] and out["final_edits"][0]["replacement"].endswith("GST extra)")
    assert out["final_edits"][0]["original"] == "- Python Full Stack: Rs. 45,000/- for 8 months"
    segs = out["diff"]["body"]
    from app.knowledge import diffing
    assert diffing.verify("# Institute Brochure\n\n## Programmes\n- Python Full Stack: Rs. 45,000/- for 8 months\n- Java Full Stack: Rs. 50,000/- for 8 months\n", out["proposed_content"], segs)


def test_manual_edit_with_full_content(api):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    new = DOCS["brochure"][1].replace("Rs. 45,000/-", "Rs. 47,500/-")
    out = A.decide(api, rid, "brochure", "manual", content=new)
    assert out["decision"] == "manual" and "47,500" in out["proposed_content"] and out["final_edits"]


def test_discard_keeps_the_published_text_and_stores_the_reason(api):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    out = A.decide(api, rid, "brochure", "discarded", reason="Brochure is reprinted in January")
    assert out["decision"] == "discarded" and out["rationale"] == "Brochure is reprinted in January" and out["included"] is False
    assert out["proposed_content"] == DOCS["brochure"][1] and out["stats"]["added_chars"] == 0 and out["final_edits"] == []
    assert A.decide(api, rid, "brochure", "discarded")["rationale"] is None                              # the reason is optional


@pytest.mark.parametrize("body,code", [
    ({"decision": "manual"}, 422),                                                                      # no text at all
    ({"decision": "manual", "replacements": [{"index": 9, "replacement": "x ₹48,000"}]}, 422),
    ({"decision": "manual", "replacements": [{"index": 0, "replacement": "   "}]}, 422),
    ({"decision": "manual", "replacements": [{"index": 0, "replacement": "- Python Full Stack: Rs. 45,000/- for 8 months"}]}, 422),   # identical: changes nothing
    ({"decision": "maybe"}, 422),
    ({"decision": "accepted", "extra": 1}, 422),
    ({"decision": "discarded", "reason": "x" * 501}, 422),
])
def test_invalid_decisions_are_refused(api, body, code):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    r = api.post(f"{ST}/changes/{rid}/items/brochure/decision", json=body, headers=staff(api))
    assert r.status_code == code, r.text


def test_related_and_unknown_documents_have_nothing_to_decide(api):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    assert api.post(f"{ST}/changes/{rid}/items/fee-archive/decision", json={"decision": "accepted"}, headers=staff(api)).status_code == 404
    assert api.post(f"{ST}/changes/{rid}/items/hostel-rules/decision", json={"decision": "accepted"}, headers=staff(api)).status_code == 404


def test_a_decision_made_from_a_stale_window_conflicts(api):  # noqa: F811
    seed(api)
    rid = A.analysed(api)["request_id"]
    rv = A.item(A.get(api, rid), "brochure")["row_version"]
    A.decide(api, rid, "brochure", "discarded", expected_row_version=rv)
    A.decide(api, rid, "brochure", "accepted", expected_row_version=rv, expect=409)
    A.decide(api, rid, "brochure", "accepted", expected_row_version=rv + 1)


def test_cannot_submit_until_every_suggestion_has_a_decision(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    A.decide(api, req["request_id"], "brochure", "accepted")
    r = A.submit(api, req["request_id"], expect=422)
    assert "decide every suggested document" in r["detail"]


def test_nothing_to_submit_when_everything_is_discarded(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    for it in req["items"]:
        if it["actionable"]:
            A.decide(api, req["request_id"], it["doc_id"], "discarded", reason="no")
    assert "nothing to submit" in A.submit(api, req["request_id"], expect=422)["detail"]


def test_submission_preserves_everything_the_administrator_needs(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    rid = req["request_id"]
    A.decide(api, rid, "python-fees", "accepted")
    A.decide(api, rid, "brochure", "manual", replacements=[{"index": 0, "replacement": "- Python Full Stack: Rs. 48,000/- for 8 months (new batches)"}])
    A.decide(api, rid, "admissions-faq", "discarded", reason="FAQ is rewritten next week")
    sub = A.submit(api, rid, "Python fee goes up in December")
    assert sub["status"] == "submitted" and sub["submitted_by"] == "staff1" and sub["submitted_at"] and sub["description"] == "Python fee goes up in December"
    assert sub["counts"]["accepted"] == 1 and sub["counts"]["edited"] == 1 and sub["counts"]["discarded"] == 1 and sub["counts"]["included"] == 2
    ad = A.a_get(api, rid)
    assert ad["status"] == "submitted" and ad["document_count"] == len(ad["items"]) and ad["counts"] == sub["counts"] | ad["counts"]
    faq = A.item(ad, "admissions-faq")
    assert faq["decision"] == "discarded" and faq["rationale"] == "FAQ is rewritten next week" and faq["included"] is False and faq["ai"]["edits"]      # the AI proposal is kept
    for it in ad["items"]:
        assert it["base_version"] == 1 and it["base_checksum"] and it["classification"]
    br = A.item(ad, "brochure")
    assert br["decision"] == "manual" and br["ai"]["edits"][0]["replacement"] != br["final_edits"][0]["replacement"] and br["decided_by"] == "staff1"
    assert A.item(ad, "python-fees")["decision"] == "accepted"


def test_a_submitted_request_is_frozen_for_staff(api):  # noqa: F811
    seed(api)
    rid = submitted(api)
    A.decide(api, rid, "brochure", "discarded", expect=409)
    A.analyse(api, rid, expect=409)
    assert api.put(f"{ST}/changes/{rid}", json={"description": "x"}, headers=staff(api)).status_code == 409
    A.submit(api, rid, expect=409)


# ---- workflow A -------------------------------------------------------------------------------------------------------------------------
def test_workflow_a_starts_from_the_staff_members_own_edit(api):  # noqa: F811
    from tests.knowledge.kwhelp import save, start
    seed(api)
    out = start(api, "python-fees")
    rid = out["request"]["request_id"]
    save(api, rid, "python-fees", DOCS["python-fees"][1].replace("45,000", "48,000"), out["request"]["items"][0]["row_version"])
    A.inline(api)
    v = A.start_ai(api, "Python fee goes up to ₹48,000", request_id=rid)
    assert v["request_id"] == rid and v["source"] == "ai_assisted"
    A.analyse(api, rid)
    req = A.get(api, rid)
    assert req["status"] == "awaiting_decisions"
    primary = A.item(req, "python-fees")
    assert primary["classification"] is None and primary["included"] is True and primary["actionable"] is False                    # the staff member's own edit stays manual
    assert {i["doc_id"] for i in req["items"] if i["classification"] == "definite"} == {"brochure", "admissions-faq"}
    A.accept_all(api, req)
    assert A.submit(api, rid)["status"] == "submitted"


# ---- failure, retry and fencing of the analysis -----------------------------------------------------------------------------------------
def test_a_failing_model_returns_the_request_to_draft_with_a_retryable_error(api, monkeypatch):  # noqa: F811
    from app.knowledge.impact.evaluate import RuleImpactModel
    seed(api)

    class Boom(RuleImpactModel):
        def evaluate(self, *a, **k):
            raise RuntimeError("ollama unreachable " + SECRET_TEXT)
    monkeypatch.setattr("app.api.routes.knowledge_workspace.get_impact_model", lambda s: Boom())
    A.inline(api)
    rid = A.start_ai(api)["request_id"]
    A.analyse(api, rid)
    req = A.get(api, rid)
    assert req["status"] == "draft" and req["analysis"]["error"] and SECRET_TEXT not in req["analysis"]["error"] and req["items"] == []
    acts = [e[0] for e in events(rid)]
    assert "change_analysis_failed" in acts
    monkeypatch.undo()
    A.inline(api)
    A.analyse(api, rid)
    assert A.get(api, rid)["status"] == "awaiting_decisions"


def test_a_running_analysis_cannot_be_started_twice_but_a_dead_one_can_be_taken_over(api):  # noqa: F811
    from app.knowledge import changes
    from app.knowledge.impact import analyse as impact
    from app.knowledge.impact.evaluate import RuleImpactModel
    seed(api)
    s = A.inline(api)
    rid = A.start_ai(api)["request_id"]
    with session_scope() as db:
        _, a1 = changes.begin_analysis(db, T, "staff1", s, rid)
    with pytest.raises(Exception) as e:
        with session_scope() as db:
            changes.begin_analysis(db, T, "staff1", s, rid)
    assert getattr(e.value, "status", None) == 409
    s.kb_ai_analysis_stale_s = 0                                                                        # the first worker is considered dead
    with session_scope() as db:
        _, a2 = changes.begin_analysis(db, T, "staff1", s, rid)
    assert a2 == a1 + 1
    emb = api.app.state.embedder if getattr(api.app.state, "embedder", None) else None
    impact.run(T, rid, a1, s, RuleImpactModel(), emb)                                                   # the old worker wakes up: it must write nothing
    assert A.get(api, rid)["status"] == "analysing" and A.get(api, rid)["items"] == []
    impact.run(T, rid, a2, s, RuleImpactModel(), emb)
    assert A.get(api, rid)["status"] == "awaiting_decisions"
    s.kb_ai_analysis_stale_s = 900


def test_cancelling_during_analysis_stops_the_results_from_being_written(api):  # noqa: F811
    from app.knowledge import changes
    from app.knowledge.impact import analyse as impact
    from app.knowledge.impact.evaluate import RuleImpactModel
    seed(api)
    s = A.inline(api)
    rid = A.start_ai(api)["request_id"]
    with session_scope() as db:
        _, att = changes.begin_analysis(db, T, "staff1", s, rid)
    r = api.post(f"{ST}/changes/{rid}/cancel", headers=staff(api))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    impact.run(T, rid, att, s, RuleImpactModel(), None)
    out = A.get(api, rid)
    assert out["status"] == "cancelled" and out["items"] == []


def test_reanalysing_replaces_the_suggestions_and_clears_old_decisions(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    A.decide(api, req["request_id"], "brochure", "discarded")
    A.analyse(api, req["request_id"])
    out = A.get(api, req["request_id"])
    assert out["status"] == "awaiting_decisions" and A.item(out, "brochure")["decision"] is None and out["analysis"]["attempt"] == 2


# ---- staleness --------------------------------------------------------------------------------------------------------------------------
def republish(api, doc_id, content):
    from tests.knowledge.kbhelp import add_version, process, publish
    n = add_version(api, doc_id, content)["version"]["version"]
    process(api, doc_id, n)
    publish(api, doc_id, n)
    return n


def test_a_document_changed_after_the_analysis_is_stale_and_cannot_be_accepted(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    republish(api, "brochure", DOCS["brochure"][1] + "\nNew line.\n")
    it = A.item(A.get(api, req["request_id"]), "brochure")
    assert it["base_stale"] is True and it["current_published_version"] == 2 and it["base_version"] == 1
    r = A.decide(api, req["request_id"], "brochure", "accepted", expect=409)
    assert "run the analysis again" in r["detail"]
    A.decide(api, req["request_id"], "brochure", "discarded")                                     # discarding is always possible
    A.analyse(api, req["request_id"])                                                              # re-analysis works on the new version
    assert A.item(A.get(api, req["request_id"]), "brochure")["base_version"] == 2


def test_submit_refuses_documents_that_moved_on(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    A.accept_all(api, req)
    republish(api, "admissions-faq", DOCS["admissions-faq"][1] + "\nMore.\n")
    r = A.submit(api, req["request_id"], expect=409)
    assert "admissions-faq" in r["detail"]


# ---- audit ------------------------------------------------------------------------------------------------------------------------------
def test_audit_records_the_staff_phase_without_any_document_text(api):  # noqa: F811
    seed(api)
    req = A.analysed(api)
    rid = req["request_id"]
    A.decide(api, rid, "brochure", "manual", replacements=[{"index": 0, "replacement": "- Python Full Stack: Rs. 48,000/- " + SECRET_TEXT}])
    A.decide(api, rid, "python-fees", "accepted")
    A.decide(api, rid, "admissions-faq", "discarded", reason="secret reason " + SECRET_TEXT)
    A.submit(api, rid)
    ev = events(rid)
    acts = [e[0] for e in ev]
    for need in ("change_request_created", "change_analysis_started", "change_analysis_completed", "change_suggestions_generated", "change_decision_recorded", "change_request_submitted"):
        assert need in acts, need
    assert acts.index("change_analysis_started") < acts.index("change_analysis_completed") < acts.index("change_decision_recorded") < acts.index("change_request_submitted")
    assert SECRET_TEXT not in str(ev) and "secret reason" not in str(ev) and "Rs. 48" not in str(ev)
    done = next(e for e in ev if e[0] == "change_analysis_completed")[2]
    assert done["definite"] == 3 and done["related"] == 2 and done["candidates"] == 5
    decisions = [e[2]["decision"] for e in ev if e[0] == "change_decision_recorded"]
    assert sorted(decisions) == ["accepted", "discarded", "manual"]
    # staff can read the history of their own request, admins only once it is submitted
    aud = api.get(f"{ST}/changes/{rid}/audit", headers=staff(api)).json()["items"]
    assert [a["action"] for a in aud] == acts
    assert api.get(f"{ST}/changes/{rid}/audit", headers=staff(api, "staff2")).status_code == 404
    assert api.get(f"{BASE}/changes/{rid}/audit", headers=admin(api)).status_code == 200
