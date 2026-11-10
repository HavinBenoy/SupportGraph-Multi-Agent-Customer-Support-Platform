"""Generalized editing through the REAL application (HTTP, database, release pipeline) with a scripted model. The model is a stub that edits the section it is shown the way a good Qwen
would; everything else (routes, analysis job, validation, decisions, approval, atomic publication, post-publication check, RBAC) is the real code.

What these tests do NOT show: how a real Qwen3 1.7B / 4B behaves. That is a separate, manual check (scripts/judge_probe.py, docs/PART2_FINAL.md)."""

import re

from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db.models import KbVersion
from app.db.session import session_scope
from app.knowledge.impact import judge as J
from app.knowledge.impact.evaluate import LlmImpactModel
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.kbhelp import go_live
from tests.knowledge.kwhelp import staff, student
from tests.rag.conftest import embedder, ingested  # noqa: F401

DS = """# Data Science

## Faculty

Machine Learning faculty is Santhosh sir.
He has a work experience of 3 years. Previously he worked in IT.

## Eligibility

- Any graduate
- Age under 30
- Online classes only

## Fees

| Course | Fee |
|---|---|
| Data Science | 15000 |
"""
WEB = """# Web Development

## Faculty

Web faculty is Rao sir. He has a work experience of 7 years.

## Eligibility

- 12th pass
- Classes are conducted offline
"""
HIST = """# Data Science Archive

## History

Santhosh sir had 3 years of experience in 2020. Online classes only were offered in 2021.
"""


class Editor:
    """A scripted model: finds the section in the prompt and returns it with the given line replacements applied (or a canned reply)."""

    def __init__(self, changes=None, reply=None):
        self.changes, self.reply, self.calls = changes or [], reply, []

    def with_structured_output(self, _):
        return self

    def invoke(self, msgs):
        text = msgs[0].content
        self.calls.append(text)
        if self.reply is not None:
            return AIMessage(self.reply)
        m = re.search(r"<section>\n(.*?)\n</section>", text, re.S)
        if not m:                                                       # a structured-output (parse) call: unusable, so the rules / general path decide
            return AIMessage("{}")
        sec = m.group(1)
        for old, new in self.changes:
            sec = sec.replace(old, new)
        return AIMessage("Thinking done.\n```text\n" + sec + "\n```")


def use(monkeypatch, chat):
    J._CACHE.clear()
    monkeypatch.setattr("app.api.routes.knowledge_workspace.get_impact_model", lambda s: LlmImpactModel(chat, name="stub:editor", judge_chat=chat))


def seed(api, **docs):
    for doc_id, (title, content) in docs.items():
        go_live(api, doc_id=doc_id, content=content, title=title, category="courses")


def analysed(api, desc, **kw):
    api.app.state.settings.kb_ai_discovery = "v2"
    api.app.state.settings.kb_ai_judge = "all"
    return A.analysed(api, desc, **kw)


def release(api, req, accept=True):
    rid = req["request_id"]
    for it in req["items"]:
        if it["actionable"] and (it["ai"] or {}).get("edits"):
            A.decide(api, rid, it["doc_id"], "accepted" if accept else "discarded")
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    A.publish(api, rid)
    return A.a_get(api, rid)


def test_add_information_that_is_absent_from_the_document_end_to_end(api, monkeypatch):  # noqa: F811
    chat = Editor([("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.")])
    use(monkeypatch, chat)
    seed(api, ds=("Data Science", DS), web=("Web Development", WEB))
    req = analysed(api, "Santhosh sir now also holds an MTech in AI")
    assert [i["doc_id"] for i in req["items"] if i["actionable"]] == ["ds"]                       # the unrelated document is not touched
    it = A.item(req, "ds")
    e = it["ai"]["edits"][0]
    assert it["classification"] == "potential" and e["kind"] == "add" and e["replacement"].endswith("He holds an MTech in AI.")      # a model-only change is never "definite"
    assert A.live_text("ds").count("MTech") == 0                                                  # an analysis never edits the published text
    assert it["proposed_content"].count("He holds an MTech in AI.") == 1
    done = release(api, req)
    assert done["status"] == "published"
    live = A.live_text("ds")
    assert "He holds an MTech in AI." in live and "Previously he worked in IT." in live and "3 years" in live and "| Data Science | 15000 |" in live
    v = done["release"]["verification"]
    assert v["mode"] == "statements" and v["verifiable"] is False and v["confirmed_new"] == 1 and v["clean"] is True


def test_remove_an_outdated_restriction_keeps_the_rest_of_the_list(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("- Age under 30\n", "")]))
    seed(api, ds=("Data Science", DS), web=("Web Development", WEB))
    req = analysed(api, "Remove the age restriction from the eligibility criteria")
    it = A.item(req, "ds")
    assert it["ai"]["edits"][0]["kind"] == "remove"
    done = release(api, req)
    live = A.live_text("ds")
    assert done["status"] == "published" and "Age under 30" not in live and "Any graduate" in live and "Online classes only" in live


def test_online_to_hybrid_changes_only_the_document_where_it_applies(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("- Online classes only", "- Hybrid classes (online and offline)")]))
    seed(api, ds=("Data Science", DS), web=("Web Development", WEB))
    req = analysed(api, "change online classes to hybrid classes")
    docs = [i["doc_id"] for i in req["items"] if i["actionable"] and (i["ai"] or {}).get("edits")]
    assert docs == ["ds"]
    assert release(api, req)["status"] == "published"
    assert "Hybrid classes (online and offline)" in A.live_text("ds") and "Classes are conducted offline" in A.live_text("web")


def test_historical_records_are_never_edited_even_when_the_model_does(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("3 years", "5 years"), ("Online classes only were offered in 2021.", "Hybrid classes were offered in 2021.")]))
    seed(api, hist=("Data Science Archive", HIST))
    req = analysed(api, "change online classes only to hybrid classes")
    assert not [i for i in req["items"] if (i["ai"] or {}).get("edits")]
    assert "Online classes only were offered in 2021." in A.live_text("hist")


def test_a_wrong_or_incomplete_model_answer_creates_no_proposal(api, monkeypatch):  # noqa: F811
    seed(api, ds=("Data Science", DS))
    for reply in ("```text\nHere is half", "I cannot help with that.", "```text\n# Data Science\n\n## Faculty\n```", "```text\n" + DS.replace("15000", "99999") + "\n```"):
        use(monkeypatch, Editor(reply=reply))
        req = analysed(api, "Santhosh sir now also holds an MTech in AI")
        assert not [i for i in req["items"] if (i["ai"] or {}).get("edits")], reply


def test_a_good_change_mixed_with_an_invented_one_keeps_only_the_good_part_and_asks_a_person(api, monkeypatch):  # noqa: F811
    mixed = "```text\n" + DS.replace("15000", "99999").replace("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.") + "\n```"
    use(monkeypatch, Editor(reply=mixed))
    seed(api, ds=("Data Science", DS))
    it = A.item(analysed(api, "Santhosh sir now also holds an MTech in AI"), "ds")
    assert it["classification"] == "potential" and len(it["ai"]["edits"]) == 1 and "99999" not in it["proposed_content"] and "He holds an MTech in AI." in it["proposed_content"]
    assert any("dropped" in u for u in it["ai"]["uncertainty"]) or any("dropped" in f for f in it["ai"]["edits"][0]["flags"])


def test_a_vague_request_asks_a_question_instead_of_editing(api, monkeypatch):  # noqa: F811
    chat = Editor([("x", "y")])
    use(monkeypatch, chat)
    seed(api, ds=("Data Science", DS))
    req = analysed(api, "We changed the Data Science eligibility.")
    assert req["status"] == "awaiting_clarification" and not any("<section>" in c for c in chat.calls) and A.live_text("ds").count("Age under 30") == 1


def test_a_document_published_after_the_analysis_cannot_be_accepted_or_published_stale(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.")]))
    seed(api, ds=("Data Science", DS))
    req = analysed(api, "Santhosh sir now also holds an MTech in AI")
    from tests.knowledge.test_ai_flow import republish
    republish(api, "ds", DS + "\nNew line.\n")                                                                          # a newer version goes live meanwhile
    r = api.post(f"{A.ST}/changes/{req['request_id']}/items/ds/decision", json={"decision": "accepted"}, headers=staff(api))
    assert r.status_code == 409 and "published again" in r.text
    assert "MTech" not in A.live_text("ds")


def test_concurrent_requests_on_one_document_second_cannot_overwrite_the_first(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.")]))
    seed(api, ds=("Data Science", DS))
    a = analysed(api, "Santhosh sir now also holds an MTech in AI")
    J._CACHE.clear()
    b = analysed(api, "Santhosh sir now also holds an MTech in AI", h=staff(api, "staff2"))
    assert release(api, a)["status"] == "published"
    r = api.post(f"{A.ST}/changes/{b['request_id']}/items/ds/decision", json={"decision": "accepted"}, headers=staff(api, "staff2"))
    assert r.status_code == 409
    assert A.live_text("ds").count("He holds an MTech in AI.") == 1


def test_a_publication_failure_publishes_nothing_and_a_retry_recovers(api, monkeypatch):  # noqa: F811
    from app.knowledge import release as R
    from app.knowledge.errors import Conflict

    use(monkeypatch, Editor([("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.")]))
    seed(api, ds=("Data Science", DS))
    req = analysed(api, "Santhosh sir now also holds an MTech in AI")
    rid = req["request_id"]
    A.decide(api, rid, "ds", "accepted")
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    real = R.pipeline.publish_version
    monkeypatch.setattr(R.pipeline, "publish_version", lambda *a, **k: (_ for _ in ()).throw(Conflict("simulated failure")))
    out = A.publish(api, rid)["request"]
    assert out["status"] == "failed" and "MTech" not in A.live_text("ds")
    monkeypatch.setattr(R.pipeline, "publish_version", real)
    assert A.publish(api, rid)["request"]["status"] == "published" and "MTech" in A.live_text("ds")


def test_staff_can_edit_the_models_revision_and_the_diff_is_rebuilt_by_the_server(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.")]))
    seed(api, ds=("Data Science", DS))
    req = analysed(api, "Santhosh sir now also holds an MTech in AI")
    rid = req["request_id"]
    mine = "Previously he worked in IT.\nHe holds an MTech in Artificial Intelligence (IIT)."
    out = A.decide(api, rid, "ds", "manual", replacements=[{"index": 0, "replacement": mine}])
    assert "Artificial Intelligence (IIT)" in out["proposed_content"] and out["decision"] == "manual" and out["diff"]["body"]
    assert "MTech" not in A.live_text("ds")


def test_permissions_and_tenant_isolation_are_unchanged(api, monkeypatch):  # noqa: F811
    use(monkeypatch, Editor([("Previously he worked in IT.", "Previously he worked in IT.\nHe holds an MTech in AI.")]))
    seed(api, ds=("Data Science", DS))
    req = analysed(api, "Santhosh sir now also holds an MTech in AI")
    rid = req["request_id"]
    assert api.post(f"{A.ST}/changes/{rid}/items/ds/decision", json={"decision": "accepted"}, headers=student(api)).status_code in (401, 403)
    assert api.post(f"{A.BASE}/changes/{rid}/verify", json={}, headers=staff(api)).status_code in (401, 403)
    assert api.get(f"{A.ST}/changes/{rid}").status_code in (401, 403)
    with session_scope() as db:
        assert db.execute(select(KbVersion).where(KbVersion.tenant_id == T, KbVersion.doc_id == "ds", KbVersion.version_no == 2)).first() is None
