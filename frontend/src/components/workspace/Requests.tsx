"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwRequest } from "@/lib/types";
import { DiffLegend, DiffView } from "./DiffView";
import { Badge, msg, when } from "./ui";


/** The signed-in person's own requests (drafts included). Drafts can be resumed from the document page. */
export function MyRequests({ onResume, onResumeAi }: { onResume: (docId: string) => void; onResumeAi?: (requestId: string) => void }) {
  const { withToken } = useAuth();
  const [rows, setRows] = useState<KwRequest[] | null>(null);
  const [sel, setSel] = useState<KwRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { withToken((t) => api.kwMine(t)).then((r) => setRows(r.items)).catch((e) => setError(msg(e))); }, [withToken]);
  const open = (id: string) => withToken((t) => api.kwMyRequest(t, id)).then(setSel).catch((e) => setError(msg(e)));
  return (
    <section aria-label="My change requests">
      <h1 className="kw-title">My change requests</h1>
      {error && <p role="alert" className="kw-error">{error}</p>}
      {rows === null && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}
      {rows?.length === 0 && <p className="kw-muted">You have not made any changes yet. Open a document and choose “Make changes”.</p>}
      <ul className="kw-list">
        {rows?.map((r) => (
          <li key={r.request_id}><button className="kw-row" onClick={() => open(r.request_id)} aria-label={`Open request ${r.description ?? r.request_id}`}>
            <Badge s={r.status} /> <b>{r.description ?? "Untitled draft"}</b>
            <span className="kw-sub">{r.documents.map((d) => d.title).join(", ") || "no documents"} · updated {when(r.updated_at)}</span></button></li>))}
      </ul>
      {sel && (
        <div className="kw-detail">
          <h2>{sel.description ?? "Draft"} <Badge s={sel.status} /></h2>
          {(sel.return_note || sel.review_note) && <p className="kw-banner warn">Reviewer note: {sel.return_note ?? sel.review_note}</p>}
          {sel.source === "ai_assisted" && ["draft", "analysing", "awaiting_clarification", "awaiting_decisions", "needs_revision"].includes(sel.status) && onResumeAi && <button className="kw-btn primary" onClick={() => onResumeAi(sel.request_id)}>Continue with the AI analysis</button>}
          {sel.failure && <p className="kw-banner warn">{sel.failure.message}</p>}
          {sel.status === "draft" && sel.source !== "ai_assisted" && sel.documents[0] && <button className="kw-btn primary" onClick={() => onResume(sel.documents[0].doc_id)}>Continue editing</button>}
          {sel.items?.map((it) => (
            <div key={it.doc_id}><h3>{it.doc_title}</h3>{it.base_stale && sel.status === "draft" && <p className="kw-banner warn">The published text changed after you started. Discard this draft and make the change again.</p>}
              {it.diff && <><DiffLegend /><DiffView segs={it.diff.body} label={`Changes to ${it.doc_title}`} /></>}</div>))}
        </div>
      )}
    </section>
  );
}
