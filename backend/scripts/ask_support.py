"""Send one message through the whole support graph (triage -> route -> branch) and show what happened.

    cd backend && python scripts/ask_support.py "What courses are available?"
    python scripts/ask_support.py --student stu_asha "What is my attendance in PYF-101?"
    python scripts/ask_support.py --student stu_asha "I want to complain about my trainer"
    python scripts/ask_support.py --student stu_meera --ticket demo1 "Please arrange a make-up class for the session I missed in PYD-301"
    python scripts/ask_support.py --student stu_meera --ticket demo1 "any news?"      # same ticket = same conversation

Without --student you are an anonymous prospect: general questions work, personal records do not.
Needs: make db-up db-setup ingest.
"""

import argparse
import sys
import uuid

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.graph.checkpointer import get_checkpointer
from app.graph.review import converse
from app.graph.support import build_support_graph
from app.observability.tracing import shutdown_tracing, tracing_enabled
from app.tools.scope import ANONYMOUS


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("message")
    p.add_argument("--tenant", default=get_settings().default_tenant)
    p.add_argument("--student", default=ANONYMOUS, help="student id of a signed-in student (default: anonymous prospect)")
    p.add_argument("--ticket", default=f"sup-{uuid.uuid4().hex[:8]}")
    a = p.parse_args()
    try:
        settings = get_settings()
        out = converse(build_support_graph(checkpointer=get_checkpointer(settings)), a.message, tenant_id=a.tenant,
                       student_id=a.student, ticket_id=a.ticket)
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup ingest", file=sys.stderr)
        return 2
    print(f"Q: {a.message}   (as {a.student}, ticket {a.ticket})")
    if "triage" in out:
        t = out["triage"]
        print(f"   triage : {t['category']}, confidence {t['confidence']}, priority {t['priority']}, own-records {t['wants_own_records']}")
        print(f"   route  : {out['route']}  ({out['route_reason']})")
    if out.get("awaiting"):
        print(f"   PAUSED : waiting for {out['awaiting']} (decide with: make review ARGS=\"approve {a.ticket} --staff s_1\")" if out["awaiting"] == "staff"
              else "   PAUSED : waiting for your yes/no (send the same --ticket again)")
    if out.get("escalation_id"):
        print(f"   escalation: {out['escalation_id']} -> team brief stored")
    if out.get("qa_blocked"):
        print(f"   QA     : reply replaced by the safe fallback ({[i['code'] for i in out['qa_issues']]})")
    if out.get("queries"):
        print(f"   searches: {out['queries']}")
    if out.get("tools_called"):
        print(f"   tools  : {out['tools_called']}")
    if out.get("needs_human"):
        print(f"   needs a human: {out.get('escalation_reason')}")
    print(f"A: {out['reply']}")
    if tracing_enabled():
        shutdown_tracing()
        print(f"[trace] export attempted, session id: {a.tenant}:{a.ticket}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
