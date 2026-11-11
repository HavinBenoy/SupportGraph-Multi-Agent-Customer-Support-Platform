"use client";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ChatStatus, Citation, PublicConfig } from "@/lib/types";
import { browserMic, browserSocket, createPlayer, parseAudioFrame, voiceWsUrl, type Mic, type MicFactory, type Player, type ServerMsg, type Socket, type SocketFactory } from "@/lib/voice";
import { LoginForm } from "./LoginForm";

type Phase = "idle" | "connecting" | "listening" | "hearing" | "transcribing" | "thinking" | "speaking";
interface Line { id: number; turn?: string; role: "user" | "assistant"; text: string; citations?: Citation[]; status?: ChatStatus; stale?: boolean; partial?: boolean }
let nextId = 1;

const PHASE_LABEL: Record<Phase, string> = {
  idle: "Not in a call", connecting: "Connecting…", listening: "Listening… ask your question", hearing: "Hearing you…",
  transcribing: "Understanding what you said…", thinking: "Thinking…", speaking: "Speaking… talk to interrupt",
};
const BANNERS: Partial<Record<ChatStatus, string>> = {
  awaiting_staff: "Your request is waiting for a staff member to review. You can end the call: your conversation is saved in the chat.",
  awaiting_confirmation: "Please confirm: say “yes” to go ahead or “no” to cancel.",
  escalated: "A staff member will follow up. Keep your reference number (it is also shown in the chat).",
};

export interface VoiceDeps { socket: SocketFactory; mic: MicFactory; player: () => Player; wsUrl: () => string }
const realDeps: VoiceDeps = { socket: browserSocket, mic: browserMic, player: () => createPlayer(), wsUrl: () => voiceWsUrl() };

export function VoiceCall({ tenant, deps = realDeps }: { tenant?: string; deps?: VoiceDeps }) {
  const { session, ready } = useAuth();
  const student = session?.role === "student" ? session : null;
  const token = student?.token ?? null;
  const storeKey = `sg.ticket.${student ? `${student.tenantId}.${student.username}` : "guest"}`; // the SAME key as the text chat: one conversation

  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [withStaff, setWithStaff] = useState(false);   // M4.1: a staff member has taken the call (the assistant stays silent for the rest of the call)
  const [trial, setTrial] = useState<Extract<ServerMsg, { type: "trial_notice" }> | null>(null);   // M5: Trial Verification Mode disclosure and the recording choice
  const [consent, setConsent] = useState<"offered" | "granted" | "declined" | "withdrawn">("offered");
  const [recording, setRecording] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [showLogin, setShowLogin] = useState(false);
  const sock = useRef<Socket | null>(null), mic = useRef<Mic | null>(null), player = useRef<Player | null>(null), endRef = useRef<HTMLDivElement>(null), ending = useRef(false), callNo = useRef(0), fillerNow = useRef(false);
  // Turn ids restart at 1 on every call: line keys include the call number so call 2 can never overwrite call 1's transcript.
  const key = (turn?: number) => (turn === undefined ? undefined : `${callNo.current}:${turn}`);
  const live = phase !== "idle";
  const tenantArg = tenant ?? cfg?.tenant_id;

  useEffect(() => { api.config(tenant).then(setCfg).catch(() => setError("The support service is not reachable right now.")); }, [tenant]);
  useEffect(() => {
    fetch("/api/voice/status", { cache: "no-store" }).then((r) => r.json())
      .then((j: { ready: boolean; problems: string[] }) => setProblems(j.ready ? [] : j.problems))
      .catch(() => setProblems(["Cannot reach the server."]));
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end" }); }, [lines, phase]);

  const hangUp = useCallback(() => {
    try { sock.current?.send(JSON.stringify({ type: "stop" })); } catch { /* already closed */ }
    sock.current?.close(); sock.current = null;
    mic.current?.stop(); mic.current = null;
    player.current?.stop(); player.current = null;
    setPhase("idle"); setMuted(false); setWithStaff(false); setTrial(null); setConsent("offered"); setRecording(false); setConsentError(null);
  }, []);
  useEffect(() => hangUp, [hangUp]); // leaving the page ends the call and releases the microphone

  const onMsg = useCallback((m: ServerMsg) => {
    switch (m.type) {
      case "ready":
        try { sessionStorage.setItem(storeKey, m.ticket_id); } catch { /* ignore */ }
        setPhase("listening"); break;
      // M2.2: the browser no longer stops the bot on every raw "speech_start" (a cough or the bot's own echo would cut it off). The server decides
      // whether it is a real interruption (sustained speech) and says so with `interrupted{turn_id}`; only then does playback stop.
      case "speech_start": setPhase("hearing"); break;
      case "transcribing": setPhase("transcribing"); break;
      case "transcript":
        if (m.ignored || !m.text) { setPhase("listening"); break; }
        // one line per user turn: a partial transcript is updated in place when the same turn is merged or finalised
        setLines((l) => {
          const i = m.turn_id === undefined ? -1 : l.findIndex((x) => x.role === "user" && x.turn === key(m.turn_id));
          if (i < 0) return [...l, { id: nextId++, turn: key(m.turn_id), role: "user", text: m.text, partial: m.final === false }];
          const copy = l.slice(); copy[i] = { ...copy[i], text: m.text, partial: m.final === false }; return copy;
        });
        break;
      case "thinking": setPhase("thinking"); setNotice(null); break;
      case "reply":
        setStatus(m.status as ChatStatus);
        setLines((l) => [...l, { id: nextId++, turn: key(m.turn_id), role: "assistant", text: m.reply, citations: m.citations, status: m.status as ChatStatus, stale: m.stale }]);
        setPhase(m.stale ? "listening" : "speaking"); break;      // a stale answer (a newer question exists) is shown but never spoken
      case "audio_start": fillerNow.current = !!m.filler; if (m.turn_id !== undefined) { if (m.staff) player.current?.begin(m.turn_id, m.sample_rate, 150); else player.current?.begin(m.turn_id, m.sample_rate); } break;   // a live human voice gets a bigger start-up buffer
      case "audio_end":
        if (m.turn_id !== undefined) { if (m.interrupted) player.current?.stop(m.turn_id); else player.current?.end(m.turn_id); }
        if (!m.filler) setPhase((p) => (p === "speaking" || p === "thinking" ? "listening" : p));   // a holding phrase is not the end of the turn
        break;
      case "interrupted": player.current?.stop(m.turn_id); break;
      case "ownership": // the server says who is speaking with the caller; the answer in progress was already cut by an `interrupted` message
        if (m.owner === "staff") { setWithStaff(true); setNotice("A staff member has joined your call. The assistant will not speak again on this call."); }
        setPhase((p) => (p === "idle" ? p : "listening")); break;
      case "trial_notice": setTrial(m); setConsent("offered"); setConsentError(null); break;
      case "consent":
        if (m.error) setConsentError(m.error); else { if (!(m.status === "granted" && !m.recording)) setConsentError(null); if (m.status) setConsent(m.status); setRecording(!!m.recording); }   // "agreed but not recording" keeps the reason the `recording` message gave
        break;
      case "recording":
        setRecording(m.state === "on");
        if (m.state === "off" && m.reason === "storage_unavailable") setConsentError("Recording could not be started, so this call is not being recorded. The call itself is unaffected.");
        else if (m.state === "off" && m.reason === "trial_disabled") setConsentError("Recording was switched off. This call is no longer being recorded.");
        else if (m.state === "off" && m.reason && m.reason !== "consent_withdrawn" && m.reason !== "call_ended") setConsentError("Recording stopped. This call is no longer being recorded.");
        break;
      case "call_end": { // goodbye: let the spoken closing finish playing, then hang up cleanly (no "call ended" error)
        ending.current = true;
        const finish = () => { if (!ending.current) return; ending.current = false; hangUp(); setNotice(m.reason === "ended_by_staff" ? "The call was ended by a staff member. Your conversation is saved in the chat." : "Call ended. Thanks for calling."); };
        if (player.current?.playing) { player.current.onidle = finish; setTimeout(finish, 20000); } else finish();
        break;
      }
      case "notice": if (m.detail !== "too_short") setNotice(m.detail); break;
      case "error": setError(m.detail); if (m.fatal) hangUp(); else setPhase((p) => (p === "idle" ? p : "listening")); break;
    }
  }, [storeKey, hangUp]);

  async function startCall() {
    callNo.current += 1;
    setError(null); setNotice(null); setPhase("connecting");
    try {
      player.current = deps.player();
      player.current.onidle = () => setPhase((p) => (p === "speaking" ? "listening" : p));
      player.current.onplayed = (turn) => { try { sock.current?.send(JSON.stringify({ type: "playback", turn_id: turn, state: "ended" })); } catch { /* closed */ } };
      player.current.onstarted = (turn) => { try { sock.current?.send(JSON.stringify({ type: "playback", turn_id: turn, state: "started", filler: fillerNow.current })); } catch { /* closed */ } };
      mic.current = await deps.mic((pcm) => sock.current?.send(pcm));
    } catch (e) {
      setPhase("idle"); player.current = null;
      setError(e instanceof Error && e.message ? e.message : "The microphone is not available. Allow microphone access for this site and try again.");
      return;
    }
    let ticket: string | null = null;
    try { ticket = sessionStorage.getItem(storeKey); } catch { /* ignore */ }
    sock.current = deps.socket(deps.wsUrl(), {
      onopen: () => sock.current?.send(JSON.stringify({ type: "start", tenant: tenantArg, token, ticket_id: ticket })),
      onmessage: (d) => { if (typeof d === "string") { try { onMsg(JSON.parse(d) as ServerMsg); } catch { /* ignore a malformed message */ } } else { const f = parseAudioFrame(d); if (f) player.current?.push(f.turn, f.pcm); } },
      onclose: () => { if (ending.current) return; if (sock.current) { setError((e) => e ?? "The call ended."); hangUp(); } },
      onerror: () => { setError("Cannot reach the voice service."); hangUp(); },
    });
  }

  function send(e?: FormEvent) {
    e?.preventDefault();
    const q = text.trim();
    if (!q || !live) return;
    sock.current?.send(JSON.stringify({ type: "text", text: q })); setText("");
  }
  // M5: an explicit button press is the ONLY way recording consent is ever given, declined or withdrawn.
  function decide(decision: "granted" | "declined" | "withdrawn") {
    if (trial?.recording.offered !== true) return;
    setConsentError(null);
    try { sock.current?.send(JSON.stringify({ type: "consent", decision, policy_version: trial.recording.policy_version })); } catch { setConsentError("Your choice could not be sent. Nothing is being recorded."); }
  }
  function toggleMute() { const v = !muted; mic.current?.mute(v); setMuted(v); }

  if (session && !student) return <div className="card narrow"><p>You are signed in as {session.role}. The voice line is for students and visitors.</p></div>;
  const blocked = problems !== null && problems.length > 0;
  return (
    <section className="chat card voice" aria-label="Voice call">
      <div className="chat-head">
        <div>
          <h1>{cfg ? `${cfg.tenant_name}: voice` : "Voice"}</h1>
          <p className="muted">{student ? `Signed in as ${student.username}: you can ask about your own batches and attendance.` : "Ask about courses, fees and policies by voice. Sign in for questions about your own records."}</p>
          {cfg?.is_sample && <p className="pill warn">Sample data: not a real institute</p>}
        </div>
        <div className="actions">{!student && !live && <button className="btn small" onClick={() => setShowLogin((v) => !v)}>{showLogin ? "Hide sign in" : "Student sign in"}</button>}</div>
      </div>
      {!student && showLogin && <LoginForm heading="Student sign in" onDone={() => setShowLogin(false)} />}

      {blocked && <div className="banner escalated" role="status"><strong>Voice is not ready on this server.</strong><ul>{problems!.map((p) => <li key={p}>{p}</li>)}</ul></div>}

      <div className={`voice-stage phase-${phase}${muted ? " muted" : ""}`} aria-hidden="true">
        <span className="voice-orb"><span className="ring" /><span className="ring" /><span className="core">{live ? (muted ? "🔇" : "🎙") : "☎"}</span></span>
        <span className="voice-bars">{[0, 1, 2, 3, 4].map((i) => <i key={i} />)}</span>
      </div>
      <div className="callbar">
        {!live
          ? <button className="btn primary" onClick={() => void startCall()} disabled={!ready || blocked}>Start call</button>
          : <><button className="btn danger" onClick={hangUp}>End call</button><button className="btn" onClick={toggleMute} aria-pressed={muted}>{muted ? "Unmute microphone" : "Mute microphone"}</button></>}
        <span className={`phase ${phase}`} role="status" aria-live="polite">{PHASE_LABEL[phase]}{muted && live ? " (muted)" : ""}</span>
      </div>

      <div className="log" role="log" aria-live="polite" aria-label="Call transcript">
        {lines.length === 0 && <p className="muted empty">Press “Start call”, allow the microphone, then ask your question. Everything said appears here as text too.</p>}
        {lines.map((m) => (
          <div key={m.id} className={`msg-row ${m.role}`}>
            <span className="avatar" aria-hidden="true">{m.role === "user" ? "You" : "SG"}</span>
            <div className={`msg ${m.role}${m.partial ? " partial" : ""}`}>
              <span className="sr-only">{m.role === "user" ? "You" : "Assistant"}: </span><p>{m.text}</p>
              {m.stale && <p className="muted tiny">Not spoken: you had already asked something newer.</p>}
              {m.citations && m.citations.length > 0 && <ul className="cites chips" aria-label="Sources">{m.citations.map((c) => <li key={c.id}><span aria-hidden="true">❖ </span><span>{c.heading || c.id}</span></li>)}</ul>}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {trial && live && (
        <div className="banner trial" role="region" aria-label="Trial call notice">
          <p>{trial.disclosure}</p>
          {trial.recording.offered && (
            <div className="consent">
              <p>{trial.recording.notice}</p>
              <p role="status" aria-live="polite"><strong>{recording ? "Recording is on." : consent === "granted" ? "Recording is not running." : "This call is not being recorded."}</strong>
                {consent === "declined" && " You said no. The call works exactly the same."}{consent === "withdrawn" && " You stopped the recording."}{consent === "offered" && " Recording is optional: it only starts if you choose “Allow recording”."}</p>
              <div className="actions">
                {(consent === "offered" || consent === "declined" || consent === "withdrawn") && <button className="btn primary" onClick={() => decide("granted")}>{consent === "offered" ? "Allow recording" : "Allow recording after all"}</button>}
                {consent === "offered" && <button className="btn" onClick={() => decide("declined")}>No thanks</button>}
                {consent === "granted" && <button className="btn" onClick={() => decide("withdrawn")}>Stop recording</button>}
              </div>
            </div>
          )}
          {consentError && <p className="error" role="alert">{consentError}</p>}
        </div>
      )}
      {recording && <p className="pill warn" role="status" aria-label="Recording indicator">● Recording</p>}
      {withStaff && <p className="banner" role="status">You are speaking with a staff member. Talk as you would on a phone call. The assistant will not speak again on this call.</p>}
      {status && BANNERS[status] && <p className={`banner ${status}`} role="status">{BANNERS[status]}</p>}
      {notice && <p className="muted tiny" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <form className="composer modern" onSubmit={send}>
        <label className="sr-only" htmlFor="voice-text">Type instead</label>
        <input id="voice-text" value={text} maxLength={1000} placeholder={!live ? "Start a call to type or speak" : withStaff ? "A staff member is on the call" : "Or type your question"} disabled={!live || withStaff} onChange={(e) => setText(e.target.value)} />
        <button className="btn" disabled={!live || withStaff || !text.trim()}>Send</button>
      </form>
      <p className="muted tiny">Your voice is processed on this institute’s own server and is not stored, unless you agree to a recording on a trial call; only the text of the conversation is saved, like the chat.</p>
    </section>
  );
}
