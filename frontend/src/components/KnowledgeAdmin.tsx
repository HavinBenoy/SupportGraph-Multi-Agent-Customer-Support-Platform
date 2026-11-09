"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KbAuditRow, KbDoc, KbFeedbackRow, KbMeta, KbVersionRow } from "@/lib/types";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const LABEL: Record<string, string> = { draft: "Draft", processing: "Processing", ready_for_review: "Ready for review", published: "Published", failed: "Failed", superseded: "Superseded", archived: "Archived" };

/** Knowledge store (M6), administrators only. A minimal console over the M6 API: the server enforces every rule (processing before publishing, approved proposals,
 *  versioned edits); this screen shows state and sends requests. Nothing here publishes by itself: every publish is a button an administrator presses. */
export function KnowledgeAdmin() {
  const { withToken } = useAuth();
  const [docs, setDocs] = useState<KbDoc[] | null>(null);
  const [meta, setMeta] = useState<KbMeta | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    withToken((t) => Promise.all([api.kbDocs(t, q.trim() || undefined), api.kbMeta(t)])).then(([l, m]) => { setDocs(l.items); setMeta(m); setError(null); }).catch((e) => setError(msg(e)));
  }, [withToken, q]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="console">
      <h1>Knowledge</h1>
      <p className="muted">Published knowledge is what the assistant answers from. Drafts, processed-but-unpublished versions and archived documents are never used. Every change is saved as a new version and recorded in the audit trail.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="banner done">{notice}</p>}
      <section className="card" aria-label="Documents">
        <div className="bar"><h2>Documents</h2>
          <input aria-label="Search documents" placeholder="Search title, text or description" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") load(); }} />
          <button className="btn small" onClick={load}>Search</button></div>
        {!docs && !error && <p className="muted" aria-busy="true">Loading…</p>}
        {docs?.length === 0 && <p className="muted">No documents yet.</p>}
        <ul className="plain">{docs?.map((d) => (
          <li key={d.doc_id}><button className={`row${sel === d.doc_id ? " sel" : ""}`} onClick={() => setSel(d.doc_id)} aria-label={`Open ${d.title}`}>
            <strong>{d.title}</strong> <span className="muted tiny">{d.category} · {LABEL[d.status] ?? d.status} · latest v{d.latest_version}{d.published_version ? ` · live v${d.published_version}` : ""}</span></button></li>))}</ul>
        {meta && meta.unmanaged_disk_documents.length > 0 && <p className="muted tiny" data-testid="unmanaged">{meta.unmanaged_disk_documents.length} article(s) come from files on disk and are not managed here yet.</p>}
      </section>
      {meta && <NewDocument meta={meta} onDone={(id) => { setNotice("Draft created. Process it, then publish it when it is ready."); setSel(id); load(); }} />}
      {sel && <Detail key={sel} docId={sel} onChanged={(m) => { if (m) setNotice(m); load(); }} />}
      <Proposals onChanged={(m) => { if (m) setNotice(m); load(); }} categories={meta?.categories ?? []} />
    </div>
  );
}

function NewDocument({ meta, onDone }: { meta: KbMeta; onDone: (id: string) => void }) {
  const { withToken } = useAuth();
  const [f, setF] = useState({ doc_id: "", title: "", category: meta.categories[0] ?? "faq", content: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    withToken((t) => api.kbCreate(t, f)).then((r) => { setF({ ...f, doc_id: "", title: "", content: "" }); onDone(r.document.doc_id); }).catch((er) => setError(msg(er))).finally(() => setBusy(false));
  };
  return (
    <section className="card" aria-label="New document">
      <h2>New document</h2>
      {error && <p role="alert" className="error">{error}</p>}
      <form className="form" onSubmit={submit}>
        <label>Id (lower-case letters, digits, dashes)<input value={f.doc_id} onChange={(e) => setF({ ...f, doc_id: e.target.value })} required maxLength={128} /></label>
        <label>Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required maxLength={300} /></label>
        <label>Category<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{meta.categories.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label>Text (Markdown, up to {Math.round(meta.limits.max_bytes / 1000)} KB)<textarea rows={6} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} required /></label>
        <div className="actions"><button className="btn primary" disabled={busy}>Create draft</button></div>
      </form>
    </section>
  );
}

function Detail({ docId, onChanged }: { docId: string; onChanged: (m?: string) => void }) {
  const { withToken } = useAuth();
  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [vers, setVers] = useState<KbVersionRow[]>([]);
  const [audit, setAudit] = useState<KbAuditRow[]>([]);
  const [shown, setShown] = useState<KbVersionRow | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    withToken((t) => Promise.all([api.kbDoc(t, docId), api.kbVersions(t, docId), api.kbAudit(t, docId)])).then(([d, v, a]) => { setDoc(d); setVers(v.items); setAudit(a.items); }).catch((e) => setError(msg(e)));
  }, [withToken, docId]);
  useEffect(() => { load(); }, [load]);

  const run = (fn: (t: string) => Promise<unknown>, done: string) => {
    setBusy(true); setError(null);
    withToken(fn).then(() => { load(); onChanged(done); }).catch((e) => { setError(msg(e)); load(); }).finally(() => setBusy(false));
  };
  const view = (n: number) => withToken((t) => api.kbVersion(t, docId, n)).then(setShown).catch((e) => setError(msg(e)));
  const addVersion = (e: FormEvent) => {
    e.preventDefault();
    if (!doc) return;
    run((t) => api.kbAddVersion(t, docId, text, note, doc.row_version).then(() => { setText(""); setNote(""); }), "New version saved as a draft.");
  };

  return (
    <section className="card" aria-label={`Document ${docId}`}>
      {error && <p role="alert" className="error">{error}</p>}
      {!doc && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {doc && (<>
        <h2>{doc.title}</h2>
        <p className="muted tiny">{doc.doc_id} · {doc.category} · {LABEL[doc.status]} · {doc.published_version ? `live version ${doc.published_version}` : "nothing live"}</p>
        <div className="actions">
          {doc.status === "published" && <button className="btn" disabled={busy} onClick={() => run((t) => api.kbDocAction(t, docId, "unpublish"), "Unpublished: the assistant no longer uses this document.")}>Unpublish</button>}
          {doc.status !== "archived" && <button className="btn danger" disabled={busy} onClick={() => run((t) => api.kbDocAction(t, docId, "archive"), "Archived: history is kept, retrieval stopped.")}>Archive</button>}
          {doc.status === "archived" && <button className="btn" disabled={busy} onClick={() => run((t) => api.kbDocAction(t, docId, "unarchive"), "Restored to draft. Nothing is live until you publish a version.")}>Unarchive</button>}
        </div>
        <h3>Versions</h3>
        <ul className="plain">{vers.map((v) => (
          <li key={v.version} className="tiny" data-testid={`version-${v.version}`}>
            <strong>v{v.version}</strong> · <span>{LABEL[v.lifecycle]}</span> · {v.created_by} · {when(v.created_at)} · {v.change_note}
            {v.restored_from ? ` (restored from v${v.restored_from})` : ""}{v.proposal ? ` · from feedback proposal (${v.proposal.status})` : ""}
            {v.processing_error ? <span className="error"> · {v.processing_error}</span> : null}
            <span className="actions">
              <button className="btn small" onClick={() => void view(v.version)}>View v{v.version}</button>
              {(v.lifecycle === "draft" || v.lifecycle === "failed" || v.lifecycle === "ready_for_review") && doc.status !== "archived" &&
                <button className="btn small" disabled={busy} onClick={() => run((t) => api.kbProcess(t, docId, v.version).then((r) => { if (r.status === "failed") throw new ApiError(422, `Processing failed: ${r.error ?? "unknown error"}`); }), `Version ${v.version} processed.`)}>{v.lifecycle === "ready_for_review" ? "Re-process" : "Process"} v{v.version}</button>}
              {v.lifecycle === "ready_for_review" && <button className="btn small primary" disabled={busy} onClick={() => run((t) => api.kbPublish(t, docId, v.version), `Version ${v.version} is now live.`)}>Publish v{v.version}</button>}
              {v.lifecycle !== "published" && <button className="btn small" disabled={busy} onClick={() => run((t) => api.kbRestore(t, docId, v.version), `Version ${v.version} restored as a new draft version.`)}>Restore v{v.version}</button>}
            </span>
          </li>))}</ul>
        {shown && <div className="card" aria-label={`Version ${shown.version} text`}><p className="muted tiny">Version {shown.version} · checksum {shown.checksum.slice(0, 12)}</p><pre className="tiny" style={{ whiteSpace: "pre-wrap" }}>{shown.content}</pre></div>}
        {doc.status !== "archived" && (
          <form className="form" onSubmit={addVersion} aria-label="New version">
            <h3>New version</h3>
            <label>Text<textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} required /></label>
            <label>What changed<input value={note} onChange={(e) => setNote(e.target.value)} required maxLength={500} /></label>
            <div className="actions"><button className="btn primary" disabled={busy}>Save as draft version</button></div>
          </form>)}
        <h3>Recent audit events</h3>
        <ul className="notes">{audit.map((a) => <li key={a.id} className="tiny">{when(a.at)} · {a.actor} · {a.action.replace(/_/g, " ")}{a.version ? ` · v${a.version}` : ""}</li>)}</ul>
      </>)}
    </section>
  );
}

function Proposals({ onChanged, categories }: { onChanged: (m?: string) => void; categories: string[] }) {
  const { withToken } = useAuth();
  const [items, setItems] = useState<KbFeedbackRow[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [f, setF] = useState({ doc_id: "", title: "", category: categories[0] ?? "faq", content: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { withToken((t) => api.kbFeedback(t)).then((r) => { setItems(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [withToken]);
  useEffect(() => { load(); }, [load]);
  const go = (fn: (t: string) => Promise<unknown>, done: string) => {
    setBusy(true); setError(null);
    withToken(fn).then(() => { setOpen(null); load(); onChanged(done); }).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  return (
    <section className="card" aria-label="Feedback proposals">
      <h2>Knowledge from approved feedback</h2>
      <p className="muted tiny">Approved feedback is permission to start a change, not a change. You write the new text, another review step approves it, and you publish it yourself.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {items?.length === 0 && <p className="muted">No approved feedback yet.</p>}
      <ul className="plain">{items?.map((i) => (
        <li key={i.call_id} className="card" data-testid={`fb-${i.call_id}`}>
          <p className="tiny"><strong>{i.category.replace(/_/g, " ")}</strong> · written by {i.written_by}</p>
          <p>{i.what_was_wrong}</p>{i.suggested_correction && <p className="muted tiny">Suggested: {i.suggested_correction}</p>}
          {i.proposal ? (
            <p className="tiny">Proposal for <strong>{i.proposal.doc_id}</strong> v{i.proposal.version}: {i.proposal.status}{i.proposal.reviewed_by ? ` (reviewed by ${i.proposal.reviewed_by})` : ""}
              {i.proposal.status === "proposed" && <span className="actions">
                <button className="btn small primary" disabled={busy} onClick={() => go((t) => api.kbReview(t, i.call_id, "approve"), "Proposal approved. It still has to be processed and published.")}>Approve proposal</button>
                <button className="btn small" disabled={busy} onClick={() => go((t) => api.kbReview(t, i.call_id, "reject"), "Proposal rejected.")}>Reject proposal</button></span>}</p>
          ) : open === i.call_id ? (
            <form className="form" aria-label="Create proposal" onSubmit={(e) => { e.preventDefault(); go((t) => api.kbPropose(t, i.call_id, f.content, { new_document: { doc_id: f.doc_id, title: f.title, category: f.category } }), "Draft created from feedback. Process it, get it approved, then publish."); }}>
              <label>New document id<input value={f.doc_id} onChange={(e) => setF({ ...f, doc_id: e.target.value })} required /></label>
              <label>Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required /></label>
              <label>Category<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{categories.map((c) => <option key={c}>{c}</option>)}</select></label>
              <label>Text<textarea rows={4} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} required /></label>
              <div className="actions"><button className="btn primary" disabled={busy}>Create draft</button><button type="button" className="btn" onClick={() => setOpen(null)}>Cancel</button></div>
            </form>
          ) : <button className="btn small" onClick={() => setOpen(i.call_id)}>Start a knowledge change</button>}
        </li>))}</ul>
    </section>
  );
}
