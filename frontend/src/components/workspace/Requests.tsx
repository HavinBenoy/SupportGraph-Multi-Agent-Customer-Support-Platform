"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwRequest, KwStatus } from "@/lib/types";
import { DiffLegend, DiffView, Stats } from "./DiffView";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const LABEL: Record<KwStatus, string> = { draft: "Draft", submitted: "Awaiting review", approved: "Approved", rejected: "Rejected", applied: "Applied", cancelled: "Cancelled" };
const Badge = ({ s }: { s: KwStatus }) => <span className={`kw-chip st-${s}`}>{LABEL[s]}</span>;

/** The signed-in person's own requests (drafts included). Drafts can be resumed from the document page. */
export function MyRequests({ onResume }: { onResume: (docId: string) => void }) {
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
          {sel.review_note && <p className="kw-banner">Reviewer note: {sel.review_note}</p>}
          {sel.status === "draft" && sel.documents[0] && <button className="kw-btn primary" onClick={() => onResume(sel.documents[0].doc_id)}>Continue editing</button>}
          {sel.items?.map((it) => (
            <div key={it.doc_id}><h3>{it.doc_title}</h3>{it.base_stale && sel.status === "draft" && <p className="kw-banner warn">The published text changed after you started. Discard this draft and make the change again.</p>}
              {it.diff && <><DiffLegend /><DiffView segs={it.diff.body} label={`Changes to ${it.doc_title}`} /></>}</div>))}
        </div>
      )}
    </section>
  );
}

/** Administrator queue: submitted requests with the full structured diff, then approve / reject / apply. Applying only creates ordinary draft versions;
 *  publishing them is the existing process-and-publish step. */
export function ChangeRequests({ onOpenPublishing }: { onOpenPublishing?: () => void }) {
  const { withToken } = useAuth();
  const [status, setStatus] = useState<string>("submitted");
  const [rows, setRows] = useState<KwRequest[] | null>(null);
  const [sel, setSel] = useState<KwRequest | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => { withToken((t) => api.kwRequests(t, status || undefined)).then((r) => { setRows(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [withToken, status]);
  useEffect(() => { setRows(null); load(); }, [load]);
  const open = (id: string) => withToken((t) => api.kwRequest(t, id)).then((r) => { setSel(r); setNote(""); setError(null); }).catch((e) => setError(msg(e)));
  const act = (fn: (t: string) => Promise<KwRequest>, ok: string) => {
    setBusy(true); setError(null);
    withToken(fn).then((r) => { setSel(r); setDone(ok); load(); }).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };

  return (
    <div className="kw">
      <div className="kw-requests">
        <section aria-label="Change requests">
          <h1 className="kw-title">Change requests</h1>
          <label className="kw-inline">Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setSel(null); }}>
              {["submitted", "approved", "applied", "rejected", "cancelled", ""].map((s) => <option key={s} value={s}>{s ? LABEL[s as KwStatus] : "All"}</option>)}
            </select>
          </label>
          {error && <p role="alert" className="kw-error">{error}</p>}
          {rows === null && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}
          {rows?.length === 0 && <p className="kw-muted">No requests here.</p>}
          <ul className="kw-list">
            {rows?.map((r) => (
              <li key={r.request_id}><button className={`kw-row${sel?.request_id === r.request_id ? " sel" : ""}`} onClick={() => open(r.request_id)} aria-label={`Review ${r.description ?? r.request_id}`}>
                <Badge s={r.status} /> <b>{r.description}</b>
                <span className="kw-sub">{r.created_by} · {r.document_count} document(s) · <Stats added={r.added_chars} deleted={r.deleted_chars} /> · {when(r.submitted_at)}</span></button></li>))}
          </ul>
        </section>
        {sel && (
          <section className="kw-detail" aria-label="Request detail">
            <h2>{sel.description} <Badge s={sel.status} /></h2>
            <p className="kw-muted">Requested by {sel.created_by} on {when(sel.submitted_at)}{sel.reviewed_by ? ` · reviewed by ${sel.reviewed_by}` : ""}</p>
            {done && <p role="status" className="kw-banner ok">{done}{sel.status === "applied" && onOpenPublishing && <> <button className="kw-btn small" onClick={onOpenPublishing}>Open Publishing</button></>}</p>}
            {sel.review_note && <p className="kw-banner">Note: {sel.review_note}</p>}
            {sel.items?.map((it) => (
              <div key={it.doc_id} className="kw-item">
                <h3>{it.doc_title} <span className="kw-muted tiny">based on v{it.base_version}</span> <Stats added={it.stats.added_chars} deleted={it.stats.deleted_chars} /></h3>
                {it.base_stale && ["submitted", "approved"].includes(sel.status) && <p className="kw-banner warn">The live text has changed since this was written, so it can no longer be applied. Reject it and ask for the change again.</p>}
                <DiffLegend />
                {it.diff && <DiffView segs={it.diff.body} label={`Changes to ${it.doc_title}`} />}
                {it.result_version && <p className="kw-muted tiny">Created draft v{it.result_version}.</p>}
              </div>))}
            {sel.status === "submitted" && (
              <div className="kw-ask">
                <label>Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
                <div className="kw-actions">
                  <button className="kw-btn primary" disabled={busy} onClick={() => act((t) => api.kwReview(t, sel.request_id, "approve", note.trim() || undefined), "Approved. Apply it to create the draft versions.")}>Approve</button>
                  <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReview(t, sel.request_id, "reject", note.trim() || undefined), "Rejected. Nothing was changed.")}>Reject</button>
                </div>
              </div>)}
            {sel.status === "approved" && (
              <div className="kw-actions"><button className="kw-btn primary" disabled={busy} onClick={() => act((t) => api.kwApply(t, sel.request_id), "Applied: draft versions were created. They are not live until you process and publish them.")}>Apply — create draft versions</button></div>)}
          </section>)}
      </div>
    </div>
  );
}
