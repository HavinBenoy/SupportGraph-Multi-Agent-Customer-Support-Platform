"""Staff console (command line) for escalations and approvals. Tenant-scoped; there is NO login yet, so `--staff` is trusted
(see README "known limits"): run this only on a machine that staff alone can use.

    python scripts/review_queue.py list                          # open escalations (urgent first) and pending approvals
    python scripts/review_queue.py show ESC-1A2B3C4D             # the full brief
    python scripts/review_queue.py claim ESC-1A2B3C4D --staff s_1
    python scripts/review_queue.py resolve ESC-1A2B3C4D --staff s_1 --note "called the student"
    python scripts/review_queue.py approve TICKET --staff s_1     # resumes the paused run: staff-approval actions (make-up class)
    python scripts/review_queue.py reject TICKET --staff s_1

Approve/reject resume a run that another process paused, so they need SG_CHECKPOINTER=postgres (an in-memory checkpoint
dies with the process that created it).
"""

import argparse
import json
import sys

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import write_repo
from app.db.session import session_scope
from app.escalation import queue
from app.graph.checkpointer import get_checkpointer
from app.graph.review import ReviewError, resume_ticket
from app.graph.support import build_support_graph
from app.tenants.scope import Reviewer


def main() -> int:
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["list", "show", "claim", "resolve", "approve", "reject"])
    p.add_argument("target", nargs="?", help="escalation id (show/claim/resolve) or ticket id (approve/reject)")
    p.add_argument("--tenant", default=s.default_tenant)
    p.add_argument("--staff", help="your staff id (required to claim, resolve, approve, reject)")
    p.add_argument("--team")
    p.add_argument("--note", default="")
    a = p.parse_args()
    if a.command != "list" and not a.target:
        p.error(f"{a.command} needs a target id")
    if a.command in ("claim", "resolve", "approve", "reject") and not a.staff:
        p.error("--staff is required")
    try:
        if a.command == "list":
            with session_scope() as db:
                esc = queue.list_escalations(db, a.tenant, team=a.team)
                pend = queue.list_pending_makeups(db, a.tenant)
                print(f"{len(esc)} open escalation(s):")
                for e in esc:
                    print(f"  {e.id}  [{e.priority}] team={e.team} reason={e.reason} ticket={e.ticket_id}")
                print(f"{len(pend)} make-up request(s) waiting for approval:")
                for m in pend:
                    print(f"  {m.id}  student={m.student_id} batch={m.batch_id} date={m.session_date} ticket={m.ticket_id}")
        elif a.command == "show":
            with session_scope() as db:
                from app.db.models import Escalation

                row = db.get(Escalation, (a.tenant, a.target))
                if row is None:
                    print("not found", file=sys.stderr)
                    return 1
                print(json.dumps({"status": row.status, "claimed_by": row.claimed_by, **row.brief}, indent=2, ensure_ascii=False))
        elif a.command in ("claim", "resolve"):
            with session_scope() as db:
                row = (queue.claim(db, a.tenant, a.target, a.staff) if a.command == "claim"
                       else queue.resolve(db, a.tenant, a.target, a.staff, a.note))
                print(f"{row.id}: {row.status}")
        else:
            if s.checkpointer != "postgres":
                print("WARNING: SG_CHECKPOINTER is not 'postgres'. A run paused by another process cannot be found in memory.", file=sys.stderr)
            saver = get_checkpointer(s)
            out = resume_ticket(build_support_graph(checkpointer=saver), Reviewer(a.tenant, a.staff, "staff"), a.target, approve=a.command == "approve")
            print(f"{a.command}d: {out.get('outcome')}\nstudent will see: {out['reply']}")
    except (write_repo.WriteError, ReviewError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
