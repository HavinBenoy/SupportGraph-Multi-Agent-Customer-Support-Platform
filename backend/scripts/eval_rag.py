"""Evaluate the RAG FAQ baseline on the labelled tickets and print the numbers.

    make eval-rag                                   # or: cd backend && python scripts/eval_rag.py
    python scripts/eval_rag.py --out ../evals/reports/rag_baseline.json

Needs ingested documents (make ingest). Exit code 1 if ANY answer leaked staff-only or other-user text.
Read the caveat in app/rag/evaluate.py: with the default offline fakes these numbers test the plumbing, not language
understanding. Re-run with SG_EMBEDDING_PROVIDER / SG_LLM_PROVIDER set to a real model before quoting them, and use the
printed `best similarity` values to calibrate SG_RAG_MIN_SCORE.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db.session import session_scope
from app.rag.evaluate import internal_markers, load_tickets, run_eval, run_retrieval_eval
from app.rag.retrieval import search
from app.rag.faq import answer_question, build_faq_graph
from app.llm.gateway import get_embedding_client

DATASET = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "tickets_qspiders_v1.jsonl"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--out", type=Path, help="also write the full JSON report here")
    a = p.parse_args()
    s = get_settings()

    graph = build_faq_graph()
    tickets = load_tickets(a.dataset)
    embedder = get_embedding_client(s)

    def rank(t):  # retrieval alone, no score threshold, so it can be judged separately from the answer
        with session_scope() as db:
            hits = search(db, tenant_id=t["tenant_id"], query_vector=embedder.embed_query(t["message"]),
                          model_name=embedder.name, top_k=s.rag_top_k)
        return [h.doc_id for h in hits]

    try:
        retrieval = run_retrieval_eval(rank, tickets, k=s.rag_top_k)
        report = run_eval(
            lambda t: answer_question(graph, t["message"], tenant_id=t["tenant_id"], ticket_id=f"eval-{t['id']}",
                                      student_id=t["student_id"]),
            tickets, markers=internal_markers(tickets[0]["tenant_id"]))
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup ingest", file=sys.stderr)
        return 2

    m = report.metrics
    print(f"RAG FAQ baseline  |  embeddings: {s.embedding_provider}  chat model: {s.llm_provider}  "
          f"top_k={s.rag_top_k}  min_score={s.rag_min_score}")
    if "fake" in (s.embedding_provider, s.llm_provider):
        print("NOTE: offline fakes in use. These numbers test the plumbing, not real language understanding.")
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"  # noqa: E731
    print(f"  retrieval ({retrieval['tickets']} answerable): right doc first {pct(retrieval['hit_at_1'])}, "
          f"in top {retrieval['k']} {pct(retrieval['hit_at_k'])}, MRR {retrieval['mrr']}"
          + (f"  missed: {' '.join(retrieval['missed'])}" if retrieval["missed"] else ""))
    print(f"  answerable questions : {m['answerable']}")
    print(f"    answered           : {pct(m['answered_rate'])}")
    print(f"    correct (all facts): {pct(m['correct_rate'])}")
    print(f"    wrong among answers: {pct(m['wrong_answer_rate'])}")
    print(f"  unsupported numbers  : {pct(m['unsupported_claim_rate'])} of answers state a number their sources lack")
    print(f"  must-abstain         : {m['must_abstain']}   correctly abstained: {pct(m['abstain_rate'])}")
    print(f"  leaks (all {m['tickets_run']} tickets): {m['leaks']}   <- must be 0")
    for r in report.results:
        problem = (r.leaked and f"LEAK {r.leaked}") or (r.kind == "answerable" and (
            (not r.answered and f"abstained ({r.reason})") or (r.facts_missing and f"missing {r.facts_missing}")))
        if r.kind == "must_abstain" and r.answered:
            problem = "answered but should abstain"
        if problem:
            print(f"    {r.id}: {problem} | {r.message[:60]}")
    if a.out:
        a.out.write_text(report.to_json(retrieval), encoding="utf-8")
        print(f"full report: {a.out}")
    return 1 if m["leaks"] else 0


if __name__ == "__main__":
    sys.exit(main())
