"""OPTIONAL: the impact analysis with the REAL local model (Ollama, settings.llm_model_impact). Skipped unless SG_REAL_IMPACT=1 and Ollama answers; never part of the normal suites.
Run with `make eval-impact`. It checks the safety properties that must hold for ANY model (guards), and prints what the model proposed; it does not assert model quality."""

import os

import pytest

from tests.api.conftest import T, api, auth  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import aihelp as A
from tests.knowledge.institute import DOCS, seed
from tests.rag.conftest import embedder, ingested  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("SG_REAL_IMPACT") != "1", reason="set SG_REAL_IMPACT=1 (and run Ollama) to test the real model")


def test_real_model_respects_every_guard(api):  # noqa: F811
    seed(api)
    s = A.inline(api)
    s.kb_ai_mode = "llm"
    rid = A.start_ai(api)["request_id"]
    A.analyse(api, rid)
    req = A.get(api, rid)
    print("\nstatus:", req["status"], "summary:", (req["analysis"] or {}).get("summary"), "error:", (req["analysis"] or {}).get("error"))
    if req["status"] == "draft":
        pytest.skip("the real model did not answer: " + str((req["analysis"] or {}).get("error")))
    for it in req["items"]:
        edits = (it["ai"] or {}).get("edits") or []
        print(it["doc_id"], it["classification"], it["confidence"], [e["replacement"] for e in edits])
        for e in edits:
            assert "48" in e["replacement"] and "45,000" not in e["replacement"] and "\n" not in e["replacement"]
    assert not any(i["classification"] in ("definite", "potential") and i["doc_id"] in {d for d in DOCS if "history" in d or "archive" in d} for i in req["items"])
