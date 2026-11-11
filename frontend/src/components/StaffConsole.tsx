"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useUrlParam } from "@/lib/nav";
import type { Approval, EscalationDetail, EscalationRow } from "@/lib/types";
import { LiveCalls } from "./LiveCalls";
import { MyInterventions } from "./MyInterventions";
import { KnowledgeWorkspace } from "./workspace/KnowledgeWorkspace";

type Tab = "escalations" | "approvals" | "calls" | "feedback" | "knowledge";
const STATUSES = ["open", "claimed", "resolved"] as const;
const when = (iso: string) => new Date(iso).toLocaleString();
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

export function StaffConsole() {
  const [t, setT] = useUrlParam("tab", "escalations");
  const tab = (["escalations", "approvals", "calls", "feedback", "knowledge"].includes(t ?? "") ? t : "escalations") as Tab;
  const setTab = (v: Tab) => setT(v);
  return (
    <div className="console">
      <header className="page-head"><div><p className="eyebrow">Staff</p><h1 className="page-title">Staff console</h1>
        <p>Escalations, approvals, live calls and the knowledge base in one place.</p></div></header>
      <div role="tablist" aria-label="Queues" className="tabs">
        <button role="tab" aria-selected={tab === "escalations"} onClick={() => setTab("escalations")}>Escalations</button>
        <button role="tab" aria-selected={tab === "approvals"} onClick={() => setTab("approvals")}>Make-up approvals</button>
        <button role="tab" aria-selected={tab === "calls"} onClick={() => setTab("calls")}>Live calls</button>
        <button role="tab" aria-selected={tab === "feedback"} onClick={() => setTab("feedback")}>My intervention feedback</button>
        <button role="tab" aria-selected={tab === "knowledge"} onClick={() => setTab("knowledge")}>Knowledge</button>
      </div>
      {tab === "escalations" ? <Escalations /> : tab === "approvals" ? <Approvals /> : tab === "calls" ? <LiveCalls /> : tab === "knowledge" ? <KnowledgeWorkspace scope="staff" /> : <MyInterventions />}
    </div>
  );
}

function Escalations() {
  const { withToken, session } = useAuth();
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("open");
  const [rows, setRows] = useState<EscalationRow[] | null>(null);
  const [sel, setSel] = useState<EscalationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    withToken((t) => api.escalations(t, status)).then((r) => { setRows(r); setError(null); }).catch((e) => setError(msg(e)));
  }, [withToken, status]);
  useEffect(() => { setRows(null); load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 20000); return () => clearInterval(t); }, [load]);

  const open = (id: string) => withToken((t) => api.escalation(t, id)).then((d) => { setSel(d); setError(null); }).catch((e) => setError(msg(e)));
  const changed = (d: EscalationDetail) => { setSel(d); load(); };

  return (
    <div className="split">
      <section aria-label="Escalation queue">
        <div className="bar">
          <label>Status
            <select value={status} onChange={(e) => { setStatus(e.target.value as typeof status); setSel(null); }}>
              {STATUSES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <button className="btn small" onClick={load}>Refresh</button>
        </div>
        {error && <p role="alert" className="error">{error}</p>}
        {rows === null && !error && <p className="muted" aria-busy="true">Loading…</p>}
        {rows?.length === 0 && <p className="muted">Nothing {status} right now.</p>}
        <ul className="list">
          {rows?.map((r) => (
            <li key={r.id}>
              <button className={`row ${sel?.id === r.id ? "sel" : ""}`} onClick={() => open(r.id)}>
                <strong>{r.id}</strong> <span className={`pill pr-${r.priority}`}>{r.priority}</span> <span className="pill">{r.team}</span>
                <span className="muted block">{r.reason} · {when(r.created_at)}{r.claimed_by ? ` · ${r.claimed_by}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="Escalation detail">
        {sel ? <Detail d={sel} me={session?.username ?? ""} onChanged={changed} /> : <p className="muted">Select an escalation to see its brief.</p>}
      </section>
    </div>
  );
}

function Detail({ d, me, onChanged }: { d: EscalationDetail; me: string; onChanged: (d: EscalationDetail) => void }) {
  const { withToken } = useAuth();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setNote(""); setError(null); }, [d.id]);

  async function run(fn: (t: string) => Promise<EscalationDetail>) {
    setBusy(true); setError(null);
    try { onChanged(await withToken(fn)); } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  }
  const b = d.brief;
  const mine = d.claimed_by === me;
  return (
    <article className="card">
      <h2>{d.id} <span className="pill">{d.status}</span></h2>
      <p className="muted">Ticket {d.ticket_id} · {d.team} · {d.priority} priority{b.sla_hours ? ` · reply within ${b.sla_hours}h` : ""}</p>
      <dl>
        <dt>Student</dt><dd>{b.student_name ?? "Visitor (not signed in)"}{b.student_id && b.student_id !== "anonymous" ? ` (${b.student_id})` : ""}</dd>
        <dt>Reason</dt><dd>{d.reason}</dd>
        {b.summary && <><dt>Summary</dt><dd>{b.summary}</dd></>}
        {b.excerpt && <><dt>What they wrote (contact details masked)</dt><dd className="quote">{b.excerpt}</dd></>}
        {b.batches && b.batches.length > 0 && <><dt>Batches</dt><dd>{b.batches.map((x) => `${x.batch_id}${x.attendance_pct != null ? ` (${x.attendance_pct}% attendance)` : ""}`).join(", ")}</dd></>}
        {b.suggested_next_step && <><dt>Suggested next step</dt><dd>{b.suggested_next_step}</dd></>}
        {d.claimed_by && <><dt>Claimed by</dt><dd>{d.claimed_by}</dd></>}
        {d.resolution_note && <><dt>Resolution</dt><dd>{d.resolution_note}</dd></>}
      </dl>
      {error && <p role="alert" className="error">{error}</p>}
      {d.status === "open" && <button className="btn primary" disabled={busy} onClick={() => run((t) => api.claim(t, d.id))}>Claim</button>}
      {d.status !== "resolved" && (d.status === "open" || mine) && (
        <form onSubmit={(e) => { e.preventDefault(); void run((t) => api.resolve(t, d.id, note.trim())); }} className="form">
          <label>Resolution note<textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={1000} required /></label>
          <button className="btn" disabled={busy || !note.trim()}>Resolve</button>
        </form>
      )}
      {d.status === "claimed" && !mine && <p className="muted">Claimed by {d.claimed_by}; only they can resolve it.</p>}
    </article>
  );
}

function Approvals() {
  const { withToken } = useAuth();
  const [rows, setRows] = useState<Approval[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null); // kept apart from load errors so a refresh does not erase it

  const load = useCallback(() => {
    withToken((t) => api.approvals(t)).then((r) => { setRows(r); setError(null); }).catch((e) => setError(msg(e)));
  }, [withToken]);
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, [load]);

  async function decide(a: Approval, approve: boolean) {
    setBusyId(a.request_id); setActionError(null); setDone(null);
    try {
      const r = await withToken((t) => api.decide(t, a.request_id, approve));
      setDone(`${approve ? "Approved" : "Rejected"} ${a.batch_id} make-up for ${a.student_name ?? a.student_id}. ${r.message}`);
    } catch (e) { setActionError(msg(e)); } finally { setBusyId(null); load(); }
  }

  return (
    <section aria-label="Make-up approvals">
      <div className="bar"><p className="muted">Requests the assistant prepared and students confirmed. Nothing is booked until you approve.</p><button className="btn small" onClick={load}>Refresh</button></div>
      {(actionError ?? error) && <p role="alert" className="error">{actionError ?? error}</p>}
      {done && <p role="status" className="banner done">{done}</p>}
      {rows === null && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {rows?.length === 0 && <p className="muted">No requests are waiting.</p>}
      <ul className="list">
        {rows?.map((a) => (
          <li key={a.request_id} className="card">
            <h3>{a.student_name ?? a.student_id} <span className="pill">{a.batch_id}</span></h3>
            <p>Missed session on <strong>{a.session_date}</strong>. Reason: {a.reason || "not given"}</p>
            <p className="muted">Requested {when(a.created_at)}</p>
            <div className="actions">
              <button className="btn primary" disabled={busyId === a.request_id} onClick={() => decide(a, true)}>Approve</button>
              <button className="btn" disabled={busyId === a.request_id} onClick={() => decide(a, false)}>Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
