"""End-to-end check against a REAL running Langfuse (v4): run the demo, then ask Langfuse whether the
spans arrived.

    make langfuse-up && make langfuse-wait && make verify-tracing

Exit code 0 = the session's trace contains the expected spans; 1 = not found / wrong content.
Needs the keys from `make langfuse-init`. Prints no secrets.

Why this uses the observations API and not /api/public/traces: Langfuse v4 stores data as observations
("events only"). The legacy traces endpoints are not served there (HTTP 404). The observations v2 endpoint,
exposed by the SDK as `client.api.observations.get_many()`, is the real-time read path, so that is what we use.
"""

import argparse
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.messages import HumanMessage

from app.config import get_settings
from app.graph.builder import build_graph
from app.observability.tracing import instrument_config, shutdown_tracing, tracing_enabled

EXPECTED_SPANS = {"agent", "tools", "get_my_attendance"}
# What each workflow must leave in Langfuse. The FAQ bot (build step 2) is retrieve -> generate -> validate.
EXPECTED_BY_WORKFLOW = {"agent": EXPECTED_SPANS, "faq": {"retrieve", "generate", "validate"},
                       "support": {"triage", "route", "knowledge", "qa", "finalize"}}
FIELDS = "core,basic"  # id, traceId, parentObservationId, name, sessionId, ... (no input/output payloads needed)
PAGE_SIZE = 100
MAX_PAGES = 20  # hard stop so a misbehaving server cannot make us loop forever


class VerifyError(Exception):
    """A failure that retrying will not fix (bad keys, endpoint missing). Message is safe to print."""


def _status_of(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def fetch_observations(client: Any, **filters: Any) -> list[Any]:
    """All observations matching `filters`, following the cursor. Raises VerifyError on 401/403/404."""
    out: list[Any] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        try:
            page = client.api.observations.get_many(fields=FIELDS, limit=PAGE_SIZE, cursor=cursor, **filters)
        except Exception as e:  # noqa: BLE001 - classified below; anything else is treated as transient
            status = _status_of(e)
            if status in (401, 403):
                raise VerifyError(f"Langfuse rejected the API keys (HTTP {status}). Check LANGFUSE_PUBLIC_KEY / "
                                  "LANGFUSE_SECRET_KEY in .env match the project in the Langfuse UI.") from e
            if status == 404:
                raise VerifyError("Langfuse returned 404 for the observations endpoint. Check LANGFUSE_BASE_URL "
                                  "points at Langfuse v4 (default http://localhost:3100).") from e
            raise
        out.extend(page.data)
        cursor = page.meta.cursor
        if not cursor:
            break
    return out


def verify_session(client: Any, session_id: str, *, expected: set[str] = EXPECTED_SPANS, timeout: float = 90.0,
                   interval: float = 3.0, sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print) -> dict:
    """Poll until the session's trace has every expected span, or the timeout passes.

    Returns {"ok": bool, "trace_id": str | None, "names": set[str], "missing": set[str]}.
    Ingestion is asynchronous (queue -> worker -> ClickHouse), so a trace can appear before all of its spans do;
    that is why we keep polling until the names are complete, not just until the first hit.
    """
    deadline = clock() + timeout
    trace_id: str | None = None
    names: set[str] = set()
    while True:
        try:
            by_session = fetch_observations(client, session_id=session_id)
            trace_id = next((o.trace_id for o in by_session if o.trace_id), trace_id)
            if trace_id:
                # Child spans may not carry the session id themselves, so read the whole trace by id.
                in_trace = fetch_observations(client, trace_id=trace_id)
                names = {o.name for o in [*by_session, *in_trace] if o.name}
                if expected <= names:
                    return {"ok": True, "trace_id": trace_id, "names": names, "missing": set()}
        except VerifyError:
            raise
        except Exception as e:  # noqa: BLE001 - server still starting, network blip, etc.
            log(f"  waiting for Langfuse ({type(e).__name__})...")
        if clock() >= deadline:
            return {"ok": False, "trace_id": trace_id, "names": names, "missing": expected - names}
        sleep(interval)


def diagnose(client: Any, since: datetime, log: Callable[[str], None] = print) -> None:
    """Timeout help: show which root observations Langfuse DID receive since the run started."""
    try:
        roots = fetch_observations(client, is_root_observation=True, from_start_time=since)
    except Exception as e:  # noqa: BLE001
        log(f"  (could not list recent observations: {type(e).__name__})")
        return
    if not roots:
        log("  Langfuse has received no root observations since this run started. The export probably never "
            "arrived: check `make langfuse-logs` and that LANGFUSE_BASE_URL matches the UI port.")
        return
    log("  Langfuse received these root observations since this run started (name / session):")
    for o in roots[:10]:
        log(f"    {o.name!r} / {o.session_id!r}")
    log("  If the names are right but the session differs, the session id is not being propagated onto spans.")


def _run_workflow(workflow: str, tenant: str, ticket: str) -> str:
    """Run one traced workflow and return a one-line summary of what it answered."""
    if workflow == "faq":
        from app.rag.faq import answer_question, build_faq_graph

        r = answer_question(build_faq_graph(), "What payment methods do you accept?", tenant_id=tenant, ticket_id=ticket)
        note = "" if not r.abstained else f"  [abstained: {r.reason}; if no_documents_ingested run `make ingest`]"
        return f"{r.answer}{note}"
    if workflow == "support":
        from app.graph.support import ask_support, build_support_graph

        out = ask_support(build_support_graph(), "What payment methods do you accept?", tenant_id=tenant, ticket_id=ticket)
        return f"[{out['route']}] {out['reply']}"
    config = instrument_config(
        {"configurable": {"tenant_id": tenant, "student_id": "stu_asha"}, "recursion_limit": 10},
        tenant_id=tenant, student_id="stu_asha", ticket_id=ticket,
    )
    result = build_graph().invoke({"messages": [HumanMessage("What is my attendance in PYF-101?")]}, config)
    return result["messages"][-1].content


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", choices=sorted(EXPECTED_BY_WORKFLOW), default="agent")
    workflow = ap.parse_args(argv).workflow
    expected = EXPECTED_BY_WORKFLOW[workflow]

    s = get_settings()
    if not tracing_enabled(s):
        print("Tracing is not enabled. Run `make langfuse-init` (sets SG_TRACING_ENABLED and the keys).")
        return 1
    base = s.langfuse_base_url.rstrip("/")

    ticket = f"verify-{uuid.uuid4().hex[:8]}"
    session = f"{s.default_tenant}:{ticket}"
    started = datetime.now(timezone.utc) - timedelta(seconds=5)  # small allowance for clock skew
    print("Workflow answered:", _run_workflow(workflow, s.default_tenant, ticket))
    shutdown_tracing()

    # A separate client used only for READING. tracing_enabled=False so it never exports spans itself.
    from langfuse import Langfuse

    reader = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key.get_secret_value(),  # type: ignore[union-attr]
                      base_url=base, tracing_enabled=False)
    try:
        r = verify_session(reader, session, expected=expected)
    except VerifyError as e:
        print(f"FAIL: {e}")
        return 1

    if r["trace_id"]:
        print(f"Trace {r['trace_id']} found with spans: {sorted(r['names'])}")
        print(f"Open it:  {base}/project/supportgraph-dev/traces/{r['trace_id']}")
    if not r["ok"]:
        if r["trace_id"]:
            print(f"FAIL: expected spans missing: {sorted(r['missing'])}")
        else:
            print(f"FAIL: no observations for session {session} within 90s. See README > Troubleshooting.")
        diagnose(reader, started)
        return 1
    print(f"OK: trace contains the expected spans: {', '.join(sorted(expected))}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
