"""Ask the RAG FAQ baseline a question from the command line.

    cd backend && python scripts/ask_faq.py "What is the fee for the Data Science with Python course?"
    cd backend && python scripts/ask_faq.py --sources "Is the demo class free?"

Needs ingested documents (make ingest). If tracing is configured (make langfuse-init, SG_TRACING_ENABLED=true) the run
appears in Langfuse as spans retrieve -> generate -> validate (or retrieve -> abstain) under session "<tenant>:<ticket>".
"""

import argparse
import sys
import uuid

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.observability.tracing import shutdown_tracing, tracing_enabled
from app.rag.faq import answer_question, build_faq_graph


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("question")
    p.add_argument("--tenant", default=get_settings().default_tenant)
    p.add_argument("--ticket", default=f"faq-{uuid.uuid4().hex[:8]}")
    p.add_argument("--sources", action="store_true", help="also print the retrieved passages and their scores")
    a = p.parse_args()

    try:
        result = answer_question(build_faq_graph(), a.question, tenant_id=a.tenant, ticket_id=a.ticket)
    except OperationalError:
        print("Could not reach the database. Start and prepare it with:  make db-up db-setup", file=sys.stderr)
        return 2

    print(f"Q: {a.question}\nA: {result.answer}")
    for s in result.sources:
        print(f"   [{s['source_id']}] {s['heading']}  (score {s['score']})")
    if result.abstained:
        print(f"   (abstained: {result.reason}; best similarity {result.top_score})")
        if result.reason == "no_documents_ingested":
            print("   No documents are indexed for this tenant and embedding model. Run:  make ingest")
    if tracing_enabled():
        shutdown_tracing()  # flush buffered spans before the process exits
        print(f"[trace] export attempted, session id: {a.tenant}:{a.ticket}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
