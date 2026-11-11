"use client";
import Link from "next/link";
import { useEffect, useRef } from "react";
import { homeFor, useAuth } from "@/lib/auth";

const FEATURES = [
  { ico: "✦", t: "Answers from your own knowledge", d: "Students get answers grounded in the institute's published documents, with the source shown. Drafts and staged edits are never used." },
  { ico: "◐", t: "Voice line with human takeover", d: "Callers speak naturally; staff listen live and take over a call in one click, with the full transcript and status at hand." },
  { ico: "⇄", t: "Escalations that reach a person", d: "Anything the assistant should not decide goes to the staff console with a masked brief, priority and an owner." },
  { ico: "≡", t: "AI-assisted knowledge updates", d: "Describe a change in plain words; the assistant finds every affected document and drafts the edit. Staff and an administrator approve it." },
  { ico: "◇", t: "Versioned and auditable", d: "Every published document is an immutable version. Publication is atomic, stale edits are refused, and every action is recorded." },
  { ico: "◎", t: "Runs on your own hardware", d: "Local models through Ollama on a CPU-only machine: no paid service, no student data leaving the institute." },
];
const FLOW = [
  { b: "Ask", s: "A student writes or calls." },
  { b: "Answer", s: "Agents retrieve, reason and reply with sources." },
  { b: "Escalate", s: "Staff step in when it matters." },
  { b: "Improve", s: "Knowledge changes are drafted by AI and approved by people." },
];

/** The public landing page. Lightweight motion only (CSS transitions; a single IntersectionObserver for reveals); everything is visible immediately when the user
 *  prefers reduced motion or the observer is unavailable. */
export function Landing() {
  const { session } = useAuth();
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const els = Array.from(root.current?.querySelectorAll(".reveal") ?? []);
    if (typeof IntersectionObserver === "undefined" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { els.forEach((e) => e.classList.add("in")); return; }
    const io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }), { threshold: 0.12 });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, []);
  const home = session ? homeFor(session.role) : "/login";
  return (
    <div className="landing" ref={root}>
      <section className="hero" aria-labelledby="hero-title">
        <span className="badge-line"><span className="dot" aria-hidden="true" />Multi-agent support for training institutes</span>
        <h1 id="hero-title"><span className="line">Support that</span> <span className="line"><em>answers</em>, escalates</span> <span className="line">and keeps learning.</span></h1>
        <p className="lead">SupportGraph handles student questions by chat and voice, hands the hard ones to your staff, and keeps the institute&apos;s knowledge correct — with people approving every change.</p>
        <div className="actions">
          <Link className="btn primary lg" href="/chat">Ask the assistant</Link>
          <Link className="btn lg" href={home}>{session ? "Open your workspace" : "Staff sign in"}</Link>
        </div>
      </section>

      <section className="section reveal" aria-labelledby="how">
        <p className="eyebrow">How it works</p>
        <h2 id="how">From a question to better knowledge</h2>
        <p>Specialised agents route each conversation, retrieve the right documents, answer with citations, and know when to call a person.</p>
        <ol className="flow">{FLOW.map((f) => <li key={f.b}><b>{f.b}</b><span>{f.s}</span></li>)}</ol>
      </section>

      <section className="section reveal" aria-labelledby="features">
        <p className="eyebrow">Capabilities</p>
        <h2 id="features">Everything a support desk needs</h2>
        <div className="features">{FEATURES.map((f) => <article key={f.t} className="feature"><div className="ico" aria-hidden="true">{f.ico}</div><h3>{f.t}</h3><p>{f.d}</p></article>)}</div>
      </section>

      <section className="section reveal" aria-labelledby="cta">
        <div className="cta">
          <h2 id="cta">Try it now</h2>
          <p className="muted">Ask about courses, fees, batches or placements — or sign in to the staff console.</p>
          <div className="actions" style={{ justifyContent: "center" }}>
            <Link className="btn primary" href="/chat">Start a chat</Link><Link className="btn" href="/voice">Call the voice line</Link>
          </div>
        </div>
      </section>
      <p className="foot">SupportGraph · demo with synthetic data · nothing published without human approval</p>
    </div>
  );
}
