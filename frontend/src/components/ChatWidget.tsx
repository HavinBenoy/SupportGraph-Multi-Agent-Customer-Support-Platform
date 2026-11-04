"use client";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ChatStatus, Citation, PublicConfig } from "@/lib/types";
import { LoginForm } from "./LoginForm";

interface Msg { id: number; role: "user" | "assistant"; content: string; citations?: Citation[]; status?: ChatStatus }

export const POLL_MS = 4000;
const MAX_LEN = 1000;
let nextId = 1;

const BANNERS: Partial<Record<ChatStatus, string>> = {
  awaiting_staff: "Waiting for a staff member to review your request. You can leave this page open or come back later: your conversation is saved.",
  awaiting_confirmation: "Please confirm: reply “yes” to go ahead or “no” to cancel.",
  escalated: "A staff member will follow up. Keep your reference number.",
};

export function ChatWidget({ tenant, pollMs = POLL_MS }: { tenant?: string; pollMs?: number }) {
  const { session, ready } = useAuth();
  const student = session?.role === "student" ? session : null;
  const token = student?.token ?? null;
  const storeKey = `sg.ticket.${student ? `${student.tenantId}.${student.username}` : "guest"}`;

  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [ticket, setTicket] = useState<string | null>(null);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const tenantArg = tenant ?? cfg?.tenant_id;

  useEffect(() => { api.config(tenant).then(setCfg).catch(() => setError("The support service is not reachable right now.")); }, [tenant]);

  // Restore the saved conversation for this identity (guest or student); switching identity starts clean.
  useEffect(() => {
    if (!ready) return;
    setMsgs([]); setStatus(null); setNotice(null); setError(null); setTicket(null);
    let saved: string | null = null;
    try { saved = sessionStorage.getItem(storeKey); } catch { /* ignore */ }
    if (!saved || (!student && !tenantArg)) return;
    api.history(saved, { token, tenant: tenantArg })
      .then((h) => {
        setTicket(h.ticket_id);
        setMsgs(h.messages.map((m) => ({ id: nextId++, ...m })));
        setStatus(h.awaiting === "staff" ? "awaiting_staff" : h.awaiting === "student" ? "awaiting_confirmation" : null);
      })
      .catch(() => { try { sessionStorage.removeItem(storeKey); } catch { /* ignore */ } });
  }, [ready, storeKey, token, tenantArg, student]);

  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end" }); }, [msgs, status]);

  // While staff decide, poll the saved conversation so the student sees the outcome without refreshing.
  useEffect(() => {
    if (status !== "awaiting_staff" || !ticket) return;
    const t = setInterval(() => {
      api.history(ticket, { token, tenant: tenantArg }).then((h) => {
        if (h.awaiting !== "staff") {
          setMsgs(h.messages.map((m) => ({ id: nextId++, ...m })));
          setStatus(h.awaiting === "student" ? "awaiting_confirmation" : "done");
          setNotice("Staff have responded to your request.");
        }
      }).catch(() => { /* transient; try again on the next tick */ });
    }, pollMs);
    return () => clearInterval(t);
  }, [status, ticket, token, tenantArg, pollMs]);

  const send = useCallback(async (e?: FormEvent) => {
    e?.preventDefault();
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true); setError(null); setNotice(null);
    setMsgs((m) => [...m, { id: nextId++, role: "user", content: q }]);
    setText("");
    try {
      const r = await api.chat(q, { ticketId: ticket, token, tenant: tenantArg });
      setTicket(r.ticket_id);
      try { sessionStorage.setItem(storeKey, r.ticket_id); } catch { /* ignore */ }
      setStatus(r.status);
      setMsgs((m) => [...m, { id: nextId++, role: "assistant", content: r.reply, citations: r.citations, status: r.status }]);
    } catch (err) {
      setText(q); // give the text back so nothing typed is lost
      setMsgs((m) => m.slice(0, -1));
      if (err instanceof ApiError && err.status === 404 && ticket) { // saved conversation no longer valid: start fresh
        setTicket(null); try { sessionStorage.removeItem(storeKey); } catch { /* ignore */ }
        setError("That conversation could not be found. Send your message again to start a new one.");
      } else setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally { setBusy(false); }
  }, [text, busy, ticket, token, tenantArg, storeKey]);

  function reset() {
    try { sessionStorage.removeItem(storeKey); } catch { /* ignore */ }
    setTicket(null); setMsgs([]); setStatus(null); setNotice(null); setError(null);
  }

  if (session && !student) return <div className="card narrow"><p>You are signed in as {session.role}. Use the staff console to handle requests; the student chat is for students and visitors.</p></div>;
  const locked = status === "awaiting_staff";

  return (
    <section className="chat card" aria-label="Support chat">
      <div className="chat-head">
        <div>
          <h1>{cfg ? cfg.tenant_name : "Support"}</h1>
          <p className="muted">{student ? `Signed in as ${student.username}: I can answer about your batches, attendance and scores.` : "Ask about courses, fees and policies. Sign in for questions about your own records."}</p>
          {cfg?.is_sample && <p className="pill warn">Sample data: not a real institute</p>}
        </div>
        <div className="actions">
          {!student && <button className="btn small" onClick={() => setShowLogin((v) => !v)}>{showLogin ? "Hide sign in" : "Student sign in"}</button>}
          {msgs.length > 0 && <button className="btn small" onClick={reset}>New conversation</button>}
        </div>
      </div>
      {!student && showLogin && <LoginForm heading="Student sign in" onDone={() => setShowLogin(false)} />}

      <div className="log" role="log" aria-live="polite" aria-label="Conversation">
        {msgs.length === 0 && <p className="muted empty">Hi! How can I help today?</p>}
        {msgs.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <span className="sr-only">{m.role === "user" ? "You" : "Assistant"}: </span>
            <p>{m.content}</p>
            {m.citations && m.citations.length > 0 && (
              <ul className="cites" aria-label="Sources">{m.citations.map((c) => <li key={c.id}>{c.heading || c.id}</li>)}</ul>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant typing" aria-label="Assistant is typing">…</div>}
        <div ref={endRef} />
      </div>

      {status && BANNERS[status] && <p className={`banner ${status}`} role="status">{BANNERS[status]}</p>}
      {notice && <p className="banner done" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <form className="composer" onSubmit={send}>
        <label className="sr-only" htmlFor="chat-text">Your message</label>
        <textarea id="chat-text" value={text} maxLength={MAX_LEN} rows={2} placeholder={locked ? "Waiting for staff…" : "Type your question"}
          disabled={locked} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
        <button className="btn primary" disabled={busy || locked || !text.trim()}>Send</button>
      </form>
      <p className="muted tiny">{text.length}/{MAX_LEN}</p>
    </section>
  );
}
