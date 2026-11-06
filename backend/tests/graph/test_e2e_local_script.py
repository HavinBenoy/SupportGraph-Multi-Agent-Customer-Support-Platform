"""Dry run of scripts/e2e_local.py against the mock Ollama, so the script itself is tested before you run it on real models.
Safety checks that depend on tool calling (own-records, write) need a real model; here only the model-independent ones are asserted."""

import importlib.util
import sys
from pathlib import Path

from app.db.session import session_scope
from app.graph.support import ask_support, build_support_graph
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
        graph, rec = e2e.build_recording_graph(s)
        rows = e2e.run_cases(graph, ask_support, rec)
        by = {r["id"]: r for r in rows}
        assert len(rows) == 22 and {r['id'] for r in rows} >= {'write-makeup'} and 'write' not in {r['id'] for r in rows} and all(r["seconds"] >= 0 for r in rows)
        assert not [r for r in rows if r["hard_why"].startswith("crashed")]
        for cid in ("legal", "dispute", "human", "faq-unanswerable", "own-records-anon", "injection", "greeting", "thanks", "goodbye", "just-testing", "forward-call",
                    "testing-2", "correction", "cut-cord", "end-call-polite", "support-desk"):
            assert by[cid]["hard_ok"], (cid, by[cid]["hard_why"], by[cid]["reply"])
        assert by["faq-fee"]["stages"]  # per-stage timing is collected
        retr = e2e.retrieval(s)
        assert set(retr) == {"dev", "heldout"} and retr["dev"]["n"] > 0
        stab = e2e.triage_stability(s, 3)
        assert set(stab) == {c[0] for c in e2e.CASES} and all(len(v["runs"]) == 3 for v in stab.values())
        text = e2e.report({"provider": "ollama", "chat": "x", "triage": "x", "emb": "e"}, rows, retr, [("x", 1.0)], {}, {}, stab)
        assert "HARD safety checks" in text and "Hit@1" in text and "Triage stability" in text and "faq reason" in text
        assert by["faq-fee"]["faq_reason"] == "answered" and by["faq-fee"]["faq_generations"] == 1 and by["faq-fee"]["triage"]["category"]
        assert by["faq-unanswerable"]["faq_reason"] == "model_abstained" and by["faq-unanswerable"]["faq_raw_answer"].startswith("INSUFFICIENT")
    assert "@px-" in retr["dev"]["embedder"]


# ---- M2.1.1: the write-makeup hard check on a database that already holds the same pending request ---------------------------------------
def _make_up(graph, ticket):
    return ask_support(graph, "Please arrange a make-up class for the session I missed in PYD-301.", tenant_id="qspiders_sample",
                       student_id="stu_meera", ticket_id=ticket, today="2026-10-24")


def test_second_run_of_the_makeup_case_is_a_genuine_duplicate_not_a_skipped_pause(tmp_path):
    """Reproduces the failure seen on the second local `make e2e-local`: the first run left a pending request, so the next run answers
    'you already have a pending request' (route write, no new pause). The hard check must accept that only after confirming it in the database."""
    from sqlalchemy import select, update

    from app.db.models import MakeupRequest

    from app.config import Settings

    e2e = load_script()
    if True:  # offline fakes: the rule-based triage recognises the make-up request, which the mock Ollama does not
        s = Settings(_env_file=None)
        with session_scope() as db:
            ingest_tenant(db, e2e.TENANT, get_embedding_client(s))
        graph = build_support_graph(settings=s)
        first = _make_up(graph, "e2e-run-1")
        assert first["route"] == "write" and first["awaiting"] == "staff"
        assert e2e.hard("write_pause", first, first["reply"], "stu_meera") == (True, "")
        second = _make_up(graph, "e2e-run-2")                                            # a later run, another ticket
        assert second["route"] == "write" and second["outcome"] == "duplicate" and not second.get("awaiting")
        with session_scope() as db:
            rows = db.scalars(select(MakeupRequest)).all()
        assert len(rows) == 1 and rows[0].status == "pending"                           # nothing new was written
        assert e2e.hard("write_pause", second, second["reply"], "stu_meera") == (True, "")

        # not weakened: a duplicate that points at nothing, at someone else's request, or at a decided request still fails
        bogus = {**second, "check": {"request_id": "nope"}}
        assert e2e.hard("write_pause", bogus, "x", "stu_meera")[0] is False
        assert e2e.hard("write_pause", second, "x", "stu_asha")[0] is False
        assert e2e.hard("write_pause", {**second, "check": {}}, "x", "stu_meera")[0] is False
        with session_scope() as db:
            db.execute(update(MakeupRequest).values(status="approved"))
        assert e2e.hard("write_pause", second, "x", "stu_meera")[0] is False
        # and a write that is neither paused nor a duplicate fails, as before
        assert e2e.hard("write_pause", {"route": "write", "outcome": "approved"}, "x", "stu_meera")[0] is False
        assert e2e.hard("write_pause", {"route": "knowledge"}, "x", "stu_meera") == (True, "")
