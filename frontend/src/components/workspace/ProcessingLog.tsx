"use client";
/* How the AI handled this request, stage by stage (the per-request processing trace). Shown to the staff member / admin who can see the request; it holds no document text
   unless the server runs with SG_KB_AI_TRACE_CONTENT=true (a debugging switch). */
import type { KwTrace, KwTraceEvent } from "@/lib/types";

const STAGE: Record<string, string> = {
  request: "Request", understanding: "Understanding", discovery: "Discovery", passages: "Passages", context: "Context", model_call: "Model call", validation: "Validation",
  semantic_check: "Second check", retry: "Retry", classification: "Result", analysis: "Analysis",
};
const BAD = new Set(["not_discovered", "no_passage", "not_sent", "model_not_invoked", "model_failed", "truncated", "malformed", "rejected", "vague", "error"]);

function detail(e: KwTraceEvent): string {
  const parts: string[] = [];
  if (e.section) parts.push(`section “${e.section}”`);
  if (typeof e.seconds === "number") parts.push(`${e.seconds}s`);
  if (e.config && typeof e.config === "object") parts.push(Object.entries(e.config as Record<string, unknown>).map(([k, v]) => `${k}=${String(v)}`).join(" "));
  if (Array.isArray(e.accepted) && e.accepted.length) parts.push(`accepted: ${(e.accepted as { kind: string }[]).map((h) => h.kind).join(", ")}`);
  if (Array.isArray(e.rejected) && e.rejected.length) parts.push(`dropped: ${(e.rejected as string[]).join("; ")}`);
  if (Array.isArray(e.review) && e.review.length) parts.push(`for review: ${(e.review as string[]).join("; ")}`);
  if (typeof e.count === "number") parts.push(`${e.count} passage(s)`);
  if (typeof e.budget === "string") parts.push(e.budget);
  if (typeof e.why === "string") parts.push(e.why);
  if (typeof e.reason === "string") parts.push(e.reason);
  return parts.join(" · ");
}

export function ProcessingLog({ trace }: { trace: KwTrace | null | undefined }) {
  if (!trace?.events?.length) return null;
  return (
    <details className="kw-trace">
      <summary>Processing log ({trace.events.length} step{trace.events.length === 1 ? "" : "s"}) — how the AI handled this request</summary>
      <ol aria-label="Processing log">
        {trace.events.map((e) => (
          <li key={e.n} className={BAD.has(e.outcome) ? "kw-trace-bad" : undefined}>
            <b>{STAGE[e.stage] ?? e.stage}</b> <span className="kw-chip">{e.outcome.replace(/_/g, " ")}</span>
            {e.doc && <> <span className="kw-where">{e.doc}</span></>} <span className="kw-muted tiny">{detail(e)}</span>
            {e.text && <details><summary className="tiny">text</summary>{Object.entries(e.text).map(([k, v]) => <div key={k}><b className="tiny">{k}</b><pre className="kw-pre">{v}</pre></div>)}</details>}
          </li>))}
      </ol>
      {trace.dropped > 0 && <p className="kw-muted tiny">{trace.dropped} more step(s) were not stored.</p>}
    </details>);
}
