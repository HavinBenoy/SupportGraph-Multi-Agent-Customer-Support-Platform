"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwDiff, KwItem, KwReadDoc, KwRequest } from "@/lib/types";
import { DiffLegend, DiffView, Stats } from "./DiffView";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

/** The editing workspace: the published text stays untouched on the server; this edits a private copy inside a change request.
 *  Edit tab = plain text area. Review tab = the server's structured diff of the copy against the published original. */
export function Editor({ doc, onClose, onSubmitted, onFindRelated }: { doc: KwReadDoc; onClose: (note?: string) => void; onSubmitted: (note: string) => void; onFindRelated?: (r: { requestId: string; description: string; docId: string; title: string }) => void }) {
  const { withToken } = useAuth();
  const [req, setReq] = useState<KwRequest | null>(null);
  const [item, setItem] = useState<KwItem | null>(null);
  const [text, setText] = useState(doc.content);
  const [saved, setSaved] = useState(doc.content);
  const [view, setView] = useState<"edit" | "review">("edit");
  const [diff, setDiff] = useState<KwDiff | null>(null);
  const [unchanged, setUnchanged] = useState(true);
  const [stats, setStats] = useState({ added: 0, deleted: 0 });
  const [desc, setDesc] = useState("");
  const [asking, setAsking] = useState(false);
  const [aiAsking, setAiAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    withToken((t) => api.kwStart(t, doc.doc_id)).then((r) => {
      setReq(r.request); setDesc(r.request.description ?? "");
      const it = r.request.items?.find((i) => i.doc_id === doc.doc_id) ?? null;
      setItem(it);
      if (it?.proposed_content !== undefined) { setText(it.proposed_content); setSaved(it.proposed_content); }
      if (r.existing) setNote("Resumed your saved draft.");
    }).catch((e) => setError(msg(e)));
  }, [withToken, doc.doc_id]);

  const dirty = text !== saved;
  useEffect(() => {                                         // browsers ask before a tab with unsaved text is closed
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  // The review diff comes from the server (single source of truth), debounced so typing never waits on it.
  useEffect(() => {
    if (view !== "review") return;
    let live = true;
    const t = setTimeout(() => {
      withToken((tk) => api.kwPreview(tk, doc.doc_id, text)).then((p) => {
        if (!live) return;
        setDiff(p.diff); setUnchanged(p.unchanged); setStats({ added: p.stats.added_chars, deleted: p.stats.deleted_chars }); setError(null);
      }).catch((e) => live && setError(msg(e)));
    }, 350);
    return () => { live = false; clearTimeout(t); };
  }, [view, text, withToken, doc.doc_id]);

  const save = useCallback(async (): Promise<boolean> => {
    if (!req || !item) return false;
    setBusy(true); setError(null); setConflict(false);
    try {
      const it = await withToken((t) => api.kwSaveItem(t, req.request_id, doc.doc_id, text, item.row_version));
      setItem(it); setSaved(text); setNote("Draft saved. The published text has not changed."); return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(true);
      setError(msg(e)); return false;
    } finally { setBusy(false); }
  }, [req, item, text, withToken, doc.doc_id]);

  const reload = () => {
    if (!req) return;
    withToken((t) => api.kwMyRequest(t, req.request_id)).then((r) => {
      const it = r.items?.find((i) => i.doc_id === doc.doc_id) ?? null;
      setReq(r); setItem(it); if (it?.proposed_content !== undefined) { setText(it.proposed_content); setSaved(it.proposed_content); }
      setConflict(false); setError(null); setNote("Reloaded the latest saved draft.");
    }).catch((e) => setError(msg(e)));
  };

  const submit = async () => {
    if (!req) return;
    if (!desc.trim()) { setError("Describe the change: say what changed and why."); return; }
    if (dirty && !(await save())) return;
    setBusy(true); setError(null);
    try { await withToken((t) => api.kwSubmit(t, req.request_id, desc.trim())); onSubmitted("Change request submitted. The published text stays live until an administrator approves and publishes it."); }
    catch (e) { setError(msg(e)); } finally { setBusy(false); }
  };

  const findRelated = async () => {
    if (!req) return;
    if (desc.trim().length < 8) { setError("Say what changed, in a sentence, so the assistant knows what to look for."); return; }
    if (dirty && !(await save())) return;
    onFindRelated?.({ requestId: req.request_id, description: desc.trim(), docId: doc.doc_id, title: doc.title });
  };

  const cancel = async () => {
    if (dirty || (item && (item.stats.added_chars || item.stats.deleted_chars))) { if (!window.confirm("Discard your changes? The published document is not affected.")) return; }
    if (req) { try { await withToken((t) => api.kwCancel(t, req.request_id)); } catch (e) { setError(msg(e)); return; } }
    onClose("Changes discarded.");
  };

  const ready = !!req && !!item;
  return (
    <section className="kw-editor" aria-label={`Editing ${doc.title}`}>
      <header className="kw-editor-head">
        <div>
          <p className="kw-eyebrow">Editing a copy · original v{doc.version} stays live</p>
          <h2 className="kw-title">{doc.title}</h2>
        </div>
        <div className="kw-seg" role="tablist" aria-label="Editor view">
          <button role="tab" aria-selected={view === "edit"} onClick={() => setView("edit")}>Edit</button>
          <button role="tab" aria-selected={view === "review"} onClick={() => setView("review")}>Review changes</button>
        </div>
      </header>
      {error && <p role="alert" className="kw-error">{error}</p>}
      {conflict && <p className="kw-banner warn">This draft was saved from another window. <button className="kw-btn small" onClick={reload}>Load the latest draft</button> (your unsaved text here will be replaced).</p>}
      {note && !error && <p role="status" className="kw-banner ok">{note}</p>}
      {view === "edit" ? (
        <textarea className="kw-textarea" aria-label="Document text" value={text} spellCheck onChange={(e) => { setText(e.target.value); setNote(null); }} disabled={!ready} rows={22} />
      ) : (
        <div>
          <div className="kw-review-bar"><DiffLegend /><Stats added={stats.added} deleted={stats.deleted} /></div>
          {diff === null ? <p className="kw-muted" aria-busy="true">Comparing…</p>
            : unchanged ? <p className="kw-muted">No differences from the published text yet.</p>
            : <DiffView segs={diff.body} label="Your changes compared with the published text" />}
        </div>
      )}
      {asking && (
        <div className="kw-ask">
          <label>What changed, and why?
            <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} maxLength={1000} placeholder="e.g. Fee payable in three instalments from the December batch" />
          </label>
          <div className="kw-actions">
            <button className="kw-btn primary" onClick={submit} disabled={busy || !ready}>Send for approval</button>
            <button className="kw-btn" onClick={() => setAsking(false)}>Back</button>
          </div>
        </div>
      )}
      {aiAsking && (
        <div className="kw-ask">
          <label>Describe the change in a sentence
            <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} maxLength={1000} placeholder="e.g. Python Full Stack fee is now ₹48,000 (was ₹45,000)" />
          </label>
          <p className="kw-muted tiny">The assistant will look for other documents that may need the same change. It only suggests: you decide on each one.</p>
          <div className="kw-actions"><button className="kw-btn primary" onClick={findRelated} disabled={busy || !ready}>Find related documents</button><button className="kw-btn" onClick={() => setAiAsking(false)}>Back</button></div>
        </div>)}
      <footer className="kw-actions kw-editor-foot">
        <button className="kw-btn" onClick={save} disabled={busy || !ready || !dirty}>Save draft</button>
        <button className="kw-btn primary" onClick={() => { setAsking(true); setView("review"); }} disabled={busy || !ready}>Submit change request…</button>
        {onFindRelated && <button className="kw-btn" onClick={() => setAiAsking(true)} disabled={busy || !ready}>Find related documents with AI</button>}
        <button className="kw-btn ghost" onClick={cancel} disabled={busy}>Cancel changes</button>
        {dirty && <span className="kw-muted tiny">Unsaved changes</span>}
      </footer>
    </section>
  );
}
