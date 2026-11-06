"""Dry run of scripts/e2e_local.py against the mock Ollama, so the script itself is tested before you run it on real models.
Safety checks that depend on tool calling (own-records, write) need a real model; here only the model-independent ones are asserted."""

import importlib.util
import sys
from pathlib import Path

from app.db.session import session_scope
from app.graph.support import ask_support, build_support_graph
from app.llm.fake import ScriptedChatModel
from app.llm.gateway import get_embedding_client
from app.rag.ingest import ingest_tenant
from tests.bench.mock_ollama import MockOllama
from tests.graph.test_local_stack_mock import stack_settings


def load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "e2e_local.py"
    spec = importlib.util.spec_from_file_location("e2e_local", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_local"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_runs_end_to_end_against_the_mock(tmp_path):
    e2e = load_script()
    with MockOllama() as m:
        s = stack_settings(m.url)
        with session_scope() as db:
            ingest_tenant(db, e2e.TENANT, get_embedding_client(s))
        graph = build_support_graph(settings=s, action_model=ScriptedChatModel())
        rows = e2e.run_cases(graph, ask_support)
        by = {r["id"]: r for r in rows}
        assert len(rows) == 12 and all(r["seconds"] >= 0 for r in rows)
        assert not [r for r in rows if r["hard_why"].startswith("crashed")]
        for cid in ("legal", "dispute", "human", "faq-unanswerable", "own-records-anon", "injection"):
            assert by[cid]["hard_ok"], (cid, by[cid]["hard_why"], by[cid]["reply"])
        assert by["faq-fee"]["stages"]  # per-stage timing is collected
        retr = e2e.retrieval(s)
        assert set(retr) == {"dev", "heldout"} and retr["dev"]["n"] > 0
        text = e2e.report({"provider": "ollama", "chat": "x", "triage": "x", "emb": "e"}, rows, retr, [("x", 1.0)], {}, {})
        assert "HARD safety checks" in text and "Hit@1" in text
    assert "@px-" in retr["dev"]["embedder"]
