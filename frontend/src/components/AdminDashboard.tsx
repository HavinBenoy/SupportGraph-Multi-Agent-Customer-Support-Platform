"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Analytics, KnowledgeAnalytics } from "@/lib/types";
import { Bars, DailyChart, Funnel, Stat } from "./Bars";

const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const num = (v: number | null | undefined, unit = "") => (v == null ? "n/a" : `${v}${unit}`);
const hours = (v: number | null | undefined) => (v == null ? "n/a" : v < 1 ? `${Math.round(v * 60)} min` : v < 48 ? `${v.toFixed(1)} h` : `${(v / 24).toFixed(1)} days`);
const STATUS_WORDS: Record<string, string> = { draft: "Draft", analysing: "Analysing", awaiting_clarification: "Needs answer", awaiting_decisions: "Awaiting staff", submitted: "Awaiting review",
  under_review: "Under review", needs_revision: "Returned", approved: "Approved", processing: "Processing", publishing: "Publishing", published: "Published", failed: "Failed",
  rejected: "Rejected", cancelled: "Cancelled", applied: "Drafts created" };

/** Administrator analytics: support operations and knowledge governance, for the administrator's own tenant. Only counts, rates and timings — no conversation or
 *  document text. Every figure says how it is computed (hover, or "How these are calculated"); a figure without data shows "n/a". */
export function AdminDashboard() {
  const { withToken } = useAuth();
  const [days, setDays] = useState(14);
  const [data, setData] = useState<Analytics | null>(null);
  const [kb, setKb] = useState<KnowledgeAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Something went wrong.");
    withToken((t) => api.analytics(t, days)).then(setData).catch(fail);                           // the two sections load independently: one failing never hides the other
    withToken((t) => api.knowledgeAnalytics(t, days)).then(setKb).catch(fail);
  }, [withToken, days]);
  useEffect(() => { load(); }, [load]);
  const s = data?.support;
  const d = s?.definitions ?? {};
  const k = kb?.knowledge;
  const kd = kb?.definitions ?? {};

  return (
    <div className="console analytics">
      <div className="bar">
        <h1>Analytics</h1>
        <label>Period
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 90].map((n) => <option key={n} value={n}>Last {n} days</option>)}
          </select>
        </label>
        <button className="btn small" onClick={load}>Refresh</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {!data && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {data && (
        <section aria-label="Support operations">
          <h2 className="an-h">Support operations</h2>
          <div className="stats">
            <Stat label="Conversations" value={s?.conversations ?? "n/a"} def={d.conversations} hint={`${data.chat.turns} turns`} />
            <Stat label="Resolved by the AI" value={pct(s?.ai_resolution_rate)} def={d.ai_resolution_rate} hint={s ? `${s.ai_resolved} of ${s.conversations}` : undefined} />
            <Stat label="Escalated to staff" value={s?.escalated ?? "n/a"} def={d.escalated} hint={`${data.escalations.sla_breaches_open} open past SLA`} />
            <Stat label="Voice takeover rate" value={pct(s?.takeover_rate)} def={d.takeover_rate} hint={s ? `${s.calls_taken_over} of ${s.calls} calls · ${s.calls_failed} failed` : undefined} />
            <Stat label="Escalations resolved" value={pct(s?.escalation_resolution_rate)} def={d.escalation_resolution_rate} hint={`median ${hours(data.escalations.median_hours_to_resolve)} to resolve`} />
            <Stat label="Median reply time" value={num(data.chat.latency_ms.p50, " ms")} def={d.median_reply_ms} hint={`p95 ${num(data.chat.latency_ms.p95, " ms")}`} />
            <Stat label="Unresolved now" value={s?.unresolved ?? data.escalations.open + data.escalations.claimed} def={d.unresolved} hint={`${data.approvals.pending} make-up requests pending`} />
          </div>
          <div className="grid2">
            <DailyChart series={data.chat.per_day} />
            <Bars title="Where turns were routed" data={data.chat.by_route} />
            <Bars title="Escalations by reason" data={data.escalations.by_reason} />
            <Bars title="Tickets by status" data={data.tickets_by_status} />
          </div>
        </section>)}

      {k && (
        <section aria-label="Knowledge governance">
          <h2 className="an-h">Knowledge governance</h2>
          <div className="stats">
            <Stat label="Change requests" value={k.requests} def={kd.requests} hint={`${k.by_kind.ai_assisted ?? 0} AI-assisted · ${k.by_kind.manual ?? 0} manual · ${k.by_kind.deletion ?? 0} deletions`} />
            <Stat label="AI-proposed changes" value={k.ai_proposals} def={kd.ai_proposals} hint={`${k.documents_examined} documents examined · ${k.documents_affected} affected`} />
            <Stat label="Staff acceptance" value={pct(k.staff.acceptance_rate)} def={kd.staff_acceptance_rate} hint={`${k.staff.accepted} accepted · ${k.staff.edited} edited · ${k.staff.discarded} discarded`} />
            <Stat label="Manual-edit rate" value={pct(k.staff.manual_edit_rate)} def={kd.manual_edit_rate} hint="of accepted AI proposals, edited first" />
            <Stat label="Admin approval" value={pct(k.admin.approval_rate)} def={kd.admin_approval_rate} hint={`${k.admin.approved} approved · ${k.admin.rejected} rejected · review ${hours(k.admin.median_hours_to_review)}`} />
            <Stat label="Published" value={k.published} def={kd.published} hint={`median ${hours(k.median_hours_to_publication)} from request`} />
            <Stat label="Failed / still open" value={`${k.failed} / ${k.incomplete}`} def={`${kd.failed} ${kd.incomplete}`} />
          </div>
          <div className="grid2">
            <Funnel title="From request to publication" steps={k.funnel} />
            <DailyChart series={k.per_day} title="Change requests per day" />
            <Bars title="Requests by status" data={Object.fromEntries(Object.entries(k.by_status).map(([st, n]) => [STATUS_WORDS[st] ?? st, n]))} />
            <Bars title="Published changes: AI as written vs. changed by a person" keepOrder hint={kd.published_human_modified}
              data={{ "AI, unmodified": k.published_items.ai_unmodified, "Edited or written by staff/admin": k.published_items.human_modified }} />
          </div>
          <section className="card" aria-label="Staff contributions">
            <h3>Staff contributions</h3>
            {k.contributions.length === 0 ? <p className="muted">No change requests in this period.</p> : (
              <div className="kw-tablewrap"><table className="kw-table">
                <caption className="sr-only">Change requests per staff member in the period</caption>
                <thead><tr><th scope="col">Staff</th><th scope="col">Requests</th><th scope="col">AI accepted</th><th scope="col">AI edited</th><th scope="col">Submitted</th>
                  <th scope="col">Approved</th><th scope="col">Approval rate</th><th scope="col">Published</th><th scope="col">Median review time</th></tr></thead>
                <tbody>{k.contributions.map((c) => (
                  <tr key={c.staff}><th scope="row">{c.staff}</th><td>{c.requests}</td><td>{c.accepted}</td><td>{c.edited}</td><td>{c.submitted}</td><td>{c.approved}</td>
                    <td>{pct(c.approval_rate)}</td><td>{c.published}</td><td>{hours(c.median_hours_to_review)}</td></tr>))}</tbody>
              </table></div>)}
          </section>
        </section>)}

      {(data || kb) && (
        <details className="card an-defs"><summary>How these are calculated</summary>
          <dl>
            {Object.entries({ ...d, ...kd }).map(([key, v]) => <div key={key} className="an-def"><dt>{key.replace(/_/g, " ")}</dt><dd>{v}</dd></div>)}
          </dl>
          <ul className="notes">{data?.notes.map((n) => <li key={n} className="muted tiny">{n}</li>)}</ul>
        </details>)}
    </div>
  );
}
