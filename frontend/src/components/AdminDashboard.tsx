"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Analytics } from "@/lib/types";
import { Bars, DailyChart, Stat } from "./Bars";

const pct = (v: number | null) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const num = (v: number | null, unit = "") => (v == null ? "n/a" : `${v}${unit}`);

export function AdminDashboard() {
  const { withToken } = useAuth();
  const [days, setDays] = useState(14);
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    withToken((t) => api.analytics(t, days)).then(setData).catch((e) => setError(e instanceof ApiError ? e.message : "Something went wrong."));
  }, [withToken, days]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="console">
      <div className="bar">
        <h1>Analytics</h1>
        <label>Period
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
        </label>
        <button className="btn small" onClick={load}>Refresh</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {!data && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {data && (
        <>
          <div className="stats">
            <Stat label="Chat turns" value={data.chat.turns} />
            <Stat label="Self-service rate" value={pct(data.chat.self_service_rate)} hint="turns that needed no human" />
            <Stat label="Abstain rate" value={pct(data.chat.abstain_rate)} hint="knowledge answers declined for lack of evidence" />
            <Stat label="Median reply time" value={num(data.chat.latency_ms.p50, " ms")} hint={`p95 ${num(data.chat.latency_ms.p95, " ms")}`} />
            <Stat label="Open escalations" value={data.escalations.open + data.escalations.claimed} hint={`${data.escalations.sla_breaches_open} past SLA`} />
            <Stat label="Make-up requests pending" value={data.approvals.pending} hint={`median decision ${num(data.approvals.median_hours_to_decide, " h")}`} />
          </div>
          <div className="grid2">
            <DailyChart series={data.chat.per_day} />
            <Bars title="Where turns were routed" data={data.chat.by_route} />
            <Bars title="Escalations by team" data={data.escalations.by_team} />
            <Bars title="Escalations by reason" data={data.escalations.by_reason} />
            <Bars title="Make-up requests" data={{ pending: data.approvals.pending, approved: data.approvals.approved, rejected: data.approvals.rejected }} />
            <Bars title="Tickets by status" data={data.tickets_by_status} />
          </div>
          <ul className="notes">{data.notes.map((n) => <li key={n} className="muted tiny">{n}</li>)}</ul>
        </>
      )}
    </div>
  );
}
