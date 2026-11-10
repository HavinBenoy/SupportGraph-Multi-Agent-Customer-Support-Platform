"""Part 2 end to end on the realistic corpus: analysis -> decisions -> approval -> publish -> follow-up check, over HTTP and the database. v1 against v2, stale versions,
a publication failure, residual outdated references and the permissions of the verify endpoint."""

from sqlalchemy import select

from app.db.models import KbVersion
from app.db.session import session_scope
from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge import qs_corpus as Q
from tests.knowledge.kbhelp import go_live
from tests.knowledge.kbhelp import BASE, admin
from tests.knowledge.kwhelp import staff, student
from tests.rag.conftest import embedder, ingested  # noqa: F401


def seed_qs(api):
    for doc_id, (title, content, _path) in Q.DOCS.items():
        go_live(api, doc_id=doc_id, content=content, title=title, category="courses")


def analysed(api, desc, mode="v2"):
    api.app.state.settings.kb_ai_discovery = mode
    return A.analysed(api, desc)


def proposed_lines(req):
    out = {}
    for it in req["items"]:
        for e in (it.get("ai") or {}).get("edits") or []:
            out[(it["doc_id"], e["original"])] = e["replacement"]
    return out


def test_v2_proposes_every_reworded_passage_over_http_and_v1_does_not(api):  # noqa: F811
    seed_qs(api)
    got = {m: proposed_lines(analysed(api, Q.NEW_ONLY, m)) for m in ("v1", "v2")}
    want = {k: v for k, v in Q.EDITS.items()}
    assert all(got["v2"].get(k) == v for k, v in want.items())
    assert sum(1 for k in want if got["v1"].get(k) == want[k]) < len(want)
    assert not any(k in got["v2"] for k in Q.KEEP)


def test_multiple_sections_in_one_document_become_one_item_with_several_precise_edits(api):  # noqa: F811
    seed_qs(api)
    req = analysed(api, Q.OLD_KNOWN)
    it = A.item(req, "ml-paraphrases")
    assert len(it["ai"]["edits"]) == 3 and it["classification"] in ("definite", "potential")
    body = it["proposed_content"] if "proposed_content" in it else None
    assert body is None or "Our experienced instructor conducts the machine learning module." in body


def test_publish_then_follow_up_check_reports_clean_or_what_the_reviewer_left(api):  # noqa: F811
    seed_qs(api)
    req = analysed(api, Q.OLD_KNOWN)
    rid = req["request_id"]
    for it in req["items"]:
        if it["actionable"]:
            has = bool((it["ai"] or {}).get("edits"))
            A.decide(api, rid, it["doc_id"], "accepted" if has and it["doc_id"] != "announcements" else "discarded", reason="keep")
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    A.publish(api, rid)
    done = A.a_get(api, rid)
    assert done["status"] == "published"
    v = done["release"]["verification"]
    assert v and v["clean"] is False and v["unreviewed"] == 0 and v["documents_scanned"] >= len(Q.DOCS) - 1
    rem = {(r["doc_id"], r["text"]) for r in v["remaining"]}
    assert ("announcements", "Mr. Jyothi will conduct the machine-learning bootcamp on weekends.") in rem
    assert next(r for r in v["remaining"] if r["doc_id"] == "announcements")["left_by_reviewer"] is True
    assert not any(d in ("faculty-history", "jyothi-profile") for d, _ in rem)               # historical and other-subject lines are not residue
    assert A.live_text("ml-module").count("Santhosh sir") == 1 and "Jyothi sir" not in A.live_text("ml-module")


def test_verify_is_admin_only_and_only_after_publication(api):  # noqa: F811
    seed_qs(api)
    req = analysed(api, Q.OLD_KNOWN)
    rid = req["request_id"]
    for it in req["items"]:
        if it["actionable"]:
            A.decide(api, rid, it["doc_id"], "accepted" if (it["ai"] or {}).get("edits") else "discarded")
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    assert api.post(f"{BASE}/changes/{rid}/verify", headers=admin(api)).status_code == 409
    A.publish(api, rid)
    assert api.post(f"{BASE}/changes/{rid}/verify", headers=admin(api)).status_code == 200
    assert api.post(f"{BASE}/changes/{rid}/verify", headers=staff(api)).status_code in (401, 403)
    assert api.post(f"{BASE}/changes/{rid}/verify", headers=student(api)).status_code in (401, 403)


def test_a_failing_follow_up_check_never_undoes_a_published_release(api, monkeypatch):  # noqa: F811
    from app.knowledge.impact import verify
    seed_qs(api)
    req = analysed(api, Q.OLD_KNOWN)
    rid = req["request_id"]
    for it in req["items"]:
        if it["actionable"]:
            A.decide(api, rid, it["doc_id"], "accepted" if (it["ai"] or {}).get("edits") else "discarded")
    A.submit(api, rid)
    A.a_post(api, rid, "start-review")
    A.approve(api, rid)
    monkeypatch.setattr(verify, "compute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("scan crashed")))
    A.publish(api, rid)
    done = A.a_get(api, rid)
    assert done["status"] == "published" and "Santhosh sir" in A.live_text("ml-module")


def test_the_section_judge_runs_inside_the_real_analysis_flow_and_its_verdict_is_shown(api, monkeypatch):  # noqa: F811
    """The reported demo case through HTTP: the document the staff screenshot showed. The rules hold back a line (the person is named in another block); the scripted model, asked the way
    a person would ask it in a terminal, says it applies; the reviewer sees a potential edit with the model's opinion, nothing is applied."""
    from langchain_core.messages import AIMessage

    from app.knowledge.impact import judge as J
    from app.knowledge.impact.evaluate import LlmImpactModel

    J._CACHE.clear()

    class Chat:
        def __init__(self):
            self.n = 0

        def with_structured_output(self, _):
            return self

        def invoke(self, msgs):
            self.n += 1
            return AIMessage("```text\nSanthosh sir is our Machine Learning faculty.\n\nHis total work experience is 5 years.\n```")

    chat = Chat()
    monkeypatch.setattr("app.api.routes.knowledge_workspace.get_impact_model", lambda s: LlmImpactModel(chat, name="stub", judge_chat=chat))
    go_live(api, doc_id="ds", content="# Data-Science\n\nSanthosh sir is our Machine Learning faculty.\n\nHis total work experience is 3 years.\n", title="Data-Science", category="courses")
    req = analysed(api, "change the work experience of santhosh sir from 3 years to 5 years")
    it = A.item(req, "ds")
    assert chat.n == 1 and it["classification"] == "potential"
    passage = it["ai"]["passages"][0]
    assert passage["judge"] == "agrees" and passage["replacement"] == "His total work experience is 5 years."
    assert it["ai"]["edits"][0]["original"] == "His total work experience is 3 years."
    assert A.live_text("ds").count("3 years") == 1                       # analysis never edits the published text
