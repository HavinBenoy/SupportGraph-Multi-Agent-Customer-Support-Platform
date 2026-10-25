"""Run the minimal agent from the command line and watch each graph step.

    cd backend && python scripts/run_agent.py "Where is my order A1001?"
"""

import argparse

from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_graph


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("message")
    p.add_argument("--tenant", default=get_settings().default_tenant)
    p.add_argument("--customer", default="cust_alice")
    args = p.parse_args()

    graph = build_graph()
    # Trusted scope lives in config, NOT in the prompt or tool arguments.
    config = {"configurable": {"tenant_id": args.tenant, "customer_id": args.customer}, "recursion_limit": 10}

    for update in graph.stream({"messages": [HumanMessage(args.message)]}, config, stream_mode="updates"):
        for node, delta in update.items():
            msg = delta["messages"][-1]
            if msg.type == "ai" and msg.tool_calls:
                print(f"[{node}] calls tool: {msg.tool_calls[0]['name']}({msg.tool_calls[0]['args']})")
            elif msg.type == "tool":
                print(f"[{node}] result: {msg.content}")
            else:
                print(f"[{node}] says: {msg.content}")


if __name__ == "__main__":
    main()
