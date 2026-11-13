"use client";
import { useEffect, useRef, useState } from "react";
import { Splitter, useSplitWidth } from "./Splitter";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useUrlParam } from "@/lib/nav";
import type { KwRequest } from "@/lib/types";
import { Badge, msg, when } from "./ui";
import { CompareView } from "./Compare";
import { RevisionPanel, SourceChip } from "./Revision";


/** The signed-in person's own requests (drafts included). Drafts can be resumed from the document page. */
export function MyRequests({ onResume, onResumeAi }: { onResume: (docId: string) => void; onResumeAi?: (requestId: string) => void }) {
  const { withToken } = useAuth();
  const [rows, setRows] = useState<KwRequest[] | null>(null);
  const [sel, setSel] = useState<KwRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selId, setSelId] = useUrlParam("cr", null);                     // the selected request is in the URL: Back closes the detail (phones) or returns to the previous one
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLElement>(null);
  const split = useSplitWidth(box, "sg.split.my-requests", 360);       // refinement 2.1
  const [info, setInfo] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { withToken((t) => api.kwMine(t)).then((r) => setRows(r.items)).catch((e) => setError(msg(e))); }, [withToken, tick]);
  const cancel = (id: string) => {
    setBusy(true); setError(null);
    withToken((t) => api.kwCancel(t, id)).then((r) => { setSel(r); setInfo("Deletion request cancelled. The document stays published."); setTick((n) => n + 1); })
      .catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  useEffect(() => {
    setInfo(null);
    if (!selId) { setSel(null); return; }
    let live = true;
    withToken((t) => api.kwMyRequest(t, selId)).then((r) => live && setSel(r)).catch((e) => live && setError(msg(e)));
    return () => { live = false; };
  }, [selId, withToken]);
  return (
    <section ref={box} aria-label="My change requests" className={`kw-md resizable${selId ? " has-sel" : ""}`}>
      <div className="kw-md-list">
        <h1 className="kw-title">My change requests</h1>
        {error && <p role="alert" className="kw-error">{error}</p>}
        {rows === null && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}
        {rows?.length === 0 && <p className="kw-muted">You have not made any changes yet. Open a document and choose “Make changes”.</p>}
        <ul className="kw-list">
          {rows?.map((r) => (
            <li key={r.request_id}><button className={`kw-row${selId === r.request_id ? " sel" : ""}`} aria-current={selId === r.request_id ? "true" : undefined}
              onClick={() => setSelId(r.request_id)} aria-label={`Open request ${r.description ?? r.request_id}`}>
              <Badge s={r.status} />{r.analysis?.kind === "deletion" && <span className="kw-chip st-failed">Deletion</span>} <b>{r.description ?? "Untitled draft"}</b>
              <span className="kw-sub">{r.documents.map((d) => d.title).join(", ") || "no documents"} · updated {when(r.updated_at)}</span></button></li>))}
        </ul>
      </div>
      <Splitter container={box} split={split} label="Resize the request list" controls="kw-my-requests-detail" />
      <div className="kw-md-detail" id="kw-my-requests-detail" aria-live="polite">
        {!selId && <p className="kw-muted kw-md-hint">Select a request to see its details here.</p>}
        {selId && <p className="kw-md-back"><button className="kw-btn small" onClick={() => setSelId(null)}><span aria-hidden="true">←</span> Back to the list</button></p>}
        {sel && sel.request_id === selId && (
          <div className="kw-detail kw-enter" key={sel.request_id}>
            <h2>{sel.description ?? "Draft"} <Badge s={sel.status} /></h2>
            {(sel.return_note || sel.review_note) && <p className="kw-banner warn">Reviewer note: {sel.return_note ?? sel.review_note}</p>}
            {info && <p role="status" className="kw-banner ok">{info}</p>}
            {sel.analysis?.kind === "deletion" && (
              <div className="kw-banner warn"><p><b>Deletion request</b> for {sel.documents.map((d) => `“${d.title}”`).join(", ")}. Reason: {sel.analysis.deletion_reason}</p>
                <p className="kw-muted tiny">{sel.status === "published" ? "Approved: the document was archived (its history is kept)." : sel.status === "rejected" ? "Rejected: the document stays published." : sel.status === "cancelled" ? "Cancelled: the document stays published." : "Waiting for an administrator. The document stays published until the request is approved."}</p>
                {["submitted", "under_review"].includes(sel.status) && <button className="kw-btn" disabled={busy} onClick={() => cancel(sel.request_id)}>Cancel deletion request</button>}</div>)}
            {sel.status === "needs_revision" && sel.analysis?.kind !== "deletion" && (
              <RevisionPanel req={sel} onChanged={(r, note) => { setSel(r); if (note) setInfo(note); setTick((n) => n + 1); }} onAi={onResumeAi} />)}
            {sel.source === "ai_assisted" && ["draft", "analysing", "awaiting_clarification", "awaiting_decisions"].includes(sel.status) && onResumeAi && <button className="kw-btn primary" onClick={() => onResumeAi(sel.request_id)}>Continue with the AI analysis</button>}
            {sel.failure && <p className="kw-banner warn">{sel.failure.message}</p>}
            {sel.status === "draft" && sel.source !== "ai_assisted" && sel.documents[0] && <button className="kw-btn primary" onClick={() => onResume(sel.documents[0].doc_id)}>Continue editing</button>}
            {sel.analysis?.kind !== "deletion" && sel.items?.filter((it) => it.proposed_checksum === undefined || it.proposed_checksum !== it.base_checksum || it.decision === "manual").map((it) => (
              <div key={it.doc_id}><h3>{it.doc_title} <SourceChip it={it} /></h3>{it.base_stale && sel.status === "draft" && <p className="kw-banner warn">The published text changed after you started. Discard this draft and make the change again.</p>}
                {it.diff && <CompareView it={it} />}</div>))}
          </div>
        )}
      </div>
    </section>
  );
}
