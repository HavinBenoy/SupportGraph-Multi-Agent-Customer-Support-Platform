"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { PhonePinStatus, TelephonyAccount, TelephonyAuditRow, TelephonyStatus, TelephonySummary } from "@/lib/types";
import { Stat } from "./Bars";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const ACTION: Record<string, string> = { phone_set: "number set", phone_removed: "number removed", pin_set: "PIN set by the account holder", pin_cleared: "PIN removed by the account holder",
  pin_reset_by_admin: "PIN reset by an administrator", pin_locked: "phone verification locked (too many wrong PINs)" };

/** M8, administrators only. The server enforces every rule (role, tenant, which accounts may carry a number); this screen shows state and sends requests.
 *  A telephone number only tells us who a caller CLAIMS to be. Personal records are released on the phone only after the caller types that account's PIN. */
export function TelephonyAdmin() {
  return (
    <div className="console">
      <h1>Telephone line</h1>
      <p className="muted">Callers start as anonymous visitors. A number that matches an account is shown to staff as a claim, never as proof: a student hears their own records only after typing their phone PIN. Staff can listen to and take over phone calls in Live Calls, exactly like browser calls.</p>
      <LineStatus />
      <PhoneSummary />
      <Accounts />
      <section className="card" aria-label="Your phone PIN"><h2>Your own phone PIN</h2><PhonePin /></section>
      <TelAudit />
    </div>
  );
}

function LineStatus() {
  const { withToken } = useAuth();
  const [s, setS] = useState<TelephonyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { withToken((t) => api.telStatus(t)).then(setS).catch((e) => setError(msg(e))); }, [withToken]);
  return (
    <section className="card" aria-label="Line status">
      <h2>Status</h2>
      {error && <p role="alert" className="error">{error}</p>}
      {!s && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {s && (<>
        <p><span className={`pill ${s.ready ? "ok" : s.enabled ? "warn" : ""}`}>{!s.enabled ? "Off" : s.ready ? "Ready" : "Needs attention"}</span> Provider: <b>{s.provider === "none" ? "none" : "Edesy"}</b>{s.enabled ? ` · ${s.sample_rate / 1000} kHz · up to ${s.max_concurrent} calls at once · ${s.active_phone_calls} now` : ""}</p>
        {s.problems.length > 0 && <ul className="notes">{s.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
        <p className="muted tiny">{s.approval_note} The stream URL contains a secret token and is never shown here{s.stream_path ? ` (path ${s.stream_path})` : ""}.</p>
        <p className="muted tiny">Phone PIN: {s.pin.length} digits · {s.pin.attempts_per_call} attempts per call · locked for {s.pin.lock_minutes} min after {s.pin.max_attempts} wrong PINs in a row.</p>
      </>)}
    </section>
  );
}

function PhoneSummary() {
  const { withToken } = useAuth();
  const [s, setS] = useState<TelephonySummary | null>(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { withToken((t) => api.telSummary(t, days)).then((r) => { setS(r); setError(null); }).catch((e) => setError(msg(e))); }, [withToken, days]);
  return (
    <section className="card" aria-label="Phone calls">
      <div className="bar"><h2>Phone calls</h2>
        <label className="inline">Period<select value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select></label></div>
      {error && <p role="alert" className="error">{error}</p>}
      {s && (s.phone.total === 0 ? <p className="muted">No phone calls in this period. Browser voice calls: {s.web_calls}.</p> : (
        <div className="stats">
          <Stat label="Phone calls" value={s.phone.total} hint={`${s.web_calls} browser voice calls`} />
          <Stat label="Verified callers" value={s.phone.verified} def="Typed the phone PIN of the account their number claims" />
          <Stat label="Claimed, not verified" value={s.phone.claimed_unverified} />
          <Stat label="Unknown numbers" value={s.phone.unknown} />
          <Stat label="Taken over by staff" value={s.phone.taken_over} />
          <Stat label="Failed" value={s.phone.failed} />
        </div>))}
    </section>
  );
}

function Accounts() {
  const { withToken } = useAuth();
  const [rows, setRows] = useState<TelephonyAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [num, setNum] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const load = useCallback(() => { withToken((t) => api.telAccounts(t)).then((r) => { setRows(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [withToken]);
  useEffect(() => { load(); }, [load]);
  const run = (fn: (t: string) => Promise<unknown>, done: string) => {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null);
    withToken(fn).then(() => { setNotice(done); setEditing(null); setNum(""); setConfirm(null); load(); }).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  const save = (e: FormEvent, u: string) => { e.preventDefault(); run((t) => api.telSetPhone(t, u, num), `Number saved for ${u}.`); };
  return (
    <section className="card" aria-label="Accounts and numbers">
      <h2>Accounts, numbers and PINs</h2>
      <p className="muted tiny">Students' numbers come from their student record. Staff and administrator numbers are set here. Numbers are shown masked.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="banner">{notice}</p>}
      {!rows && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {rows && (
        <div className="table-wrap"><table className="kb-doctable">
          <thead><tr><th scope="col">Account</th><th scope="col">Role</th><th scope="col">Number</th><th scope="col">Phone PIN</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.username}>
              <th scope="row">{a.username}{a.disabled ? " (disabled)" : ""}</th>
              <td>{a.role}</td>
              <td>{editing === a.username ? (
                <form className="inline" onSubmit={(e) => save(e, a.username)} aria-label={`Number for ${a.username}`}>
                  <input aria-label={`Telephone number for ${a.username}`} value={num} onChange={(e) => setNum(e.target.value)} inputMode="tel" autoComplete="off" required maxLength={24} placeholder="+91 98450 12345" />
                  <button className="btn small primary" disabled={busy}>Save</button><button type="button" className="btn small" onClick={() => setEditing(null)}>Cancel</button>
                </form>) : (a.phone_masked ?? <span className="muted">none</span>)}</td>
              <td>{a.has_pin ? (a.locked_until ? `locked until ${when(a.locked_until)}` : "set") : <span className="muted">not set</span>}</td>
              <td className="actions">
                {a.role !== "student" && editing !== a.username && <button className="btn small" onClick={() => { setEditing(a.username); setNum(""); }}>{a.phone_masked ? "Change number" : "Set number"}</button>}
                {a.role !== "student" && a.phone_masked && <button className="btn small" disabled={busy} onClick={() => run((t) => api.telDeletePhone(t, a.username), `Number removed for ${a.username}.`)}>Remove number</button>}
                {a.has_pin && (confirm === a.username
                  ? <><button className="btn small danger" disabled={busy} onClick={() => run((t) => api.telResetPin(t, a.username), `Phone PIN reset for ${a.username}. They can set a new one after signing in.`)}>Confirm reset</button><button className="btn small" onClick={() => setConfirm(null)}>Cancel</button></>
                  : <button className="btn small" onClick={() => setConfirm(a.username)}>Reset PIN</button>)}
              </td>
            </tr>))}</tbody>
        </table></div>)}
    </section>
  );
}

function TelAudit() {
  const { withToken } = useAuth();
  const [rows, setRows] = useState<TelephonyAuditRow[] | null>(null);
  useEffect(() => { withToken((t) => api.telAudit(t)).then((r) => setRows(r.items)).catch(() => setRows([])); }, [withToken]);
  return (
    <section className="card" aria-label="Telephony audit">
      <h2>Audit</h2>
      {rows?.length === 0 && <p className="muted">Nothing recorded yet.</p>}
      <ul className="notes">{rows?.map((r) => <li key={r.id} className="tiny">{when(r.at)} · {r.actor} · {ACTION[r.action] ?? r.action}{r.subject ? ` · ${r.subject}` : ""}</li>)}</ul>
    </section>
  );
}

/** One's own phone-support PIN (any signed-in account). Setting or removing it needs the current password; the PIN is never shown again. */
export function PhonePin() {
  const { withToken } = useAuth();
  const [st, setSt] = useState<PhonePinStatus | null>(null);
  const [pw, setPw] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { withToken((t) => api.myPin(t)).then(setSt).catch((e) => setError(msg(e))); }, [withToken]);
  useEffect(() => { load(); }, [load]);
  const len = st?.length ?? 6;
  const go = (fn: (t: string) => Promise<unknown>, done: string) => {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null);
    withToken(fn).then(() => { setNotice(done); setPw(""); setPin(""); load(); }).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  const submit = (e: FormEvent) => { e.preventDefault(); go((t) => api.setMyPin(t, pw, pin), "Phone PIN saved."); };
  return (
    <div className="phone-pin">
      <p className="muted tiny">On a phone call to support, press <b>*</b>, your PIN, then <b>#</b> to hear your own records. Your phone number alone is never enough.{st && st.phone_line === false ? " (The telephone line is not switched on yet.)" : ""}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="banner">{notice}</p>}
      {st && <p>Status: <b>{st.has_pin ? (st.locked_until ? `locked until ${when(st.locked_until)}` : "PIN set") : "no PIN"}</b>{st.updated_at ? <span className="muted tiny"> · changed {when(st.updated_at)}</span> : null}</p>}
      <form className="form" onSubmit={submit} aria-label="Set phone PIN">
        <label>Current password<input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} required /></label>
        <label>New {len}-digit PIN<input type="password" inputMode="numeric" autoComplete="off" pattern={`[0-9]{${len}}`} minLength={len} maxLength={len} value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} required /></label>
        <div className="actions">
          <button className="btn primary" disabled={busy || pin.length !== len || !pw}>{st?.has_pin ? "Change PIN" : "Set PIN"}</button>
          {st?.has_pin && <button type="button" className="btn" disabled={busy || !pw} onClick={() => go((t) => api.clearMyPin(t, pw), "Phone PIN removed.")}>Remove PIN</button>}
        </div>
      </form>
    </div>
  );
}
