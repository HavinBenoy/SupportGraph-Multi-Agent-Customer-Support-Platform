"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { createLiveClient, liveWsUrl, realLiveDeps, type LiveClient, type LiveHandlers, type LiveMsg, type LiveState } from "@/lib/live";
import type { CallAction, CallStatus, LiveCall } from "@/lib/types";

const POLL_MS = 3000;
const STATUS_LABEL: Record<CallStatus, string> = {
  created: "Connecting", active_ai: "Assistant on call", staff_takeover: "Staff on call", returning_to_ai: "Returning to assistant", ended: "Ended", failed: "Failed",
};
const END_REASON: Record<string, string> = {
  ended_by_staff: "ended by staff", caller_disconnected: "caller hung up", caller_ended: "caller ended it", goodbye: "caller said goodbye", limit: "reached its limit",
  error: "ended by an error", server_restart: "server restarted", server_shutdown: "server stopped", return_failed: "could not be returned to the assistant",
};
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const clock = (s: number) => `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;

export interface LiveCallsDeps { createClient: (h: LiveHandlers) => LiveClient; wsUrl: (callId: string) => string }
const realDeps: LiveCallsDeps = { createClient: (h) => createLiveClient(h, realLiveDeps), wsUrl: (id) => liveWsUrl(id) };
interface Conn { callId: string; state: LiveState; speaking: boolean; muted: boolean }
interface Line { id: number; speaker: "caller" | "ai"; text: string }
let lineId = 1;

/** Staff Live Calls (M4). What the buttons allow comes from the SERVER's view of each call (owner, status, is_mine), refreshed every few seconds and after
 *  every action; the browser only remembers its own audio connection. The server re-checks every action, so a stale screen can only get a clear refusal. */
export function LiveCalls({ deps = realDeps }: { deps?: LiveCallsDeps }) {
  const { withToken, session } = useAuth();
  const [calls, setCalls] = useState<LiveCall[] | null>(null);
  const [selId, setSelId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);        // action / audio problems
  const [feedError, setFeedError] = useState<string | null>(null); // the list could not be refreshed (kept apart so a refresh does not erase an action error)
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<CallAction | "stop" | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [conn, setConn] = useState<Conn | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const client = useRef<LiveClient | null>(null);
  const pendingSpeak = useRef<string | null>(null);   // speak as soon as the audio connection for this call is joined (set by a takeover)
  const inflight = useRef(false);                     // blocks a second click before React has re-rendered the disabled button
  const fetchedAt = useRef(Date.now());
  const seq = useRef(0);
  const alive = useRef(true);
  const me = session?.username ?? "";
  const isAdmin = session?.role === "admin";

  const load = useCallback(() => {
    const n = ++seq.current;
    withToken((t) => api.calls(t)).then((r) => {
      if (!alive.current || n !== seq.current) return;      // a newer refresh already answered
      fetchedAt.current = Date.now(); setCalls(r); setFeedError(null);
    }).catch((e) => { if (alive.current && n === seq.current) setFeedError(msg(e)); });
  }, [withToken]);

  useEffect(() => { alive.current = true; load(); const t = setInterval(load, POLL_MS); return () => { alive.current = false; clearInterval(t); }; }, [load]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const stopClient = useCallback(() => { const c = client.current; client.current = null; pendingSpeak.current = null; c?.disconnect(); setConn(null); }, []);
  useEffect(() => stopClient, [stopClient]);                // leaving the page releases the socket, the microphone and the audio context

  const patch = (c: LiveCall) => setCalls((l) => (l ? l.map((x) => (x.id === c.id ? c : x)) : l));

  function startListening(id: string, speakAfter = false) {
    if (!session) return;
    stopClient();
    setLines([]);
    pendingSpeak.current = speakAfter ? id : null;
    const handlers: LiveHandlers = {
      onState: (s) => {
        if (client.current !== c) return;
        setConn((x) => ({ callId: id, state: s, speaking: x?.speaking ?? false, muted: false }));
        if (s === "joined" && pendingSpeak.current === id) { pendingSpeak.current = null; void c.speak(); }
        if (s === "closed") { client.current = null; setConn(null); load(); }
      },
      onMic: (on) => { if (client.current === c) setConn((x) => (x ? { ...x, speaking: on, muted: on ? false : x.muted } : x)); },
      onError: (d) => { if (client.current === c) setError(d); },
      onMsg: (m: LiveMsg) => {
        if (client.current !== c) return;
        if (m.type === "transcript") setLines((l) => [...l.slice(-29), { id: lineId++, speaker: m.speaker, text: m.text }]);
        else if (m.type === "ownership") load();
        else if (m.type === "call_ended") { stopClient(); setNotice("This call has ended."); load(); }
      },
    };
    const c = deps.createClient(handlers);
    client.current = c;
    setConn({ callId: id, state: "connecting", speaking: false, muted: false });
    c.connect(deps.wsUrl(id), session.token);
  }

  async function run(action: CallAction | "stop", fn: () => Promise<void>) {
    if (inflight.current) return;
    inflight.current = true; setBusy(action); setError(null); setNotice(null);
    try { await fn(); } catch (e) { setError(msg(e)); load(); } finally { inflight.current = false; setBusy(null); }
  }

  const listen = (c: LiveCall) => run("listen", async () => {
    patch(await withToken((t) => api.callAction(t, c.id, "listen")));           // the server checks the call is live and yours to see; then connect
    startListening(c.id, c.is_mine);                                              // if you already hold the call, reconnecting also reopens your microphone
    load();
  });
  const stop = () => run("stop", async () => { client.current?.stopSpeaking(); stopClient(); load(); });
  const takeOver = (c: LiveCall) => run("takeover", async () => {
    const u = await withToken((t) => api.callAction(t, c.id, "takeover"));
    patch(u);
    setNotice(`You have taken over call ${u.short_id}. The assistant is silent.`);
    if (client.current && conn?.callId === c.id && conn.state === "joined") await client.current.speak();
    else startListening(c.id, true);
    load();
  });
  const giveBack = (c: LiveCall) => run("return", async () => {
    client.current?.stopSpeaking();                                              // stop sending first, then hand the call back
    const u = await withToken((t) => api.callAction(t, c.id, "return"));
    patch(u); setNotice(`Call ${u.short_id} is back with the assistant.`); load();
  });
  const endIt = (c: LiveCall) => run("end", async () => {
    setConfirmEnd(false);
    if (conn?.callId === c.id) stopClient();
    const u = await withToken((t) => api.callAction(t, c.id, "end"));
    patch(u); setNotice(`Call ${u.short_id} was ended.`); load();
  });

  const sel = calls?.find((c) => c.id === selId) ?? null;
  useEffect(() => { setConfirmEnd(false); }, [selId]);
  const duration = (c: LiveCall) => (c.live ? c.duration_s + Math.max(0, Math.floor((now - fetchedAt.current) / 1000)) : c.duration_s);
  const owner = (c: LiveCall) => (c.owner === "staff" ? `Staff: ${c.owner_staff ?? "?"}${c.is_mine ? " (you)" : ""}` : c.owner === "ai" ? "Assistant" : "—");
  const hearing = (c: LiveCall) => (c.listening || (conn?.callId === c.id && conn.state === "joined") ? "You are listening" : c.listeners > 0 ? `${c.listeners} listening` : "");

  const mine = sel ? conn?.callId === sel.id : false;
  const joined = mine && conn?.state === "joined";
  const live = !!sel?.live;
  const can = {
    listen: live && !(mine && (conn?.state === "joined" || conn?.state === "connecting")),
    stop: mine,
    takeover: live && sel!.owner === "ai" && sel!.status === "active_ai",
    giveBack: live && sel!.owner === "staff" && (sel!.is_mine || isAdmin),
    end: live,
  };
  const off = busy !== null;

  return (
    <div className="split">
      <section aria-label="Live calls">
        <div className="bar"><p className="muted">Calls in progress and those that ended in the last half hour.</p><button className="btn small" onClick={load}>Refresh</button></div>
        {feedError && <p role="alert" className="error">Live updates paused: {feedError}</p>}
        {calls === null && !feedError && <p className="muted" aria-busy="true">Loading…</p>}
        {calls?.length === 0 && <p className="muted">No calls right now.</p>}
        <ul className="list">
          {calls?.map((c) => (
            <li key={c.id}>
              <button className={`row ${selId === c.id ? "sel" : ""}`} onClick={() => setSelId(c.id)} aria-label={`Call ${c.short_id}, ${STATUS_LABEL[c.status]}`}>
                <strong>{c.short_id}</strong> <span className={`pill call-${c.status}`}>{STATUS_LABEL[c.status]}</span>{" "}
                {hearing(c) && <span className="pill">{hearing(c)}</span>}
                <span className="muted block">{owner(c)} · {c.caller === "student" ? "student" : "visitor"} {c.ticket_ref} · started {new Date(c.created_at).toLocaleTimeString()} · {clock(duration(c))}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="Call detail">
        {!sel && <p className="muted">Select a call to listen, take over or end it.</p>}
        {sel && (
          <article className="card">
            <h2>Call {sel.short_id} <span className={`pill call-${sel.status}`}>{STATUS_LABEL[sel.status]}</span></h2>
            <dl>
              <dt>Audio owner</dt><dd>{owner(sel)}</dd>
              <dt>Caller</dt><dd>{sel.caller === "student" ? "Signed-in student" : "Visitor"} · ref {sel.ticket_ref}{sel.student_id ? ` · ${sel.student_id}` : ""}</dd>
              <dt>Started</dt><dd>{new Date(sel.created_at).toLocaleString()} · {clock(duration(sel))}</dd>
              {sel.takeover_at && <><dt>Last takeover</dt><dd>{new Date(sel.takeover_at).toLocaleTimeString()}</dd></>}
              {!sel.live && <><dt>Ended</dt><dd>{sel.ended_at ? new Date(sel.ended_at).toLocaleTimeString() : ""} · {END_REASON[sel.end_reason ?? ""] ?? sel.end_reason ?? ""}</dd></>}
              <dt>Listening</dt><dd>{hearing(sel) || "Nobody"}{sel.listening_staff.length ? ` (${sel.listening_staff.join(", ")})` : ""}</dd>
            </dl>
            {error && <p role="alert" className="error">{error}</p>}
            {notice && <p role="status" className="banner done">{notice}</p>}
            {sel.live && sel.owner === "staff" && sel.is_mine && <p role="status" className="banner">You are on the call. {mine && conn?.speaking ? (conn.muted ? "Your microphone is muted." : "The caller can hear you.") : "Your microphone is off: the caller cannot hear you."} Use headphones to avoid echo.</p>}
            {sel.live && sel.owner === "staff" && !sel.is_mine && <p role="status" className="banner">{sel.owner_staff} has taken over this call.</p>}
            {mine && conn?.state === "connecting" && <p className="muted" aria-busy="true">Connecting audio…</p>}
            <div className="actions">
              <button className="btn" disabled={off || !can.listen} onClick={() => void listen(sel)}>Listen</button>
              <button className="btn" disabled={off || !can.stop} onClick={() => void stop()}>Stop listening</button>
              <button className="btn primary" disabled={off || !can.takeover} onClick={() => void takeOver(sel)}>Take over</button>
              <button className="btn" disabled={off || !can.giveBack} onClick={() => void giveBack(sel)}>Return to AI</button>
              {!confirmEnd
                ? <button className="btn danger" disabled={off || !can.end} onClick={() => setConfirmEnd(true)}>End call</button>
                : <span className="actions"><button className="btn danger" disabled={off} onClick={() => void endIt(sel)}>Confirm end call</button><button className="btn" onClick={() => setConfirmEnd(false)}>Cancel</button></span>}
              {sel.is_mine && sel.owner === "staff" && joined && !conn?.speaking && <button className="btn" disabled={off} onClick={() => void client.current?.speak()}>Enable microphone</button>}
              {conn?.speaking && <button className="btn" aria-pressed={conn.muted} onClick={() => { const v = !conn.muted; client.current?.mute(v); setConn({ ...conn, muted: v }); }}>{conn.muted ? "Unmute microphone" : "Mute microphone"}</button>}
            </div>
            {sel.live && sel.is_mine && sel.owner === "staff" && !mine && <p className="muted">Your audio connection is not open. Press Listen to reconnect; the call returns to the assistant if you stay away.</p>}
            {mine && joined && (
              <div className="log" role="log" aria-label="Call transcript" aria-live="polite">
                {lines.length === 0 && <p className="muted empty">Listening. The conversation appears here as text too.</p>}
                {lines.map((l) => <div key={l.id} className={`msg ${l.speaker === "caller" ? "user" : "assistant"}`}><span className="sr-only">{l.speaker === "caller" ? "Caller" : "Assistant"}: </span><p>{l.text}</p></div>)}
              </div>
            )}
          </article>
        )}
      </section>
    </div>
  );
}
