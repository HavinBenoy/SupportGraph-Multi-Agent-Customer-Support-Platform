"""Run the minimal agent from the command line and watch each graph step.

    cd backend && python scripts/run_agent.py "Where is my order A1001?"

If tracing is configured (make langfuse-init, SG_TRACING_ENABLED=true) the run also appears in the
Langfuse UI under session "<tenant>:<ticket>"; otherwise it behaves exactly as before.
"""

import argparse
import sys
import uuid

from langchain_core.messages import HumanMessage
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.graph.builder import build_graph
from app.observability.tracing import instrument_config, shutdown_tracing, tracing_enabled


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("message")
    p.add_argument("--tenant", default=get_settings().default_tenant)
    p.add_argument("--customer", default="cust_alice")
    p.add_argument("--ticket", default=f"T-{uuid.uuid4().hex[:8]}")
    args = p.parse_args()

    graph = build_graph()
    # Trusted scope lives in config, NOT in the prompt or tool arguments.
    config = {"configurable": {"tenant_id": args.tenant, "customer_id": args.customer}, "recursion_limit": 10}
    config = instrument_config(config, tenant_id=args.tenant, customer_id=args.customer, ticket_id=args.ticket)

    for update in graph.stream({"messages": [HumanMessage(args.message)]}, config, stream_mode="updates"):
        for node, delta in update.items():
            msg = delta["messages"][-1]
            if msg.type == "ai" and msg.tool_calls:
                print(f"[{node}] calls tool: {msg.tool_calls[0]['name']}({msg.tool_calls[0]['args']})")
            elif msg.type == "tool":
                print(f"[{node}] result: {msg.content}")
            else:
                print(f"[{node}] says: {msg.content}")

    if tracing_enabled():
        shutdown_tracing()  # flush buffered spans before the process exits
        # We cannot know from here whether the server accepted the spans (export is asynchronous and
        # errors are only logged), so say "attempted". `make verify-tracing` confirms delivery.
        print(f"[trace] export attempted, session id: {args.tenant}:{args.ticket}  (see README > Verify a trace)")


if __name__ == "__main__":
    try:
        main()
    except OperationalError:
        # The tools read the mock store from Postgres. Say what to do instead of dumping a traceback.
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup", file=sys.stderr)
        sys.exit(2)
