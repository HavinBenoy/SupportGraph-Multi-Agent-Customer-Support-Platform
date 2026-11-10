"""Regression suite for the reported failure: "The machine learning faculty is changed to santhosh sir from today" against "Machine Learning faculty is Jyothi sir" was shown as
"related, no change" although its own explanation said the passage must be updated.

Root cause (reproduced before the fix): a small model returned the subject as a PHRASE ("machine learning faculty"); the scope guard compares single words, so every passage
was rejected as "a different subject", and the guard kept the model's explanation next to its new verdict. The description also names no old value, which the engine could not
represent. These tests drive the full HTTP workflow with weak-model stubs (`faculty.WeakChat`) AND the rule engine. They contain no real-model call: see test_ai_real_model.py."""

import pytest
from sqlalchemy import select

from app.db.models import KbDocument
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge import faculty as F
from tests.knowledge.kbhelp import go_live, live_chunks
from tests.knowledge.test_ai_flow import events, snapshot
from tests.rag.conftest import embedder, ingested  # noqa: F401

NEW_LINE = "Machine Learning faculty is Santhosh sir"
MODELS = {
    "rules": None,
    "says_definite": lambda: F.WeakChat(F.spec_phrase(), F.says_definite),
    "says_no_change_but_explains_update": lambda: F.WeakChat(F.spec_phrase(), F.says_no_change_but_explains_update),
    "irrelevant": lambda: F.WeakChat(F.spec_hallucinated_old(), F.says_irrelevant),
    "potential_without_replacement": lambda: F.WeakChat(F.spec_slot_only(), F.says_potential_without_replacement),
    "model_down": lambda: F.WeakChat(RuntimeError("ollama down"), lambda n, t: (_ for _ in ()).throw(RuntimeError("ollama down"))),
}


def analyse(api, monkeypatch, model="rules", description=F.CHANGE):
    s = A.inline(api)
    chat = MODELS[model]() if MODELS[model] else None
    if chat is not None:
        s.kb_ai_mode = "llm"
        F.use_model(monkeypatch, chat)
    rid = A.start_ai(api, description)["request_id"]
    A.analyse(api, rid)
    return A.get(api, rid)


def row(req, doc_id):
    return next((i for i in req["items"] if i["doc_id"] == doc_id), None)


def consistent(it):
    """Explanation, classification, confidence and the proposed edit must agree with each other."""
    reason = (it["ai"]["reason"] or "").lower()
    edits = it["final_edits"] or []
    cls = it["classification"]
    if cls in ("definite", "potential"):
        assert edits, "an affected document carries a proposed replacement"
        assert "no change" not in reason and "different subject" not in reason and "historical" not in reason, reason
    if cls == "definite":
        assert it["confidence"] >= 0.75
    if cls == "related_no_change":
        assert not edits and not it["actionable"]
        assert "must be updated" not in reason, reason


# ---- A. the exact reported failure -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("model", list(MODELS))
def test_the_reported_change_marks_the_faculty_document_as_affected_with_a_grounded_replacement(api, monkeypatch, model):  # noqa: F811
    F.seed_ds(api)
    before = snapshot()
    req = analyse(api, monkeypatch, model)
    ds = row(req, "data-science")
    assert req["status"] == "awaiting_decisions" and ds is not None, req["analysis"]
    assert ds["classification"] in ("definite", "potential") and ds["actionable"] is True
    assert [(e["original"], e["replacement"]) for e in ds["final_edits"]] == [(F.DS_DOC, NEW_LINE)]
    assert ds["proposed_content"] == NEW_LINE and "from today" not in ds["proposed_content"]
    consistent(ds)
    if model in ("says_no_change_but_explains_update", "irrelevant", "model_down"):
        assert ds["classification"] == "potential" and ds["confidence"] <= 0.6                 # the model disagreed or failed: staff confirm, nothing is "definite"
    if model == "potential_without_replacement":
        assert ds["classification"] == "potential"                                              # the model was unsure: the document stays "needs confirmation"
    assert row(req, "course") is None or row(req, "course")["final_edits"] == []                 # the unrelated course document is untouched
    assert req["analysis"]["summary"]["flags"]["scope"] == 0
    assert snapshot() == before                                                                  # a suggestion only: nothing published


def test_the_rule_engine_parses_a_description_that_names_no_old_value():
    from app.knowledge.impact.spec import parse_rules
    sp = parse_rules(F.CHANGE)
    f = sp.actionable()[0]
    assert (f.attribute, f.old_value, f.new_value, f.effective) == ("faculty", None, "santhosh sir", None)
    assert sp.entity_terms == ["machine", "learning"]                                           # the new value's words are not part of the subject


def test_a_model_phrase_entity_term_no_longer_defeats_the_scope_guard():
    from app.knowledge.impact.spec import ChangeSpec, Fact
    sp = ChangeSpec(facts=[Fact(attribute="faculty", new_value="santhosh sir")], entity_terms=["machine learning faculty", "santhosh sir"])
    assert sp.entity_terms == ["machine", "learning"]


def test_values_a_model_invented_are_rejected():
    from app.knowledge.impact.evaluate import LlmImpactModel
    from app.knowledge.impact.spec import ChangeSpec, Fact
    desc = "Priya mam takes over as the Machine Learning faculty"
    invented = ChangeSpec(facts=[Fact(attribute="faculty", old_value="Jyothi sir", new_value="Priya mam")], entity_terms=["machine learning"])
    got = LlmImpactModel(F.WeakChat(invented, None), name="t").parse(desc)
    assert [(f.old_value, f.new_value) for f in got.actionable()] == [(None, "Priya mam")]       # "Jyothi sir" is not in the description: dropped, the document's value is used


# ---- B. surrounding cases ------------------------------------------------------------------------------------------------------------------
def test_same_old_value_in_an_unrelated_subject_is_not_changed(api, monkeypatch):  # noqa: F811
    F.seed_ds(api, {"python-fac": ("Python Full Stack Faculty", "Python Full Stack faculty is Jyothi sir")})
    for desc in (F.CHANGE, "The machine learning faculty changed from Jyothi sir to santhosh sir"):
        req = analyse(api, monkeypatch, "rules", desc)
        assert row(req, "data-science")["classification"] == "definite"
        py = row(req, "python-fac")
        assert py is None or (py["final_edits"] == [] and py["classification"] != "definite")


def test_same_subject_with_a_different_existing_faculty_is_still_affected(api, monkeypatch):  # noqa: F811
    go_live(api, doc_id="ml2", content="Machine Learning faculty is Anand sir", title="ML Faculty", category="courses")
    req = analyse(api, monkeypatch, "rules")
    assert row(req, "ml2")["classification"] == "definite" and row(req, "ml2")["proposed_content"] == NEW_LINE


def test_historical_records_keep_the_old_faculty(api, monkeypatch):  # noqa: F811
    F.seed_ds(api, {"ml-history": ("Faculty History", "# Faculty history\n\n## Previous faculty\nMachine Learning faculty is Jyothi sir until March 2025."),
                    "ml-archive": ("Faculty Archive", "# Archive\n\n## Previous faculty\nMachine Learning faculty is Jyothi sir.")})
    for model in ("rules", "says_definite"):
        req = analyse(api, monkeypatch, model)
        assert row(req, "data-science")["classification"] in ("definite", "potential")
        for d in ("ml-history", "ml-archive"):
            it = row(req, d)
            assert it is not None and it["classification"] == "related_no_change" and it["final_edits"] == [] and "historical" in it["ai"]["reason"].lower()
            consistent(it)
        assert req["analysis"]["summary"]["flags"]["historical"] == 2


@pytest.mark.parametrize("desc,expected", [
    ("The machine learning faculty is changed to santhosh sir from today", NEW_LINE),
    ("The machine learning faculty is changed to santhosh sir starting 1 December", NEW_LINE + " (effective 1 December)"),
])
def test_effective_dates_current_and_future(api, monkeypatch, desc, expected):  # noqa: F811
    F.seed_ds(api)
    assert row(analyse(api, monkeypatch, "rules", desc), "data-science")["proposed_content"] == expected


def test_from_tomorrow_becomes_a_calendar_date():
    from datetime import date
    from app.knowledge.impact.spec import parse_rules
    f = parse_rules("The machine learning faculty is changed to santhosh sir from tomorrow", today=date(2026, 11, 10)).actionable()[0]
    assert f.effective == "11 November 2026"


def test_a_document_that_already_has_the_new_faculty_is_left_alone(api, monkeypatch):  # noqa: F811
    go_live(api, doc_id="ml3", content="Machine Learning faculty is Santhosh sir", title="ML", category="courses")
    for model in ("rules", "says_definite"):
        it = row(analyse(api, monkeypatch, model), "ml3")
        assert it is None or (it["classification"] == "related_no_change" and it["final_edits"] == [] and "already" in it["ai"]["reason"].lower())


def test_the_same_fact_in_several_documents_is_found_in_each(api, monkeypatch):  # noqa: F811
    F.seed_ds(api, {"brochure": ("Brochure", "# Brochure\n\n- Machine Learning faculty: Jyothi sir\n- Python Full Stack faculty: Dr. Mehta"),
                    "faq": ("FAQ", "Q: Who teaches?\nA: The Machine Learning faculty is Jyothi sir.")})
    req = analyse(api, monkeypatch, "rules")
    got = {d: row(req, d) for d in ("data-science", "brochure", "faq")}
    assert all(i["classification"] == "definite" for i in got.values())
    assert got["brochure"]["final_edits"][0]["replacement"] == "- Machine Learning faculty: Santhosh sir"
    assert got["faq"]["final_edits"][0]["replacement"] == "A: The Machine Learning faculty is Santhosh sir."
    assert "Dr. Mehta" in got["brochure"]["proposed_content"]                                      # the Python line of the same file is untouched


def test_different_courses_with_similar_words_only_the_named_one_changes(api, monkeypatch):  # noqa: F811
    F.seed_ds(api, {"py": ("Python Full Stack", "Python Full Stack faculty is Dr. Mehta"), "java": ("Java Full Stack", "Java Full Stack faculty is Dr. Mehta")})
    req = analyse(api, monkeypatch, "rules", "The Python Full Stack faculty is changed to Dr. Rao from today")
    assert row(req, "py")["proposed_content"] == "Python Full Stack faculty is Dr. Rao"
    assert row(req, "java") is None or row(req, "java")["final_edits"] == []
    assert row(req, "data-science") is None or row(req, "data-science")["final_edits"] == []


def test_an_ambiguous_change_asks_instead_of_guessing(api, monkeypatch):  # noqa: F811
    go_live(api, doc_id="wa", content="Data Science weekend batch A faculty is Anand sir", title="Data Science Weekend Batch A", category="courses")
    go_live(api, doc_id="wb", content="Data Science weekend batch B faculty is Anand sir", title="Data Science Weekend Batch B", category="courses")
    req = analyse(api, monkeypatch, "rules", "The data science weekend faculty is now santhosh sir")
    assert req["status"] == "awaiting_clarification" and req["items"] == []
    q = req["analysis"]["questions"][0]
    assert "Data Science Weekend Batch A" in q["options"] and "Data Science Weekend Batch B" in q["options"]


def test_mentions_of_the_subject_that_need_no_update_are_not_changed(api, monkeypatch):  # noqa: F811
    F.seed_ds(api, {"intro": ("ML Intro", "The Machine Learning faculty are experienced professionals.\nMachine learning faculty: see the team page for biographies.")})
    req = analyse(api, monkeypatch, "rules")
    it = row(req, "intro")
    assert it is None or (it["final_edits"] == [] and not it["actionable"])


def test_an_unsupported_model_replacement_is_replaced_by_the_grounded_one_or_dropped(api, monkeypatch):  # noqa: F811
    F.seed_ds(api)
    bad = lambda n, t: F.PassageVerdict(passage=n, verdict="definite", confidence=0.95, reason="update", replacement="Machine Learning faculty is Jyothi sir and more")  # noqa: E731
    s = A.inline(api)
    s.kb_ai_mode = "llm"
    F.use_model(monkeypatch, F.WeakChat(F.spec_phrase(), bad))
    rid = A.start_ai(api, F.CHANGE)["request_id"]
    A.analyse(api, rid)
    ds = row(A.get(api, rid), "data-science")
    assert ds["proposed_content"] == NEW_LINE and "Jyothi" not in ds["proposed_content"]


# ---- re-analysis, retry, stale, staff decisions, admin ---------------------------------------------------------------------------------------
def test_reanalysis_replaces_the_suggestions_and_a_failed_analysis_can_be_retried(api, monkeypatch):  # noqa: F811
    F.seed_ds(api)
    s = A.inline(api)
    s.kb_ai_mode = "llm"
    F.use_model(monkeypatch, F.WeakChat(RuntimeError("down"), lambda n, t: (_ for _ in ()).throw(RuntimeError("down"))))
    rid = A.start_ai(api, F.CHANGE)["request_id"]
    A.analyse(api, rid)
    first = A.get(api, rid)
    A.analyse(api, rid, expect=202)
    second = A.get(api, rid)
    assert len([i for i in second["items"] if i["doc_id"] == "data-science"]) == 1 and second["status"] == "awaiting_decisions"
    assert first["analysis"]["attempt"] < second["analysis"]["attempt"]


def test_a_stale_faculty_suggestion_cannot_be_accepted(api, monkeypatch):  # noqa: F811
    from tests.knowledge.test_ai_flow import republish
    F.seed_ds(api)
    req = analyse(api, monkeypatch)
    republish(api, "data-science", "Machine Learning faculty is Anand sir\n")
    r = api.post(f"{A.ST}/changes/{req['request_id']}/items/data-science/decision", json={"decision": "accepted"}, headers=A.staff(api))
    assert r.status_code == 409


def test_staff_can_accept_discard_and_correct_and_admin_approval_then_publishes_exactly_that(api, monkeypatch):  # noqa: F811
    F.seed_ds(api, {"brochure": ("Brochure", "- Machine Learning faculty: Jyothi sir")})
    req = analyse(api, monkeypatch)
    rid = req["request_id"]
    assert "Santhosh sir" in row(req, "brochure")["proposed_content"]
    A.decide(api, rid, "data-science", "manual", replacements=[{"index": 0, "replacement": "Machine Learning faculty is Santhosh sir (from today)"}])
    A.decide(api, rid, "brochure", "discarded", reason="brochure is reprinted separately")
    A.submit(api, rid)
    A.approve(api, rid)
    assert "Jyothi" in A.live_text("data-science")                                               # approval publishes nothing
    out = A.publish(api, rid)["request"]
    assert out["status"] == "published"
    assert A.live_text("data-science") == "Machine Learning faculty is Santhosh sir (from today)"
    assert A.live_text("brochure") == "- Machine Learning faculty: Jyothi sir"                   # the discarded suggestion never reached the live index
    acts = [e[0] for e in events(rid)]
    assert acts.index("change_analysis_completed") < acts.index("change_decision_recorded") < acts.index("change_request_approved") < acts.index("change_release_published")


def test_admin_rejection_leaves_the_knowledge_untouched_and_keeps_the_audit(api, monkeypatch):  # noqa: F811
    F.seed_ds(api)
    before = snapshot()
    req = analyse(api, monkeypatch)
    rid = req["request_id"]
    A.accept_all(api, req)
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.a_post(api, rid, "review", {"decision": "reject", "note": "not now"})
    assert snapshot() == before and "change_request_rejected" in [e[0] for e in events(rid)]


def test_multi_document_publication_failure_publishes_nothing_and_retry_recovers(api, monkeypatch):  # noqa: F811
    from app.knowledge import release
    from app.knowledge.errors import Conflict
    F.seed_ds(api, {"brochure": ("Brochure", "- Machine Learning faculty: Jyothi sir"), "faq": ("FAQ", "A: The Machine Learning faculty is Jyothi sir.")})
    req = analyse(api, monkeypatch)
    A.accept_all(api, req)
    A.submit(api, req["request_id"])
    A.approve(api, req["request_id"])
    before = snapshot()
    real = release.pipeline.publish_version
    seen = []

    def flaky(db, tenant, actor, doc_id, version_no, embedder_name, **kw):
        seen.append(doc_id)
        if len(seen) == 3:
            raise Conflict("simulated failure")
        return real(db, tenant, actor, doc_id, version_no, embedder_name, **kw)
    monkeypatch.setattr(release.pipeline, "publish_version", flaky)
    out = A.publish(api, req["request_id"])["request"]
    assert out["status"] == "failed"
    with session_scope() as db:
        pub = {d.doc_id: d.published_version for d in db.execute(select(KbDocument).where(KbDocument.tenant_id == T)).scalars()}
    assert pub == {"data-science": 1, "course": 1, "brochure": 1, "faq": 1}                   # nothing partially published
    monkeypatch.setattr(release.pipeline, "publish_version", real)
    assert A.publish(api, req["request_id"])["request"]["status"] == "published"
    assert A.live_text("faq") == "A: The Machine Learning faculty is Santhosh sir."
