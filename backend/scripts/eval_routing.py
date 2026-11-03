"""Evaluate triage + routing + the branches end to end on the labelled tickets.

    make eval-routing          # or: cd backend && python scripts/eval_routing.py --out ../evals/reports/routing.json

Also compares the knowledge branch (corrective RAG) with the step 2 single-pass FAQ baseline on the same questions.
Needs ingested documents. Exit 1 on any leak, unauthorized tool call or write without a decision. The run creates tickets/requests/audit rows under
`eval-<id>-<run>` ticket ids and deletes exactly those afterwards (nothing else is touched). Read the caveat in app/graph/evaluate.py: with the
offline fakes these numbers test plumbing and rule-fit, not real language understanding.
"""

import argparse
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db.models import AuditLog, Escalation, MakeupRequest, Ticket
from app.db.session import session_scope
from app.graph.evaluate import from_state, run_routing_eval
from app.graph.support import ask_support, build_support_graph
from app.rag.evaluate import internal_markers, load_tickets, run_eval
from app.rag.faq import FaqAnswer, answer_question, build_faq_graph

DATASET = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "tickets_qspiders_v1.jsonl"


EXECUTED = ("makeup_request.approved", "makeup_request.rejected", "contact_phone.updated", "contact_phone.declined")


def writes_without_decision(tenant_id: str, ticket_ids: list[str]) -> list[str]:
    """Tickets of THIS run where a decision was applied. The evaluation never approves anything, so any hit is a bug."""
    with session_scope() as db:
        return sorted({a.ticket_id for a in db.execute(select(AuditLog).where(
            AuditLog.tenant_id == tenant_id, AuditLog.ticket_id.in_(ticket_ids), AuditLog.action.in_(EXECUTED))).scalars()})


def cleanup(tenant_id: str, ticket_ids: list[str]) -> None:
    """Remove ONLY the rows this run created (its own `eval-...-<run>` ticket ids). Nothing else is touched."""
    with session_scope() as db:
        for model in (AuditLog, Escalation, MakeupRequest):
            col = model.ticket_id
            db.execute(delete(model).where(model.tenant_id == tenant_id, col.in_(ticket_ids)))
        db.execute(delete(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.id.in_(ticket_ids)))


def seed_today(tenant_id: str) -> str:
    """The sample data's own "today": the day after its last recorded session (the seed puts every batch's last session on
    as_of - 1). Evaluating on that date keeps the date-dependent tickets (make-up window) valid however long ago you seeded."""
    from sqlalchemy import func

    from app.db.models import AttendanceRecord

    with session_scope() as db:
        last = db.scalar(select(func.max(AttendanceRecord.session_date)).where(AttendanceRecord.tenant_id == tenant_id))
    return (last + timedelta(days=1)).isoformat() if last else date.today().isoformat()


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--out", type=Path)
    p.add_argument("--today", help="ISO date to evaluate as (default: the sample data's own today)")
    a = p.parse_args()
    s = get_settings()
    tickets = load_tickets(a.dataset)
    markers = internal_markers(tickets[0]["tenant_id"])
    graph, faq = build_support_graph(), build_faq_graph()
    states: dict[str, dict] = {}
    run_id = uuid.uuid4().hex[:6]
    today = a.today or seed_today(tickets[0]["tenant_id"])

    def run(t):
        if t["id"] not in states:
            states[t["id"]] = ask_support(graph, t["message"], tenant_id=t["tenant_id"], student_id=t["student_id"],
                                          ticket_id=f"eval-{t['id']}-{run_id}", today=today)
        return from_state(states[t["id"]])

    ticket_ids = [f"eval-{t['id']}-{run_id}" for t in tickets]
    try:
        m = run_routing_eval(run, tickets, markers=markers)
        m["writes_without_decision"] = writes_without_decision(tickets[0]["tenant_id"], ticket_ids)
        knowledge = [t for t in tickets if t["expected_route"] == "knowledge"]

        def corrective(t):  # what the support graph's answer would count as for the step 2 metrics
            r = run(t)
            return FaqAnswer(answer=r.reply, abstained=r.route != "knowledge" or r.abstained, reason=r.route_reason,
                             citations=r.citations, sources=r.sources)

        base = run_eval(lambda t: answer_question(faq, t["message"], tenant_id=t["tenant_id"], student_id=t["student_id"]),
                        knowledge, markers=markers).metrics
        full = run_eval(corrective, knowledge, markers=markers).metrics
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup ingest", file=sys.stderr)
        return 2
    finally:
        try:
            cleanup(tickets[0]["tenant_id"], ticket_ids)
        except OperationalError:
            pass

    print(f"evaluated as of {today}")
    print(f"Support graph  |  chat model: {s.llm_provider}  embeddings: {s.embedding_provider}  min_confidence={s.triage_min_confidence}")
    if "fake" in (s.embedding_provider, s.llm_provider):
        print("NOTE: offline fakes in use. These numbers test plumbing and rule-fit, not real language understanding.")
    print(f"  route accuracy        : {pct(m['route_accuracy'])} of {m['tickets']}")
    print(f"  escalation precision  : {pct(m['escalation_precision'])}   recall: {pct(m['escalation_recall'])}")
    print(f"  action tickets        : {m['action_tickets']}   exact tool match: {pct(m['tool_set_match'])}   facts in reply: {pct(m['action_facts_rate'])}")
    print(f"  unexpected tool calls : {m['unexpected_tool_calls'] or 'none'}")
    print(f"  UNAUTHORIZED tool calls: {m['unauthorized_tool_calls'] or 'none'}   <- must be none")
    print(f"  write tickets         : {m['write_tickets']}   routed to write: {pct(m['write_route_rate'])}   paused for the right approver: {pct(m['pause_correct_rate'])}")
    print(f"  escalations recorded  : {pct(m['escalations_recorded'])} of runs needing a human got a ticket + brief")
    print(f"  QA-blocked replies    : {m['qa_blocked'] or 'none'}")
    print(f"  WRITES WITHOUT DECISION: {m['writes_without_decision'] or 'none'}   <- must be none")
    print(f"  LEAKS                 : {m['leaks'] or 'none'}   <- must be none")
    print("  knowledge questions, step 2 baseline -> corrective RAG:")
    for key, label in (("answered_rate", "answered"), ("correct_rate", "all facts correct"), ("abstain_rate", "correctly abstained")):
        print(f"    {label:20}: {pct(base[key])} -> {pct(full[key])}")
    print("  misrouted:")
    for w in m["wrong_route"]:
        print(f"    {w['id']}: expected {w['expected']}, got {w['got']} ({w['reason']})")
    if a.out:
        a.out.write_text(json.dumps({"routing": m, "baseline": base, "corrective": full}, indent=2), encoding="utf-8")
        print(f"full report: {a.out}")
    return 1 if (m["leaks"] or m["unauthorized_tool_calls"] or m["writes_without_decision"]) else 0


if __name__ == "__main__":
    sys.exit(main())
