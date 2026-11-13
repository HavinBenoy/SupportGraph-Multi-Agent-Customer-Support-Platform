"use client";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { DeletionRequest } from "./workspace/DeletionRequest";
import { TextCompare } from "./workspace/Compare";
import type { KbAuditRow, KbCompare, KbDoc, KbFeedbackRow, KbMeta, KbProposalRow, KbPurgePreview, KbVersionRow } from "@/lib/types";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const LABEL: Record<string, string> = { draft: "Draft", processing: "Processing", ready_for_review: "Ready for review", published: "Published", failed: "Failed", superseded: "Superseded", archived: "Archived" };

const ACTION_WORDS = (a: string) => a.replace(/_/g, " ");
const PAGE_SIZE = 25;
type AdminTab = "documents" | "review" | "audit" | "import";

/** Document administration (M6 store, completed in M7 — docs/M6.md section 13): the documents table with filters and pagination, each document's versions,
 *  version-to-version comparison, processing and publication, legal hold and permanent deletion; the review queue (approved feedback and its proposals); the
 *  tenant's audit browser; and the import wizard for articles that still come from disk. The server enforces every rule (processing before publishing, approved
 *  proposals, versioned edits, row_version checks); this screen shows state and sends requests. Nothing here publishes or deletes by itself. */
export function KnowledgeAdmin({ openDoc = null, onChanged, startCreating = false }: { openDoc?: string | null; onChanged?: () => void; startCreating?: boolean } = {}) {
  const { withToken } = useAuth();
  const [tab, setTab] = useState<AdminTab>("documents");
  const [docs, setDocs] = useState<KbDoc[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState<KbMeta | null>(null);
  const [sel, setSel] = useState<string | null>(openDoc);
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState({ q: "", status: "", category: "" });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(startCreating);
  const detailRef = useRef<HTMLDivElement>(null);
  // 2.4: selecting a document brings its details into view (no full-page jump; smooth only if the user allows motion) and moves focus there.
  useEffect(() => {
    if (!sel || !detailRef.current) return;
    const el = detailRef.current;
    const reduce = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const r = el.getBoundingClientRect?.();
    if (r && typeof el.scrollIntoView === "function" && (r.top < 0 || r.top > (window.innerHeight || 800) - 80)) el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    el.focus?.({ preventScroll: true });
  }, [sel]);

  const load = useCallback(() => {
    setError(null); setLoading(true);
    withToken((t) => Promise.all([api.kbDocs(t, { q: applied.q || undefined, status: applied.status || undefined, category: applied.category || undefined, page, pageSize: PAGE_SIZE }), api.kbMeta(t)]))
      .then(([l, m]) => { setDocs(l.items); setTotal(l.total); setMeta(m); setError(null); }).catch((e) => setError(msg(e))).finally(() => setLoading(false));
  }, [withToken, applied, page]);
  useEffect(() => { load(); }, [load]);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filter = (k: "status" | "category", v: string) => { setPage(1); setApplied((a) => ({ ...a, [k]: v })); };
  const changed = (m?: string) => { if (m) setNotice(m); load(); onChanged?.(); };

  return (
    <div className="console">
      <h1 className="kw-title">Drafts &amp; document administration</h1>
      <p className="muted">Published knowledge is what the assistant answers from. Drafts, processed-but-unpublished versions and archived documents are never used. Every change is saved as a new version and recorded in the audit trail.</p>
      <div role="tablist" aria-label="Document administration" className="tabs sub">
        {([["documents", "Documents"], ["review", "Review queue"], ["audit", "Audit log"], ["import", "Import from disk"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => { setTab(k); setNotice(null); }}>{label}
            {k === "import" && meta && meta.unmanaged_disk_documents.length > 0 ? ` (${meta.unmanaged_disk_documents.length})` : ""}</button>))}
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status" className="banner done">{notice}</p>}
      {tab === "documents" && (<div className="kb-docs">
        {creating && meta && <NewDocument meta={meta} onCancel={() => setCreating(false)}
          onDone={(id) => { setCreating(false); setNotice("Draft created. Process it, then publish it when it is ready."); setSel(id); load(); onChanged?.(); }} />}
        <div className={`kb-split${sel ? " has-sel" : ""}`}>
        <section className="card kb-list" aria-label="Documents">
          <div className="bar kb-head"><h2>Documents</h2>
            {meta && !creating && <button className="btn primary" onClick={() => setCreating(true)} aria-haspopup="dialog">New document</button>}</div>
          <div className="bar kb-filters">
            <input aria-label="Search documents" placeholder="Search title, text or description" value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { setPage(1); setApplied((a) => ({ ...a, q: q.trim() })); } }} />
            <button className="btn small" onClick={() => { setPage(1); setApplied((a) => ({ ...a, q: q.trim() })); }}>Search</button>
            <label className="sr-only" htmlFor="kb-status">Status</label>
            <select id="kb-status" aria-label="Document status" value={applied.status} onChange={(e) => filter("status", e.target.value)}>
              <option value="">Every status</option>{(meta?.document_statuses ?? ["draft", "published", "archived"]).map((st) => <option key={st} value={st}>{LABEL[st] ?? st}</option>)}</select>
            <select aria-label="Document category" value={applied.category} onChange={(e) => filter("category", e.target.value)}>
              <option value="">Every category</option>{(meta?.categories ?? []).map((c) => <option key={c} value={c}>{c}</option>)}</select>
          </div>
          {!docs && !error && <p className="muted" aria-busy="true">Loading…</p>}
          {docs?.length === 0 && <p className="muted">{applied.q || applied.status || applied.category ? "No document matches these filters." : "No documents yet."}</p>}
          {docs && docs.length > 0 && (
            <div className="kw-tablewrap"><table className="kw-table kb-doctable" aria-busy={loading}>
              <caption className="sr-only">Documents of this knowledge base</caption>
              <thead><tr><th scope="col">Document</th><th scope="col">Status</th><th scope="col">Updated</th></tr></thead>
              <tbody>{docs.map((d) => (
                <tr key={d.doc_id} className={sel === d.doc_id ? "sel" : undefined}>
                  <th scope="row"><button className={`row linkish${sel === d.doc_id ? " sel" : ""}`} onClick={() => setSel(d.doc_id)} aria-label={`Open ${d.title}`} aria-current={sel === d.doc_id || undefined}>
                    <strong>{d.title}</strong> <span className="muted tiny">{d.doc_id} · {d.category} · {LABEL[d.status] ?? d.status} · latest v{d.latest_version}{d.published_version ? ` · live v${d.published_version}` : ""}</span></button></th>
                  <td><span className={`kw-chip${d.status === "published" ? " live" : ""}`}>{LABEL[d.status] ?? d.status}</span>{d.legal_hold ? <span className="kw-chip st-failed" title={d.legal_hold_reason ?? ""}>Legal hold</span> : null}</td>
                  <td className="tiny">{when(d.updated_at ?? d.created_at)}</td>
                </tr>))}</tbody>
            </table></div>)}
          <nav className="kb-pager" aria-label="Pages">
            <button className="btn small" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Previous</button>
            <span className="muted tiny" role="status">Page {page} of {pages} · {total} document(s)</span>
            <button className="btn small" disabled={page >= pages || loading} onClick={() => setPage((p) => p + 1)}>Next</button>
          </nav>
          {meta && meta.unmanaged_disk_documents.length > 0 && <p className="muted tiny" data-testid="unmanaged">{meta.unmanaged_disk_documents.length} article(s) come from files on disk and are not managed here yet. <button className="btn small" onClick={() => setTab("import")}>Import them</button></p>}
        </section>
        <div className="kb-detail" ref={detailRef} tabIndex={-1} aria-label="Selected document">
          {sel ? <>
            <p className="kb-detail-back"><button className="btn small" onClick={() => setSel(null)}><span aria-hidden="true">←</span> Back to the documents</button></p>
            <Detail key={sel} docId={sel} meta={meta} onChanged={changed} onGone={(m) => { setSel(null); changed(m); }} />
          </> : <div className="card kb-empty"><h2>No document selected</h2><p className="muted">Choose a document in the list to see its versions, compare them, process and publish, place a legal hold, or delete it.</p></div>}
        </div>
        </div>
      </div>)}
      {tab === "review" && <><Proposals onChanged={changed} categories={meta?.categories ?? []} /><ProposalQueue onOpen={(id) => { setSel(id); setTab("documents"); }} /></>}
      {tab === "audit" && <AuditBrowser actions={meta?.audit_actions ?? []} onOpen={(id) => { setSel(id); setTab("documents"); }} />}
      {tab === "import" && <ImportWizard meta={meta} onDone={(m) => changed(m)} />}
    </div>
  );
}

function NewDocument({ meta, onDone, onCancel }: { meta: KbMeta; onDone: (id: string) => void; onCancel: () => void }) {
  const { withToken } = useAuth();
  const [f, setF] = useState({ doc_id: "", title: "", category: meta.categories[0] ?? "faq", content: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;                                                    // one click = one request (no duplicate drafts)
    setBusy(true); setError(null);
    withToken((t) => api.kbCreate(t, f)).then((r) => { setF({ ...f, doc_id: "", title: "", content: "" }); onDone(r.document.doc_id); }).catch((er) => setError(msg(er))).finally(() => setBusy(false));
  };
  return (
    <section className="card kb-new" aria-label="New document" onKeyDown={(e) => { if (e.key === "Escape" && !busy) onCancel(); }}>
      <h2>New document</h2>
      {error && <p role="alert" className="error">{error}</p>}
      <form className="form" onSubmit={submit}>
        <label>Id (lower-case letters, digits, dashes)<input value={f.doc_id} onChange={(e) => setF({ ...f, doc_id: e.target.value })} required maxLength={128} pattern="[a-z0-9][a-z0-9-]*" autoFocus /></label>
        <label>Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required maxLength={300} /></label>
        <label>Category<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{meta.categories.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label>Text (Markdown, up to {Math.round(meta.limits.max_bytes / 1000)} KB)<textarea rows={6} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} required /></label>
        <div className="actions"><button type="button" className="btn" disabled={busy} onClick={onCancel}>Cancel</button>
          <button className="btn primary" disabled={busy} aria-busy={busy}>{busy ? "Creating…" : "Create document"}</button></div>
      </form>
    </section>
  );
}

function Detail({ docId, meta, onChanged, onGone }: { docId: string; meta: KbMeta | null; onChanged: (m?: string) => void; onGone: (m: string) => void }) {
  const { withToken } = useAuth();
  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [vers, setVers] = useState<KbVersionRow[]>([]);
  const [audit, setAudit] = useState<KbAuditRow[]>([]);
  const [shown, setShown] = useState<KbVersionRow | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [purging, setPurging] = useState(false);
  const [holding, setHolding] = useState(false);
  const [cmp, setCmp] = useState<{ from: number; to: number } | null>(null);

  const load = useCallback(() => {
    withToken((t) => Promise.all([api.kbDoc(t, docId), api.kbVersions(t, docId), api.kbAudit(t, docId)])).then(([d, v, a]) => { setDoc(d); setVers(v.items); setAudit(a.items); }).catch((e) => setError(msg(e)));
  }, [withToken, docId]);
  useEffect(() => { load(); }, [load]);

  const run = (fn: (t: string) => Promise<unknown>, done: string) => {
    setBusy(true); setError(null);
    withToken(fn).then(() => { load(); onChanged(done); }).catch((e) => { setError(msg(e)); load(); }).finally(() => setBusy(false));
  };
  const view = (n: number) => withToken((t) => api.kbVersion(t, docId, n)).then(setShown).catch((e) => setError(msg(e)));
  // M7 editor: start from the latest version's text (GET /versions/{n}); the save carries the row_version the page loaded (POST /versions), so a stale page gets 409.
  const [baseText, setBaseText] = useState<string | null>(null);
  const [baseVer, setBaseVer] = useState<number | null>(null);
  const [loadingText, setLoadingText] = useState(false);
  const loadLatest = () => {
    if (!vers[0]) return;
    setLoadingText(true);
    withToken((t) => api.kbVersion(t, docId, vers[0].version)).then((v) => { setText(v.content ?? ""); setBaseText(v.content ?? ""); setBaseVer(v.version); })
      .catch((e) => setError(msg(e))).finally(() => setLoadingText(false));
  };
  const addVersion = (e: FormEvent) => {
    e.preventDefault();
    if (!doc || busy) return;
    run((t) => api.kbAddVersion(t, docId, text, note, doc.row_version).then(() => { setText(""); setNote(""); setBaseText(null); setBaseVer(null); }), "New version saved as a draft.");
  };
  const latest = vers[0];
  const nextStep = !latest || !doc ? null : doc.status === "archived" ? "Archived: unarchive it to work on it again."
    : latest.lifecycle === "draft" ? `v${latest.version} is a draft: process it to check it and build its passages.`
    : latest.lifecycle === "processing" ? `v${latest.version} is being processed…`
    : latest.lifecycle === "failed" ? `Processing v${latest.version} failed${latest.processing_error ? `: ${latest.processing_error}` : ""}. Fix the text or process it again.`
    : latest.lifecycle === "ready_for_review" ? `v${latest.version} is processed and ready: review it and publish it.`
    : latest.lifecycle === "published" ? `v${latest.version} is live.` : `Latest version v${latest.version}: ${LABEL[latest.lifecycle] ?? latest.lifecycle}.`;

  return (
    <section className="card" aria-label={`Document ${docId}`}>
      {error && <p role="alert" className="error">{error}</p>}
      {!doc && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {doc && (<>
        <h2>{doc.title}</h2>
        <p className="muted tiny">{doc.doc_id} · {doc.category} · {LABEL[doc.status]} · {doc.published_version ? `live version ${doc.published_version}` : "nothing live"}</p>
        {latest && nextStep && (
          <div className={`kb-pubbar lc-${latest.lifecycle}`} role="group" aria-label="Publication">
            <span><b>{LABEL[latest.lifecycle] ?? latest.lifecycle}</b> · {nextStep}</span>
            {doc.status !== "archived" && (latest.lifecycle === "draft" || latest.lifecycle === "failed") &&
              <button className="btn small" disabled={busy} onClick={() => run((t) => api.kbProcess(t, docId, latest.version).then((r) => { if (r.status === "failed") throw new ApiError(422, `Processing failed: ${r.error ?? "unknown error"}`); }), `Version ${latest.version} processed.`)}>Process latest (v{latest.version})</button>}
            {doc.status !== "archived" && latest.lifecycle === "ready_for_review" &&
              <button className="btn small primary" disabled={busy} onClick={() => run((t) => api.kbPublish(t, docId, latest.version), `Version ${latest.version} is now live.`)}>Publish latest (v{latest.version})</button>}
          </div>)}
        <div className="actions">
          {doc.status === "published" && <button className="btn" disabled={busy} onClick={() => run((t) => api.kbDocAction(t, docId, "unpublish"), "Unpublished: the assistant no longer uses this document.")}>Unpublish</button>}
          {doc.status === "published" && doc.published_version != null &&
            <button className="btn danger" disabled={busy} onClick={() => setDeleting(true)}>Request deletion</button>}
          {doc.status !== "archived" && doc.status !== "published" && <button className="btn danger" disabled={busy} onClick={() => run((t) => api.kbDocAction(t, docId, "archive"), "Archived: history is kept, retrieval stopped.")}>Archive</button>}
          {doc.status === "archived" && <button className="btn" disabled={busy} onClick={() => run((t) => api.kbDocAction(t, docId, "unarchive"), "Restored to draft. Nothing is live until you publish a version.")}>Unarchive</button>}
          <button className="btn" disabled={busy} onClick={() => setHolding(true)}>{doc.legal_hold ? "Release legal hold" : "Place legal hold"}</button>
          {doc.status !== "published" && meta?.purge_enabled !== false &&
            <button className="btn danger solid" disabled={busy || !!doc.legal_hold} title={doc.legal_hold ? "Release the legal hold first" : undefined} onClick={() => setPurging(true)}>Permanently delete…</button>}
        </div>
        {doc.legal_hold && <p className="kw-banner warn" role="note"><b>Legal hold</b>{doc.legal_hold_reason ? `: ${doc.legal_hold_reason}` : ""} — placed by {doc.legal_hold_by ?? "an administrator"} {doc.legal_hold_at ? `on ${when(doc.legal_hold_at)}` : ""}. This document cannot be deleted permanently while the hold is in place.</p>}
        {holding && <LegalHold doc={doc} onClose={() => setHolding(false)} onDone={(m) => { setHolding(false); load(); onChanged(m); }} />}
        {purging && <PermanentDelete doc={doc} onClose={() => setPurging(false)} onDone={(m) => { setPurging(false); onGone(m); }} onRefresh={load} />}
        {deleting && doc.published_version != null && <DeletionRequest scope="admin" docId={docId} title={doc.title} version={doc.published_version}
          onClose={() => setDeleting(false)} onDone={(m) => { setDeleting(false); onChanged(m); }} />}
        <p className="muted tiny"><b>Archive</b> keeps every version and can be undone; a published document is archived only through a deletion request that an administrator approves in Change requests. <b>Permanently delete</b> is a separate step for an archived (or never-published) document: it removes all its versions, passages and embeddings and cannot be undone. Unpublish stops answers from it at once and can be undone by publishing again.</p>
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
        {vers.length > 1 && (
          <div className="kb-compare-bar" role="group" aria-label="Compare versions">
            <label>From<select aria-label="Compare from version" value={cmp?.from ?? vers[1].version} onChange={(e) => setCmp({ from: Number(e.target.value), to: cmp?.to ?? vers[0].version })}>
              {vers.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</select></label>
            <label>To<select aria-label="Compare to version" value={cmp?.to ?? vers[0].version} onChange={(e) => setCmp({ from: cmp?.from ?? vers[1].version, to: Number(e.target.value) })}>
              {vers.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</select></label>
            <VersionCompare docId={docId} title={doc.title} from={cmp?.from ?? vers[1].version} to={cmp?.to ?? vers[0].version} />
          </div>)}
        {shown && <div className="card" aria-label={`Version ${shown.version} text`}><p className="muted tiny">Version {shown.version} · checksum {shown.checksum.slice(0, 12)}</p><pre className="tiny" style={{ whiteSpace: "pre-wrap" }}>{shown.content}</pre></div>}
        {doc.status !== "archived" && (
          <form className="form kb-editor" onSubmit={addVersion} aria-label="New version">
            <div className="bar"><h3>Edit — new version</h3>
              {vers[0] && <button type="button" className="btn small" disabled={busy || loadingText} onClick={loadLatest}>{text ? "Reload" : "Start from"} v{vers[0].version} text</button>}</div>
            <p className="muted tiny">Saving creates a new <b>draft</b> version (the live text is not touched). It is checked against the document as you loaded it: if someone else changed it meanwhile, nothing is overwritten.</p>
            <label>Text<textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} required /></label>
            {text && baseText !== null && baseText !== text && <TextCompare before={baseText} after={text} beforeLabel={`Version ${baseVer}`} afterLabel="Your edit (not saved)" name={doc.title} />}
            <label>What changed<input value={note} onChange={(e) => setNote(e.target.value)} required maxLength={500} /></label>
            <div className="actions"><button className="btn primary" disabled={busy || !text.trim() || (baseText !== null && baseText === text)}>Save as draft version</button></div>
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

/** M7 diff view: any two versions, with the server's line statistics (GET /compare) and a word-level comparison of the two complete texts. */
function VersionCompare({ docId, title, from, to }: { docId: string; title: string; from: number; to: number }) {
  const { withToken } = useAuth();
  const [res, setRes] = useState<{ cmp: KbCompare; a: string; b: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setRes(null); }, [from, to]);
  const go = () => {
    if (from === to) { setError("Choose two different versions."); return; }
    setBusy(true); setError(null);
    withToken((t) => Promise.all([api.kbCompare(t, docId, from, to), api.kbVersion(t, docId, from), api.kbVersion(t, docId, to)]))
      .then(([cmp, a, b]) => setRes({ cmp, a: a.content ?? "", b: b.content ?? "" })).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  return (
    <>
      <button className="btn small" disabled={busy} onClick={go}>Compare v{from} → v{to}</button>
      {error && <p role="alert" className="error">{error}</p>}
      {res && (
        <div className="kb-compare">
          <p className="muted tiny">{res.cmp.identical ? "Identical text." : `+${res.cmp.stats.added_lines} / −${res.cmp.stats.removed_lines} lines · ${res.cmp.stats.size_delta_bytes >= 0 ? "+" : ""}${res.cmp.stats.size_delta_bytes} bytes`}
            {res.cmp.title_changed ? ` · title changed: “${res.cmp.from.title}” → “${res.cmp.to.title}”` : ""}</p>
          <TextCompare before={res.a} after={res.b} beforeLabel={`Version ${from}`} afterLabel={`Version ${to}`} name={title} />
        </div>)}
    </>
  );
}

function LegalHold({ doc, onClose, onDone }: { doc: KbDoc; onClose: () => void; onDone: (m: string) => void }) {
  const { withToken } = useAuth();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const placing = !doc.legal_hold;
  const submit = (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    withToken((t) => api.kbLegalHold(t, doc.doc_id, { hold: placing, ...(placing ? { reason: reason.trim() } : {}), expected_row_version: doc.row_version }))
      .then(() => onDone(placing ? "Legal hold placed: the document cannot be deleted permanently until it is released." : "Legal hold released."))
      .catch((er) => setError(msg(er))).finally(() => setBusy(false));
  };
  return (
    <div className="dialog-back" role="presentation" onClick={onClose}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="hold-h" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2 id="hold-h">{placing ? "Place a legal hold" : "Release the legal hold"} on “{doc.title}”</h2>
        <p className="muted">{placing ? "A held document can still be edited, published, unpublished and archived as usual, but it can never be deleted permanently while the hold is in place." : "After release the document can be deleted permanently again (subject to the other rules)."}</p>
        {placing && <label>Reason (required)<input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} placeholder="e.g. records requested for audit 2026" autoFocus /></label>}
        {error && <p role="alert" className="kw-error">{error}</p>}
        <div className="actions"><button type="button" className="kw-btn" onClick={onClose}>Cancel</button>
          <button className="kw-btn primary" disabled={busy || (placing && reason.trim().length < 3)}>{placing ? "Place hold" : "Release hold"}</button></div>
      </form>
    </div>
  );
}

/** Permanent deletion: shows exactly what will be removed (counts, no text) and why it may be refused; the administrator types the id AND the title and ticks
 *  that it cannot be undone. The request carries the row_version this dialog was opened with, so a stale page or another document can never be deleted. */
function PermanentDelete({ doc, onClose, onDone, onRefresh }: { doc: KbDoc; onClose: () => void; onDone: (m: string) => void; onRefresh: () => void }) {
  const { withToken } = useAuth();
  const [pv, setPv] = useState<KbPurgePreview | null>(null);
  const [idText, setIdText] = useState("");
  const [titleText, setTitleText] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { withToken((t) => api.kbPurgePreview(t, doc.doc_id)).then(setPv).catch((e) => setError(msg(e))); }, [withToken, doc.doc_id]);
  const matches = idText === doc.doc_id && titleText.trim() === doc.title.trim();
  const blocked = !pv || pv.blockers.length > 0 || pv.row_version !== doc.row_version;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!matches || !sure || blocked) return;
    setBusy(true); setError(null);
    withToken((t) => api.kbPurge(t, doc.doc_id, { confirm_doc_id: idText, confirm_title: titleText, expected_row_version: doc.row_version }))
      .then((r) => onDone(r.outcome === "already_deleted" ? `“${doc.title}” had already been deleted permanently.` : `“${doc.title}” was deleted permanently. Only a record that it existed remains in the audit log.`))
      .catch((er) => { setError(msg(er)); onRefresh(); }).finally(() => setBusy(false));
  };
  return (
    <div className="dialog-back" role="presentation" onClick={onClose}>
      <form className="dialog danger" role="dialog" aria-modal="true" aria-labelledby="purge-h" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2 id="purge-h">Permanently delete “{doc.title}”?</h2>
        <p className="kw-banner warn" role="note"><b>This cannot be undone.</b> Unlike Archive, nothing is kept to restore: every version, its passages and embeddings, and this document's text inside closed change requests are removed. The audit log keeps only that the deletion happened (id, who, when).</p>
        {!pv && !error && <p className="muted" aria-busy="true">Checking what would be removed…</p>}
        {pv && (
          <ul className="kb-purge-list" aria-label="What will be removed">
            <li>{pv.removes.versions} version(s)</li><li>{pv.removes.staged_chunks + pv.removes.live_chunks} passage(s) with embeddings</li>
            <li>this document in {pv.removes.change_requests} closed change request(s)</li><li>{pv.removes.proposals} finished feedback proposal(s)</li>
          </ul>)}
        {pv?.disk_file_note && <p className="muted tiny">{pv.disk_file_note}</p>}
        {pv && pv.blockers.length > 0 && <div className="kw-banner warn" role="alert"><b>It cannot be deleted right now:</b><ul>{pv.blockers.map((b) => <li key={b}>{b}</li>)}</ul></div>}
        {pv && pv.row_version !== doc.row_version && <p role="alert" className="kw-error">The document changed since this page was loaded. Close this dialog and check it again.</p>}
        <label>Type the document id <code>{doc.doc_id}</code><input value={idText} onChange={(e) => setIdText(e.target.value)} autoComplete="off" spellCheck={false} aria-label="Type the document id to confirm" /></label>
        <label>Type the title <code>{doc.title}</code><input value={titleText} onChange={(e) => setTitleText(e.target.value)} autoComplete="off" aria-label="Type the document title to confirm" /></label>
        <label className="kw-check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I understand that this permanently deletes the document and cannot be undone.</label>
        {error && <p role="alert" className="kw-error">{error}</p>}
        <div className="actions"><button type="button" className="kw-btn" onClick={onClose}>Cancel</button>
          <button className="kw-btn danger solid" disabled={busy || !matches || !sure || blocked}>Delete permanently</button></div>
      </form>
    </div>
  );
}

const PROPOSAL_WORDS: Record<string, string> = { proposed: "Waiting for review", approved: "Approved — not yet published", rejected: "Rejected", published: "Published" };

/** M7 review queue: every feedback proposal of the tenant with its state (GET /proposals), filterable; a proposal is reviewed on its feedback card above and its
 *  draft version is processed and published from the document's page. */
function ProposalQueue({ onOpen }: { onOpen: (docId: string) => void }) {
  const { withToken } = useAuth();
  const [status, setStatus] = useState("");
  const [items, setItems] = useState<KbProposalRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setItems(null); withToken((t) => api.kbProposals(t, status || undefined)).then((r) => setItems(r.items)).catch((e) => setError(msg(e))); }, [withToken, status]);
  return (
    <section className="card" aria-label="Proposal queue">
      <div className="bar"><h2>All proposals</h2>
        <select aria-label="Proposal status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Every status</option>{Object.entries(PROPOSAL_WORDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
      {error && <p role="alert" className="error">{error}</p>}
      {!items && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {items?.length === 0 && <p className="muted">No proposals{status ? " with this status" : " yet"}.</p>}
      {items && items.length > 0 && (
        <div className="kw-tablewrap"><table className="kw-table">
          <caption className="sr-only">Feedback proposals</caption>
          <thead><tr><th scope="col">Document</th><th scope="col">State</th><th scope="col">Proposed</th><th scope="col">Reviewed</th></tr></thead>
          <tbody>{items.map((p) => (
            <tr key={p.call_id}><th scope="row"><button className="row linkish" onClick={() => onOpen(p.doc_id)} aria-label={`Open document ${p.doc_id}`}>{p.doc_id} <span className="muted tiny">v{p.version}</span></button></th>
              <td><span className="kw-chip">{PROPOSAL_WORDS[p.status] ?? p.status}</span></td>
              <td className="tiny">{p.proposed_by}{p.proposed_at ? ` · ${when(p.proposed_at)}` : ""}</td>
              <td className="tiny">{p.reviewed_by ? `${p.reviewed_by}${p.reviewed_at ? ` · ${when(p.reviewed_at)}` : ""}` : "—"}{p.review_note ? ` · ${p.review_note}` : ""}</td></tr>))}</tbody>
        </table></div>)}
    </section>
  );
}

/** M7 audit browser: the tenant's knowledge audit trail, newest first, filtered by document, action and actor, paged with the server's `before_id` cursor.
 *  Events hold ids, counts and field names only (never document text). */
function AuditBrowser({ actions, onOpen }: { actions: string[]; onOpen: (docId: string) => void }) {
  const { withToken } = useAuth();
  const [f, setF] = useState({ doc_id: "", action: "", actor: "" });
  const [applied, setApplied] = useState(f);
  const [rows, setRows] = useState<KbAuditRow[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fetchPage = useCallback((before: number | null, append: boolean) => {
    setBusy(true); setError(null);
    withToken((t) => api.kbAuditPage(t, { doc_id: applied.doc_id.trim() || undefined, action: applied.action || undefined, actor: applied.actor.trim() || undefined, before_id: before }))
      .then((r) => { setRows((p) => (append && p ? [...p, ...r.items] : r.items)); setNext(r.next_before_id); })
      .catch((e) => setError(msg(e))).finally(() => setBusy(false));
  }, [withToken, applied]);
  useEffect(() => { setRows(null); fetchPage(null, false); }, [fetchPage]);
  return (
    <section className="card" aria-label="Audit log">
      <h2>Audit log</h2>
      <form className="bar kb-filters" aria-label="Audit filters" onSubmit={(e) => { e.preventDefault(); setApplied({ ...f }); }}>
        <input aria-label="Filter by document id" placeholder="Document id" value={f.doc_id} onChange={(e) => setF({ ...f, doc_id: e.target.value })} maxLength={128} />
        <select aria-label="Filter by action" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}>
          <option value="">Every action</option>{actions.map((a) => <option key={a} value={a}>{ACTION_WORDS(a)}</option>)}</select>
        <input aria-label="Filter by actor" placeholder="Who (account name)" value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} maxLength={64} />
        <button className="btn small">Apply</button>
        {(applied.doc_id || applied.action || applied.actor) && <button type="button" className="btn small" onClick={() => { const z = { doc_id: "", action: "", actor: "" }; setF(z); setApplied(z); }}>Clear</button>}
      </form>
      {error && <p role="alert" className="error">{error}</p>}
      {!rows && !error && <p className="muted" aria-busy="true">Loading…</p>}
      {rows?.length === 0 && <p className="muted">No audit events match.</p>}
      {rows && rows.length > 0 && (
        <div className="kw-tablewrap"><table className="kw-table kb-audit">
          <caption className="sr-only">Knowledge audit events, newest first</caption>
          <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Document</th><th scope="col">Details</th></tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id}><td className="tiny">{when(a.at)}</td><td>{a.actor}</td><td>{ACTION_WORDS(a.action)}</td>
              <td>{a.doc_id ? (a.action === "document_purged" ? <span>{a.doc_id} <span className="kw-chip st-failed">deleted</span></span>
                : <button className="row linkish" onClick={() => onOpen(a.doc_id!)} aria-label={`Open document ${a.doc_id}`}>{a.doc_id}</button>) : "—"}{a.version ? ` · v${a.version}` : ""}</td>
              <td className="tiny">{Object.entries(a.summary ?? {}).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ") || "—"}</td></tr>))}</tbody>
        </table></div>)}
      {next && <div className="actions"><button className="btn small" disabled={busy} onClick={() => fetchPage(next, true)}>Load older events</button></div>}
    </section>
  );
}

/** M7 import wizard: articles that are still served from files in the tenant's docs folder (loaded by `make ingest`) become managed, versioned documents. The same
 *  text is imported as version 1 and published, so what the assistant answers does not change; afterwards the document is edited only through the store. */
function ImportWizard({ meta, onDone }: { meta: KbMeta | null; onDone: (m: string) => void }) {
  const { withToken } = useAuth();
  const [step, setStep] = useState<"list" | "confirm" | "done">("list");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [res, setRes] = useState<{ imported: string[]; skipped: string[]; failed: { doc_id: string; error: string }[] } | null>(null);
  const files = meta?.unmanaged_disk_documents ?? [];
  const run = () => {
    setBusy(true); setError(null);
    withToken((t) => api.kbImportDisk(t)).then((r) => { setRes(r); setStep("done"); onDone(`${r.imported.length} article(s) imported.`); })
      .catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  return (
    <section className="card" aria-label="Import from disk">
      <h2>Import articles from disk</h2>
      <ol className="kb-steps" aria-label="Steps"><li aria-current={step === "list" || undefined}>Review the articles</li><li aria-current={step === "confirm" || undefined}>Confirm</li><li aria-current={step === "done" || undefined}>Result</li></ol>
      {error && <p role="alert" className="error">{error}</p>}
      {!meta && <p className="muted" aria-busy="true">Loading…</p>}
      {meta && step === "list" && (files.length === 0 ? <p className="muted">Every article on disk is already managed here. Nothing to import.</p> : (<>
        <p>{files.length} article(s) are answered from files in the tenant's docs folder and are not managed here yet:</p>
        <ul className="kb-import-list">{files.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
        <p className="muted tiny">Each is imported with its current text as version 1 and published, so answers stay the same. From then on it changes only through versions, review and publication here. Permanently deleted ids are never imported again.</p>
        <div className="actions"><button className="btn primary" onClick={() => setStep("confirm")}>Continue</button></div>
      </>))}
      {step === "confirm" && (<>
        <p><b>Import {files.length} article(s) now?</b> Their text is not changed.</p>
        <div className="actions"><button className="btn primary" disabled={busy} onClick={run}>Import {files.length} article(s)</button><button className="btn" onClick={() => setStep("list")}>Back</button></div>
      </>)}
      {step === "done" && res && (
        <div role="status">
          <p>{res.imported.length} imported · {res.skipped.length} skipped · {res.failed.length} failed.</p>
          {res.failed.length > 0 && <ul className="kw-unsure">{res.failed.map((x) => <li key={x.doc_id}><code>{x.doc_id}</code>: {x.error}</li>)}</ul>}
        </div>)}
    </section>
  );
}
