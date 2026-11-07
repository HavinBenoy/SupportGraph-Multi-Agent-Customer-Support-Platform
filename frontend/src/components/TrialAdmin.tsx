"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Feedback, FeedbackQueue, PurgeResult, RecordingRow, RetentionStatus, TrialEventRow, TrialMetrics, TrialView } from "@/lib/types";
import { Bars, Stat } from "./Bars";
import { REVIEW_LABEL } from "./FeedbackForm";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const pct = (v: number | null) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const secs = (v: number | null) => (v == null ? "n/a" : v >= 90 ? `${Math.round(v / 60)} min` : `${Math.round(v)} s`);
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const day = (d: Date) => d.toISOString().slice(0, 10);

/** Trial Verification Mode (M5), administrators only. The server enforces every rule shown here (role, versioned edits, provider checks); this screen only
 *  shows state and sends requests. Nothing on this page can change the assistant's knowledge. */
export function TrialAdmin() {
  return (
    <div className="console">
      <h1>Trial verification</h1>
      <p className="muted">Monitoring and recording are separate: staff may listen to a trial call, but audio is only recorded if the caller explicitly agrees. Whether and how your organisation may record calls is for your organisation to decide; nothing here is legal advice.</p>
      <Settings />
      <Metrics />
      <FeedbackReview />
      <Retention />
    </div>
  );
}

function Settings() {
  const { withToken } = useAuth();
  const [view, setView] = useState<TrialView | null>(null);
  const [events, setEvents] = useState<TrialEventRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ disclosure_text: "", recording_notice: "", retention_recording_days: 30, retention_consent_days: 365, retention_feedback_days: 365, withdrawal_policy: "delete" as "delete" | "keep_until_expiry", recording_offered: false });

  const load = useCallback(() => {
    setError(null); setConflict(false);
    withToken((t) => Promise.all([api.trial(t), api.trialEvents(t)])).then(([v, ev]) => {
      setView(v); setEvents(ev);
      const s = v.settings;
      setF({ disclosure_text: s.disclosure_text, recording_notice: s.recording_notice, retention_recording_days: s.retention_recording_days, retention_consent_days: s.retention_consent_days,
             retention_feedback_days: s.retention_feedback_days, withdrawal_policy: s.withdrawal_policy, recording_offered: s.recording_offered });
    }).catch((e) => setError(msg(e)));
  }, [withToken]);
  useEffect(() => { load(); }, [load]);

  async function save(patch: Parameters<typeof api.saveTrial>[2], done: string) {
    if (!view || busy) return;
    setBusy(true); setError(null); setNotice(null); setConflict(false);
    try {
      const v = await withToken((t) => api.saveTrial(t, view.settings.version, patch));
      setView(v); setNotice(done);
      setEvents(await withToken((t) => api.trialEvents(t)));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && /changed/i.test(e.message)) setConflict(true);
      setError(msg(e));
    } finally { setBusy(false); }
  }
  const s = view?.settings;
  const submit = (e: FormEvent) => { e.preventDefault(); void save({ ...f }, "Settings saved."); };
  return (
    <section className="card" aria-label="Trial mode and settings">
      <h2>Trial mode</h2>
      {error && <p role="alert" className="error">{error}</p>}
      {conflict && <p className="banner">Somebody else changed these settings while you were editing. <button className="btn small" onClick={load}>Reload the latest settings</button></p>}
      {notice && <p role="status" className="banner done">{notice}</p>}
      {!view && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {view && s && (
        <>
          <p>Trial verification mode is <strong data-testid="trial-state">{s.enabled ? "ON" : "OFF"}</strong>{" "}
            <span className="muted tiny">version {s.version}{s.updated_by ? ` · last changed by ${s.updated_by} ${when(s.updated_at)}` : " · never changed (defaults)"}</span></p>
          <div className="actions">
            <button className={`btn ${s.enabled ? "danger" : "primary"}`} disabled={busy} aria-pressed={s.enabled} onClick={() => void save({ enabled: !s.enabled }, s.enabled ? "Trial mode is off. New calls are normal calls." : "Trial mode is on for new calls.")}>
              {s.enabled ? "Turn trial mode off" : "Turn trial mode on"}
            </button>
          </div>
          <p className="muted tiny">Only administrators can change this. Calls already in progress keep the mode they started with, except that switching it off stops any recording straight away. Humans can never hand a call back to the assistant in either mode.</p>
          <form className="form" onSubmit={submit} aria-label="Disclosure, consent and retention settings">
            <label>What callers are told at the start of a trial call
              <textarea rows={3} value={f.disclosure_text} maxLength={600} onChange={(e) => setF({ ...f, disclosure_text: e.target.value })} />
            </label>
            <label className="check"><input type="checkbox" checked={f.recording_offered} disabled={!view.recording.operational} onChange={(e) => setF({ ...f, recording_offered: e.target.checked })} />
              Offer callers the choice to record the call (they must say yes themselves)</label>
            <p className="muted tiny" data-testid="provider-note">Recording provider: <strong>{view.recording.provider}</strong>{view.recording.operational ? " (ready)" : " (not available)"}. {view.recording.note}</p>
            <label>Recording notice shown with the consent choice (use {"{days}"} for the retention period)
              <textarea rows={4} value={f.recording_notice} maxLength={1200} onChange={(e) => setF({ ...f, recording_notice: e.target.value })} />
            </label>
            <div className="grid2">
              <label>Keep recordings (days)<input type="number" min={1} max={365} value={f.retention_recording_days} onChange={(e) => setF({ ...f, retention_recording_days: Number(e.target.value) })} /></label>
              <label>Keep consent records (days)<input type="number" min={1} max={3650} value={f.retention_consent_days} onChange={(e) => setF({ ...f, retention_consent_days: Number(e.target.value) })} /></label>
              <label>Keep reviewed feedback (days)<input type="number" min={1} max={3650} value={f.retention_feedback_days} onChange={(e) => setF({ ...f, retention_feedback_days: Number(e.target.value) })} /></label>
              <label>If a caller withdraws consent
                <select value={f.withdrawal_policy} onChange={(e) => setF({ ...f, withdrawal_policy: e.target.value as "delete" | "keep_until_expiry" })}>
                  <option value="delete">Stop recording and delete what was recorded</option>
                  <option value="keep_until_expiry">Stop recording and keep it until it expires</option>
                </select>
              </label>
            </div>
            <p className="muted tiny">Changing the recording notice, the recording period or the withdrawal rule starts a new consent version: callers always agree to the exact terms they were shown (now version {s.consent_policy_version}).</p>
            <div className="actions"><button className="btn primary" disabled={busy}>Save settings</button><button type="button" className="btn" onClick={load}>Discard changes</button></div>
          </form>
          <details><summary>Recent changes</summary>
            {events.length === 0 && <p className="muted">No changes yet.</p>}
            <ul className="notes">{events.map((ev) => <li key={ev.id} className="tiny">{when(ev.created_at)} · {ev.actor} · {ev.kind.replace(/_/g, " ")}{ev.detail?.fields ? ` (${String(ev.detail.fields)})` : ""}</li>)}</ul>
          </details>
        </>
      )}
    </section>
  );
}

function Metrics() {
  const { withToken } = useAuth();
  const today = day(new Date());
  const [from, setFrom] = useState(day(new Date(Date.now() - 29 * 86400000)));
  const [to, setTo] = useState(today);
  const [m, setM] = useState<TrialMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { setError(null); withToken((t) => api.trialMetrics(t, from, to)).then(setM).catch((e) => setError(msg(e))); }, [withToken, from, to]);
  useEffect(() => { load(); }, [load]);

  return (
    <section className="card" aria-label="Trial metrics">
      <div className="bar"><h2>Trial metrics</h2>
        <label>From<input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>To<input type="date" value={to} max={today} onChange={(e) => setTo(e.target.value)} /></label>
        <button className="btn small" onClick={load}>Apply</button></div>
      {error && <p role="alert" className="error">{error}</p>}
      {!m && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {m?.empty && <p className="muted" data-testid="metrics-empty">No trial calls in this period. Calls made while trial mode is on appear here.{m.calls_outside_trial ? ` (${m.calls_outside_trial} calls were made outside trial mode and are not counted.)` : ""}</p>}
      {m && !m.empty && (
        <>
          {m.truncated && <p className="banner">This period has more calls than can be summarised at once; the figures cover the earliest ones. Choose a shorter period.</p>}
          <div className="stats">
            <Stat label="Trial calls" value={m.calls.total} hint={`${m.customers.unique_signed_in} signed-in callers · ${m.customers.anonymous_calls} anonymous calls`} />
            <Stat label="AI handled" value={m.calls.ai_handled} hint="never taken over" />
            <Stat label="Calls with human listeners" value={m.human.calls_with_listeners} hint={`${m.human.listener_sessions} listening sessions`} />
            <Stat label="Takeovers" value={m.human.takeovers} hint="listening is not a takeover" />
            <Stat label="Intervention rate" value={pct(m.human.intervention_rate)} hint="calls taken over ÷ trial calls" />
            <Stat label="Ended normally" value={m.calls.ended_normally} />
            <Stat label="Ended after intervention" value={m.calls.ended_after_intervention} />
            <Stat label="Failed" value={m.calls.failed} />
            <Stat label="Needed escalation" value={m.calls.requiring_escalation} />
            <Stat label="Average call" value={secs(m.durations.avg_call_s)} hint={`${m.durations.calls_measured} measured`} />
            <Stat label="Average intervention" value={secs(m.durations.avg_intervention_s)} hint={`${m.durations.interventions_measured} measured`} />
            <Stat label="Recorded calls" value={m.recording.calls_recorded} hint={`${m.recording.recordings} recordings`} />
          </div>
          <div className="grid2">
            <Bars title="Recording consent" data={{ offered: m.consent.offered, agreed: m.consent.ever_granted, declined: m.consent.declined, withdrawn: m.consent.withdrawn, "no answer": m.consent.undecided }} />
            <Bars title="Why staff intervened" data={m.feedback.reasons} />
            <Bars title="Intervention feedback" data={{ ...m.feedback.by_review_status, "no reason given": m.feedback.no_reason_given }} />
          </div>
          <p className="muted tiny" data-testid="consent-rates">Agreed {pct(m.consent.granted_rate)} · declined {pct(m.consent.declined_rate)} · withdrew {pct(m.consent.withdrawn_rate)} (of those who agreed)</p>
          <ul className="notes">{m.notes.map((n) => <li key={n} className="muted tiny">{n}</li>)}</ul>
        </>
      )}
    </section>
  );
}

const NEXT: Record<string, { to: string; label: string }[]> = {
  pending_review: [{ to: "reviewed", label: "Mark reviewed" }, { to: "approved_for_update", label: "Approve for knowledge update" }, { to: "rejected", label: "Reject" }],
  reviewed: [{ to: "approved_for_update", label: "Approve for knowledge update" }, { to: "rejected", label: "Reject" }],
  approved_for_update: [{ to: "rejected", label: "Reject" }],
  rejected: [],
};

function FeedbackReview() {
  const { withToken, session } = useAuth();
  const [filter, setFilter] = useState("pending_review");
  const [q, setQ] = useState<FeedbackQueue | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback((keepError = false) => { if (!keepError) setError(null); withToken((t) => api.trialFeedback(t, filter || undefined)).then(setQ).catch((e) => setError(msg(e))); }, [withToken, filter]);
  useEffect(() => { load(); }, [load]);
  const item: Feedback | undefined = q?.items.find((i) => i.call_id === sel);

  async function review(to: string) {
    if (!item || busy) return;
    setBusy(true); setError(null);
    try { await withToken((t) => api.reviewFeedback(t, item.call_id, to, note.trim() || undefined)); setNote(""); setSel(null); load(); } catch (e) { setError(msg(e)); load(true); } finally { setBusy(false); }
  }
  return (
    <section className="card" aria-label="Feedback awaiting review">
      <div className="bar"><h2>Intervention feedback</h2>
        <label>Show
          <select value={filter} onChange={(e) => { setFilter(e.target.value); setSel(null); }}>
            <option value="pending_review">Awaiting review</option><option value="">All submitted</option>
            {Object.entries(REVIEW_LABEL).filter(([k]) => k !== "pending_review").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label></div>
      <p className="muted tiny">Staff feedback is a proposed correction, not verified knowledge. Approving a record only marks it as ready for a later knowledge update: it does not change what the assistant knows.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {!q && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {q?.items.length === 0 && <p className="muted" data-testid="feedback-empty">No feedback {filter === "pending_review" ? "is waiting for review" : "matches"}.</p>}
      <div className="split">
        <ul className="list">
          {q?.items.map((i) => (
            <li key={i.call_id}><button className={`row ${sel === i.call_id ? "sel" : ""}`} onClick={() => setSel(i.call_id)} aria-label={`Feedback on call ${i.short_id}`}>
              <strong>{i.short_id}</strong> <span className="pill">{REVIEW_LABEL[i.review_status ?? ""] ?? i.review_status}</span>
              <span className="muted block">{i.category_label} · {i.staff}</span></button></li>
          ))}
        </ul>
        {item && (
          <article className="card" aria-label="Feedback detail">
            <h3>Call {item.short_id}</h3>
            <dl><dt>From</dt><dd>{item.staff}</dd><dt>Category</dt><dd>{item.category_label}</dd><dt>What went wrong</dt><dd>{item.what_was_wrong}</dd>
              {item.suggested_correction && <><dt>Suggested correction</dt><dd>{item.suggested_correction}</dd></>}
              <dt>Status</dt><dd>{REVIEW_LABEL[item.review_status ?? ""]}{item.reviewed_by ? ` by ${item.reviewed_by}` : ""}</dd></dl>
            {item.staff === session?.username
              ? <p className="muted">You wrote this feedback, so another administrator must review it.</p>
              : (NEXT[item.review_status ?? ""] ?? []).length > 0 && <>
                <label>Note (optional)<input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
                <div className="actions">{NEXT[item.review_status ?? ""].map((n) => <button key={n.to} className="btn" disabled={busy} onClick={() => void review(n.to)}>{n.label}</button>)}</div></>}
          </article>
        )}
      </div>
    </section>
  );
}

function Retention() {
  const { withToken } = useAuth();
  const [r, setR] = useState<RetentionStatus | null>(null);
  const [recs, setRecs] = useState<RecordingRow[]>([]);
  const [result, setResult] = useState<PurgeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [holdFor, setHoldFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const load = useCallback((keepError = false) => { if (!keepError) setError(null); withToken((t) => Promise.all([api.retention(t), api.recordings(t)])).then(([a, b]) => { setR(a); setRecs(b); }).catch((e) => setError(msg(e))); }, [withToken]);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>) { if (busy) return; setBusy(true); setError(null); let failed = false; try { await fn(); } catch (e) { failed = true; setError(msg(e)); } finally { setBusy(false); load(failed); } }
  const purge = (dry: boolean) => run(async () => setResult(await withToken((t) => api.purge(t, dry))));
  const hold = (id: string, on: boolean) => run(async () => { await withToken((t) => api.holdRecording(t, id, on, on ? reason.trim() : undefined)); setHoldFor(null); setReason(""); });

  return (
    <section className="card" aria-label="Recording and retention status">
      <div className="bar"><h2>Recordings and retention</h2><button className="btn small" onClick={() => load()}>Refresh</button></div>
      {error && <p role="alert" className="error">{error}</p>}
      {!r && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {r && (
        <>
          <p data-testid="provider-state">{r.provider.name === "none" ? "No recording provider is configured on this server: calls are never recorded." : `Recording provider: ${r.provider.name} (${r.provider.operational ? "ready" : "not available"}).`}</p>
          <div className="stats">
            <Stat label="Recordings kept" value={(r.recordings.by_status.completed ?? 0) + (r.recordings.by_status.failed ?? 0)} hint={`${r.recordings.by_status.purged ?? 0} purged`} />
            <Stat label="Due for deletion" value={r.recordings.eligible_now} hint={r.recordings.next_expiry ? `next expiry ${when(r.recordings.next_expiry)}` : "nothing scheduled"} />
            <Stat label="Under legal hold" value={r.recordings.held} hint="never deleted while held" />
            <Stat label="Deletion failing" value={r.recordings.purge_failing} hint="retried on every run" />
            <Stat label="Consent records due" value={r.consents.eligible_now} /><Stat label="Feedback due" value={r.feedback.eligible_now} hint="reviewed or skipped only" />
          </div>
          <p className="muted tiny">Kept for {r.policy.recording_days} days (recordings), {r.policy.consent_days} days (consent records), {r.policy.feedback_days} days (reviewed feedback). Calls, tickets and knowledge documents are never deleted by this job.{r.last_run ? ` Last run ${when(r.last_run.at)} by ${r.last_run.actor}.` : " It has not deleted anything yet."}</p>
          <div className="actions"><button className="btn" disabled={busy} onClick={() => void purge(true)}>Preview what would be deleted</button><button className="btn danger" disabled={busy} onClick={() => void purge(false)}>Delete expired data now</button></div>
          {result && <p role="status" className="banner done">{result.dry_run ? "Preview: would delete" : "Deleted"} {result.purged} recording(s), {result.consents} consent record(s), {result.feedback} feedback record(s){result.failed ? `; ${result.failed} could not be deleted and will be retried` : ""}{result.held ? `; ${result.held} skipped (legal hold)` : ""}.</p>}
        </>
      )}
      <h3>Recordings</h3>
      {recs.length === 0 && <p className="muted" data-testid="recordings-empty">No recordings.</p>}
      <ul className="list">
        {recs.map((x) => (
          <li key={x.id} className="card">
            <strong>{x.id}</strong> <span className="pill">{x.status}</span>{x.legal_hold && <span className="pill warn">legal hold: {x.hold_reason}</span>}
            <span className="muted block">{when(x.started_at)} · {(x.size_bytes / 1024).toFixed(0)} KB · expires {when(x.expires_at)}{x.purge_error ? ` · deletion failed (${x.purge_error})` : ""}</span>
            {x.status !== "purged" && (x.legal_hold
              ? <button className="btn small" disabled={busy} onClick={() => void hold(x.id, false)}>Release legal hold</button>
              : holdFor === x.id
                ? <span className="actions"><input aria-label="Reason for the legal hold" value={reason} maxLength={120} onChange={(e) => setReason(e.target.value)} /><button className="btn small" disabled={busy || !reason.trim()} onClick={() => void hold(x.id, true)}>Confirm hold</button><button className="btn small" onClick={() => setHoldFor(null)}>Cancel</button></span>
                : <button className="btn small" onClick={() => setHoldFor(x.id)}>Place legal hold</button>)}
          </li>
        ))}
      </ul>
    </section>
  );
}
