"""Deterministic evaluation of the AI-assisted change workflow on a SYNTHETIC institute (Part 2): the 15 required scenarios, each with hand-written ground truth, run end to end
over HTTP with the rule model (and scripted stub models where a scenario needs a model that misbehaves). Nothing here calls a real LLM, so these numbers describe the
deterministic engine and the safety guards, NOT the quality of any language model: see `make eval-impact` for the optional real-model run.

Set SG_EVAL_OUT=<dir> to also write one JSON result per scenario; `python scripts/eval_impact_summary.py <dir>` aggregates them into the table in the report."""

import json
import os
from pathlib import Path

import pytest

from app.knowledge.impact.evaluate import Evaluation, PassageVerdict, RuleImpactModel
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.institute import DOCS, seed
from tests.knowledge.kbhelp import BASE, admin, create, go_live, process, publish
from tests.knowledge.kwhelp import ST, live_texts, staff
from tests.knowledge.test_ai_flow import events, republish, snapshot
from tests.knowledge.test_ai_release import Flaky, base_embedder, live_state
from tests.rag.conftest import embedder, ingested  # noqa: F401

PY_OLD = "The Python Full Stack course fee changed from ₹45,000 to ₹48,000."
ALL_DOCS = set(DOCS)


def original(doc_id):
    return DOCS[doc_id][1]


def record(name, **metrics):
    out = os.environ.get("SG_EVAL_OUT")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"{name}.json").write_text(json.dumps({"scenario": name, **metrics}, indent=2, sort_keys=True, ensure_ascii=False))


def score(name, req, truth, expected, extra=None):
    """Discovery precision/recall over the documents the analysis proposed to change, and the exactness of each proposed text against the hand-written expectation."""
    pred = {i["doc_id"] for i in req["items"] if i["classification"] in ("definite", "potential") and i["ai"] and i["ai"]["edits"]}
    tp, fp, fn = len(pred & truth), len(pred - truth), len(truth - pred)
    exact = sum(1 for d in truth if d in pred and A.item(req, d)["proposed_content"] == expected[d])
    wrong = sum(1 for d in pred if d not in truth or A.item(req, d)["proposed_content"] != expected.get(d))
    m = {"truth": sorted(truth), "predicted": sorted(pred), "precision": 1.0 if not pred else tp / (tp + fp), "recall": 1.0 if not truth else tp / (tp + fn),
         "replacement_exact": exact, "replacement_total": len(truth), "wrong_edits": wrong, "unsupported": req["analysis"]["summary"]["flags"]["unsupported"] if req["analysis"].get("summary") else 0,
         "historical_guarded": req["analysis"]["summary"]["flags"]["historical"] if req["analysis"].get("summary") else 0, **(extra or {})}
    record(name, **m)
    return m


def release_all(api, req, discard=()):
    rid = req["request_id"]
    for it in req["items"]:
        if it["actionable"] and it["ai"]["edits"]:
            A.decide(api, rid, it["doc_id"], "discarded" if it["doc_id"] in discard else "accepted", **({"reason": "no"} if it["doc_id"] in discard else {}))
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    return A.publish(api, rid)["request"]


def live_joined(doc_id):
    return "\n".join(live_state()[doc_id][2])


# 1 ------------------------------------------------------------------------------------------------------------------------------------
def test_s01_course_fee_changes_across_multiple_documents(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    truth = {"python-fees", "brochure", "admissions-faq"}
    expected = {"python-fees": original("python-fees").replace("₹45,000", "₹48,000"), "brochure": original("brochure").replace("Python Full Stack: Rs. 45,000/-", "Python Full Stack: Rs. 48,000/-"),
                "admissions-faq": original("admissions-faq").replace("The Python Full Stack fee is INR 45000.", "The Python Full Stack fee is INR 48000.")}
    m = score("s01_fee_across_documents", req, truth, expected)
    assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["replacement_exact"] == 3 and m["wrong_edits"] == 0
    assert {d: A.item(req, d)["classification"] for d in truth} == {d: "definite" for d in truth}
    other = {k: v for k, v in live_state().items() if k not in truth}
    done = release_all(api, req)
    assert done["status"] == "published"
    for d in truth:
        assert live_joined(d).replace("\n", " ").count("48") >= 1 and "45,000" not in live_joined(d) and "45000" not in live_joined(d)
    assert {k: v for k, v in live_state().items() if k not in truth} == other
    assert any("48,000" in t for t in live_texts(api, "Python Full Stack course fee", "python-fees"))               # RAG serves the new fact


# 2 ------------------------------------------------------------------------------------------------------------------------------------
def test_s02_fee_change_leaves_historical_records_unchanged(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    arch = A.item(req, "fee-archive")
    assert arch["classification"] == "related_no_change" and arch["final_edits"] == []
    pf = A.item(req, "python-fees")["proposed_content"]
    assert "In 2023 the Python Full Stack course fee was ₹38,000." in pf                                              # the dated record is untouched inside a changed document
    m = score("s02_historical", req, {"python-fees", "brochure", "admissions-faq"}, {
        "python-fees": original("python-fees").replace("₹45,000", "₹48,000"), "brochure": original("brochure").replace("Python Full Stack: Rs. 45,000/-", "Python Full Stack: Rs. 48,000/-"),
        "admissions-faq": original("admissions-faq").replace("The Python Full Stack fee is INR 45000.", "The Python Full Stack fee is INR 48000.")}, {"historical_records_changed": 0})
    assert m["historical_guarded"] >= 1
    release_all(api, req)
    assert live_joined("fee-archive") == "\n".join(live_state()["fee-archive"][2]) and "₹45,000 until March 2025" in live_joined("fee-archive")


# 3 ------------------------------------------------------------------------------------------------------------------------------------
def test_s03_faculty_change_for_one_course_not_another(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "Dr. Mehta replaced by Dr. Rao as the Python Full Stack lead trainer.")
    truth = {"python-faculty"}
    expected = {"python-faculty": original("python-faculty").replace("Dr. Mehta", "Dr. Rao")}
    m = score("s03_faculty_one_course", req, truth, expected)
    assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["wrong_edits"] == 0
    assert "java-faculty" not in {i["doc_id"] for i in req["items"] if i["classification"] in ("definite", "potential")}        # also Dr. Mehta, but a different course
    release_all(api, req)
    assert "Dr. Rao" in live_joined("python-faculty") and "Dr. Mehta" in live_joined("java-faculty")


# 4 ------------------------------------------------------------------------------------------------------------------------------------
def test_s04_weekend_batch_only_timing_change(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "Change every Data Science weekend batch timing from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM.")
    truth = {"ds-weekend-a", "ds-weekend-b", "ds-batches"}
    expected = {"ds-weekend-a": original("ds-weekend-a").replace("10:00 AM - 1:00 PM", "11:00 AM - 2:00 PM"), "ds-weekend-b": original("ds-weekend-b").replace("10:00 AM - 1:00 PM", "11:00 AM - 2:00 PM"),
                "ds-batches": original("ds-batches").replace("The weekend batch meets 10:00 AM - 1:00 PM.", "The weekend batch meets 11:00 AM - 2:00 PM.")}
    m = score("s04_weekend_only", req, truth, expected)
    assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["wrong_edits"] == 0
    assert "The weekday batch meets 10:00 AM - 1:00 PM." in A.item(req, "ds-batches")["proposed_content"]               # same time, different batch: untouched
    assert "ds-weekday" not in {i["doc_id"] for i in req["items"] if i["classification"] == "definite"}
    release_all(api, req)
    assert "weekday batch meets 10:00 AM - 1:00 PM" in live_joined("ds-batches") and "weekend batch meets 11:00 AM - 2:00 PM" in live_joined("ds-batches")


# 5 ------------------------------------------------------------------------------------------------------------------------------------
def test_s05_similar_but_unrelated_course_is_left_alone(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, "The Java Full Stack course fee changed from ₹50,000 to ₹52,000.")
    truth = {"java-fees", "brochure", "admissions-faq"}
    expected = {"java-fees": original("java-fees").replace("₹50,000", "₹52,000"), "brochure": original("brochure").replace("Java Full Stack: Rs. 50,000/-", "Java Full Stack: Rs. 52,000/-"),
                "admissions-faq": original("admissions-faq").replace("The Java Full Stack fee is INR 50000.", "The Java Full Stack fee is INR 52000.")}
    m = score("s05_similar_unrelated", req, truth, expected)
    assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["wrong_edits"] == 0
    assert "Python Full Stack: Rs. 45,000/-" in A.item(req, "brochure")["proposed_content"]
    assert "python-fees" not in {i["doc_id"] for i in req["items"] if i["classification"] in ("definite", "potential")}


# 6 ------------------------------------------------------------------------------------------------------------------------------------
FORMATS = "# Fee Formats\n\n## Python Full Stack\n- Fee: ₹45,000\n- Fee: Rs. 45,000/-\n- Fee: INR 45000\n- Fee: 45,000 rupees\n- Fee: 45k\n- Reference number 450001 is not a fee.\n"


def test_s06_same_information_in_multiple_formats(api):  # noqa: F811
    seed(api, only={"python-fees"})
    go_live(api, doc_id="formats", content=FORMATS, title="Python Full Stack Fee Formats", category="fees")
    req = A.analysed(api, PY_OLD)
    got = A.item(req, "formats")["proposed_content"]
    expected = FORMATS.replace("₹45,000", "₹48,000").replace("Rs. 45,000/-", "Rs. 48,000/-").replace("INR 45000", "INR 48000").replace("45,000 rupees", "48,000 rupees").replace("45k", "48k")
    m = score("s06_formats", req, {"python-fees", "formats"}, {"python-fees": original("python-fees").replace("₹45,000", "₹48,000"), "formats": expected})
    assert got == expected and "450001" in got and m["wrong_edits"] == 0 and m["replacement_exact"] == 2
    assert len(A.item(req, "formats")["final_edits"]) == 5


# 7 ------------------------------------------------------------------------------------------------------------------------------------
def test_s07_ambiguous_description_asks_instead_of_guessing(api):  # noqa: F811
    seed(api)
    before = (live_state(), snapshot())
    vague = A.analysed(api, "We changed the Python Full Stack fee.")
    assert vague["status"] == "awaiting_clarification" and vague["items"] == []
    batches = A.analysed(api, "Data Science weekend batch timing changed from 10:00 AM - 1:00 PM to 11:00 AM - 2:00 PM.")
    q = batches["analysis"]["questions"][0]
    assert batches["status"] == "awaiting_clarification" and {"Data Science Weekend Batch A", "Data Science Weekend Batch B", "All of them"} <= set(q["options"]) and batches["items"] == []
    record("s07_ambiguity", questions_asked=2, silent_guesses=0, suggestions_before_answer=0)
    assert (live_state(), snapshot()) == before


# 8 ------------------------------------------------------------------------------------------------------------------------------------
class Unsure(RuleImpactModel):
    """A model that finds the passages but cannot produce a replacement, or produces an unusable one."""
    name = "stub:unsure"

    def __init__(self, bogus=False):
        self.bogus = bogus

    def evaluate(self, spec, doc, passages):
        return Evaluation(verdicts=[PassageVerdict(passage=p.index, verdict="definite", confidence=0.95, reason="I think this is the fee",
                                                   replacement=("The fee is now higher." if self.bogus else None)) for p in passages if p.has_value])


@pytest.mark.parametrize("bogus", [False, True])
def test_s08_when_the_ai_cannot_identify_the_replacement_it_says_so(api, monkeypatch, bogus):  # noqa: F811
    seed(api, only={"python-fees", "brochure", "java-fees"})
    monkeypatch.setattr("app.api.routes.knowledge_workspace.get_impact_model", lambda s: Unsure(bogus))
    req = A.analysed(api, PY_OLD)
    rid = req["request_id"]
    cands = [i for i in req["items"] if i["actionable"]]
    assert cands and all(i["classification"] == "potential" and i["ai"]["edits"] == [] and i["final_edits"] == [] and i["confidence"] <= 0.4 for i in cands)
    assert all(i["proposed_content"] == original(i["doc_id"]) for i in cands)                                         # no text was invented
    r = api.post(f"{ST}/changes/{rid}/items/brochure/decision", json={"decision": "accepted"}, headers=staff(api))
    assert r.status_code == 422 and "could not propose" in r.json()["detail"]
    A.decide(api, rid, "python-fees", "discarded", reason="cannot verify")
    man = A.decide(api, rid, "brochure", "manual", content=original("brochure").replace("Python Full Stack: Rs. 45,000/-", "Python Full Stack: Rs. 48,000/-"))
    assert man["included"] is True
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    assert A.publish(api, rid)["request"]["status"] == "published"
    assert "Rs. 48,000/-" in live_joined("brochure") and "45,000" in live_joined("python-fees") and "The fee is now higher" not in live_joined("brochure")
    record(f"s08_no_replacement_{'bogus' if bogus else 'none'}", unsupported_published=0, proposals_downgraded=len(cands), flagged_unsupported=req["analysis"]["summary"]["flags"]["unsupported"])


# 9 ------------------------------------------------------------------------------------------------------------------------------------
def test_s09_staff_discards_a_wrong_suggestion(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    done = release_all(api, req, discard=("admissions-faq",))
    assert done["status"] == "published" and done["counts"]["discarded"] == 1
    assert "INR 45000" in live_joined("admissions-faq") and "48000" not in live_joined("admissions-faq")             # the discarded suggestion is nowhere in the live index
    assert not any("48000" in t for t in live_texts(api, "What is the fee for Python Full Stack?", "admissions-faq"))
    assert A.item(done, "admissions-faq")["rationale"] == "no" and A.item(done, "admissions-faq")["publish_status"] is None
    record("s09_discard", discarded_text_in_rag=0, decision_persisted=True)


# 10 -----------------------------------------------------------------------------------------------------------------------------------
def test_s10_staff_manually_corrects_a_suggestion(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    rid = req["request_id"]
    mine = "- Python Full Stack: Rs. 48,000/- for 8 months (from 1 December)"
    for it in req["items"]:
        if it["actionable"] and it["ai"]["edits"]:
            if it["doc_id"] == "brochure":
                out = A.decide(api, rid, "brochure", "manual", replacements=[{"index": 0, "replacement": mine}])
                assert out["decision"] == "manual" and out["ai"]["edits"][0]["replacement"] != mine and out["final_edits"][0]["replacement"] == mine
            else:
                A.decide(api, rid, it["doc_id"], "accepted")
    A.submit(api, rid)
    ad = A.a_get(api, rid)
    assert A.item(ad, "brochure")["decision"] == "manual" and A.item(ad, "brochure")["ai"]["edits"][0]["replacement"] != mine         # both versions are kept for the reviewer
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    A.publish(api, rid)
    assert mine in live_joined("brochure") and "Rs. 48,000/- for 8 months\n" not in live_joined("brochure") + "\n"
    record("s10_manual_correction", staff_text_published_verbatim=True)


# 11 -----------------------------------------------------------------------------------------------------------------------------------
def test_s11_document_changes_after_analysis(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    republish(api, "brochure", original("brochure") + "\nOpen day on Saturday.\n")
    stale = A.item(A.get(api, req["request_id"]), "brochure")
    assert stale["base_stale"] is True
    assert A.decide(api, req["request_id"], "brochure", "accepted", expect=409)["detail"]
    A.analyse(api, req["request_id"])
    fresh = A.get(api, req["request_id"])
    assert A.item(fresh, "brochure")["base_version"] == 2 and "Open day on Saturday." in A.item(fresh, "brochure")["proposed_content"] and A.item(fresh, "brochure")["base_stale"] is False
    record("s11_stale_after_analysis", stale_detected=True, silent_overwrites=0)


# 12 -----------------------------------------------------------------------------------------------------------------------------------
def test_s12_admin_rejects_the_request(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    A.accept_all(api, req)
    A.submit(api, req["request_id"])
    before = (live_state(), snapshot())
    A.a_post(api, req["request_id"], "start-review")
    assert A.a_post(api, req["request_id"], "review", {"decision": "reject", "note": "Fee not approved by management"})["status"] == "rejected"
    assert (live_state(), snapshot()) == before
    A.publish(api, req["request_id"], expect=409)
    acts = [e[0] for e in events(req["request_id"])]
    assert acts[-1] == "change_request_rejected" and "change_analysis_completed" in acts and "change_request_submitted" in acts
    record("s12_reject", published_after_reject=0, audit_events=len(acts))


# 13 -----------------------------------------------------------------------------------------------------------------------------------
def test_s13_processing_fails_for_one_document(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    A.accept_all(api, req)
    rid = req["request_id"]
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    before = live_state()
    good = base_embedder(api)
    api.app.state.embedder = Flaky(good, "INR 48000")
    out = A.publish(api, rid)["request"]
    assert out["status"] == "failed" and out["failure"]["documents"] == ["admissions-faq"] and live_state() == before
    api.app.state.embedder = good
    assert A.publish(api, rid)["request"]["status"] == "published"
    record("s13_partial_processing_failure", partially_published=0, recovered_on_retry=True)


# 14 -----------------------------------------------------------------------------------------------------------------------------------
def test_s14_publication_retry_is_safe(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, PY_OLD)
    A.accept_all(api, req)
    rid = req["request_id"]
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    first = A.publish(api, rid)["request"]
    snap = snapshot()
    again = A.publish(api, rid)
    assert again["outcome"] == "already_published" and snapshot() == snap and again["request"]["release"] == first["release"]
    assert sum(1 for e in events(rid) if e[0] == "change_release_published") == 1
    record("s14_retry", duplicate_versions=0, duplicate_publications=0)


# 15 -----------------------------------------------------------------------------------------------------------------------------------
def test_s15_two_requests_overlap_on_the_same_documents(api):  # noqa: F811
    seed(api)
    a = A.analysed(api, PY_OLD)
    b = A.analysed(api, "The Python Full Stack course fee changed from ₹45,000 to ₹49,000.", h=staff(api, "staff2"))
    for r, h in ((a, None), (b, staff(api, "staff2"))):
        A.accept_all(api, r, h=h)
        A.submit(api, r["request_id"], h=h)
        A.a_post(api, r["request_id"], "start-review")
        A.approve(api, r["request_id"])
    assert A.a_get(api, a["request_id"])["items"] and any(A.item(A.a_get(api, a["request_id"]), d)["overlapping_requests"] for d in ("python-fees", "brochure"))
    assert A.publish(api, a["request_id"])["request"]["status"] == "published"
    second = A.publish(api, b["request_id"])["request"]
    assert second["status"] == "failed" and second["failure"]["stage"] == "validation"
    assert "48,000" in live_joined("python-fees") and "49,000" not in live_joined("python-fees") and "49000" not in live_joined("admissions-faq")
    record("s15_overlap", lost_updates=0, second_request_blocked=True)


# 16-17: added after the real-world failure ("the machine learning faculty is changed to X from today") ---------------------------------------------
FACULTY_CHANGE = "The Python Full Stack lead trainer is changed to Dr. Rao from today"


def test_s16_change_that_names_no_old_value(api):  # noqa: F811
    seed(api)
    req = A.analysed(api, FACULTY_CHANGE)
    expected = {"python-faculty": original("python-faculty").replace("Dr. Mehta", "Dr. Rao")}
    m = score("s16_slot_change_no_old_value", req, {"python-faculty"}, expected)
    assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["replacement_exact"] == 1 and m["wrong_edits"] == 0
    assert A.item(req, "java-faculty")["final_edits"] == [] if any(i["doc_id"] == "java-faculty" for i in req["items"]) else True


def test_s17_a_model_that_contradicts_itself_cannot_hide_an_affected_document(api, monkeypatch):  # noqa: F811
    from tests.knowledge import faculty as F
    seed(api)
    s = A.inline(api)
    s.kb_ai_mode = "llm"
    F.use_model(monkeypatch, F.WeakChat(None, lambda n, t: PassageVerdict(passage=n, verdict="no_change", confidence=0.7, reason="This passage must be updated to the new trainer.")))
    rid = A.start_ai(api, FACULTY_CHANGE)["request_id"]
    A.analyse(api, rid)
    req = A.get(api, rid)
    it = A.item(req, "python-faculty")
    ok = it["classification"] == "potential" and it["final_edits"] and "Dr. Rao" in it["proposed_content"] and "must be updated" not in it["ai"]["reason"]
    record("s17_model_contradiction_not_hidden", affected_document_hidden=not ok, classification=it["classification"], confidence=it["confidence"])
    assert ok
