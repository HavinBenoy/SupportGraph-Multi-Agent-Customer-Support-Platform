"""OPTIONAL: the impact analysis with the REAL local model (Ollama). Skipped unless SG_REAL_IMPACT=1. Never part of the normal suites; run it with `make eval-impact REAL=1`.

SG_REAL_IMPACT_MODELS="qwen3:1.7b,qwen3:4b-instruct-2507-q4_K_M" evaluates several local models (default: the configured SG_LLM_MODEL_IMPACT / SG_LLM_MODEL).
The scenarios are the one that failed in real use ("the machine learning faculty is changed to santhosh sir from today" vs "Machine Learning faculty is Jyothi sir"), the fee change
with a historical record and an unrelated course, and a different-subject trap. Each asserts the SAFETY properties that must hold for any model (the right document is affected with
a grounded replacement, nothing historical or unrelated is edited), and prints what the model itself answered so its quality can be judged. If the model does not answer, the
test is skipped with the reason: a skipped test is not a pass."""

import os

import pytest

from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge import faculty as F
from tests.knowledge.institute import seed
from tests.knowledge.kbhelp import go_live
from tests.rag.conftest import embedder, ingested  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("SG_REAL_IMPACT") != "1", reason="set SG_REAL_IMPACT=1 (and run Ollama) to test the real model")
MODELS = [m.strip() for m in os.environ.get("SG_REAL_IMPACT_MODELS", "").split(",") if m.strip()] or [""]


def run_real(api, model, description):
    s = A.inline(api)
    s.kb_ai_mode = "llm"
    if model:
        s.llm_model_impact = model
    rid = A.start_ai(api, description)["request_id"]
    A.analyse(api, rid)
    req = A.get(api, rid)
    an = req["analysis"] or {}
    print(f"\n[{model or 'configured model'}] {description!r}\n  status={req['status']} error={an.get('error')} summary={an.get('summary')}")
    if req["status"] == "draft":
        pytest.skip("the real model did not answer: " + str(an.get("error")))
    for it in req["items"]:
        ai = it["ai"] or {}
        print(f"  {it['doc_id']}: {it['classification']} conf={it['confidence']} edits={[e['replacement'] for e in it['final_edits'] or []]}")
        print(f"     reason={ai.get('reason')!r}")
        for ps in ai.get("passages") or []:
            if ps.get("model_note"):
                print(f"     (model said: {ps['model_note']!r})")
    return req


@pytest.mark.parametrize("model", MODELS)
def test_real_model_the_reported_faculty_change(api, model):  # noqa: F811
    F.seed_ds(api)
    req = run_real(api, model, F.CHANGE)
    ds = next(i for i in req["items"] if i["doc_id"] == "data-science")
    assert ds["classification"] in ("definite", "potential"), "the faculty document must not be 'related, no change'"
    assert [e["replacement"] for e in ds["final_edits"]] == ["Machine Learning faculty is Santhosh sir"]
    assert not any(i["final_edits"] for i in req["items"] if i["doc_id"] == "course")
    reason = (ds["ai"]["reason"] or "").lower()
    assert "different subject" not in reason and "no change" not in reason


@pytest.mark.parametrize("model", MODELS)
def test_real_model_fee_change_keeps_history_and_other_courses(api, model):  # noqa: F811
    seed(api)
    req = run_real(api, model, A.FEE_CHANGE)
    by = {i["doc_id"]: i for i in req["items"]}
    assert by["python-fees"]["classification"] in ("definite", "potential") and by["python-fees"]["final_edits"]
    assert all("48,000" in e["replacement"] and "45,000" not in e["replacement"] for e in by["python-fees"]["final_edits"])
    for doc in ("fee-archive", "java-fees", "python-faculty"):
        assert doc not in by or by[doc]["final_edits"] == [], doc
    assert "Fee history" not in "".join(e["replacement"] for e in by["python-fees"]["final_edits"])


@pytest.mark.parametrize("model", MODELS)
def test_real_model_same_value_other_subject_is_not_edited(api, model):  # noqa: F811
    F.seed_ds(api, {"py": ("Python Full Stack Faculty", "Python Full Stack faculty is Jyothi sir")})
    req = run_real(api, model, F.CHANGE)
    py = next((i for i in req["items"] if i["doc_id"] == "py"), None)
    assert py is None or py["final_edits"] == []
