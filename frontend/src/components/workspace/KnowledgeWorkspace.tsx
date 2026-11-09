"use client";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwFolder, KwHistoryRow, KwReadDoc, KwTree, KwTreeDoc } from "@/lib/types";
import { DiffView, DiffLegend } from "./DiffView";
import { Editor } from "./Editor";
import { MyRequests } from "./Requests";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/** Folder-and-file view of the knowledge base. Staff read published documents and start change requests; administrators also organise folders.
 *  The published text is only ever shown read-only; edits happen in the Editor, inside a change request. */
export function KnowledgeWorkspace({ scope }: { scope: "admin" | "staff" }) {
  const { withToken } = useAuth();
  const isAdmin = scope === "admin";
  const [tree, setTree] = useState<KwTree | null>(null);
  const [cwd, setCwd] = useState<string | null>(null);              // folder id; null = root
  const [open, setOpen] = useState<string | null>(null);            // doc id
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ doc_id: string; title: string; category: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<KwReadDoc | null>(null);
  const [mine, setMine] = useState(false);
  const [navOpen, setNavOpen] = useState(false);

  const load = useCallback(() => {
    withToken((t) => api.kwTree(t, scope)).then((r) => { setTree(r); setError(null); }).catch((e) => setError(msg(e)));
  }, [withToken, scope]);
  useEffect(load, [load]);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits(null); return; }
    let live = true;
    const t = setTimeout(() => withToken((tk) => api.kwSearch(tk, term, scope)).then((r) => live && setHits(r.items)).catch((e) => live && setError(msg(e))), 250);
    return () => { live = false; clearTimeout(t); };
  }, [q, scope, withToken]);

  const byParent = useMemo(() => {
    const m = new Map<string | null, KwFolder[]>();
    for (const f of tree?.folders ?? []) m.set(f.parent_id, [...(m.get(f.parent_id) ?? []), f]);
    return m;
  }, [tree]);
  const folderMap = useMemo(() => new Map((tree?.folders ?? []).map((f) => [f.folder_id, f])), [tree]);
  const docsHere = (tree?.documents ?? []).filter((d) => d.folder_id === cwd);
  const crumbs = useMemo(() => { const out: KwFolder[] = []; let c = cwd; while (c && folderMap.has(c)) { out.unshift(folderMap.get(c)!); c = folderMap.get(c)!.parent_id; } return out; }, [cwd, folderMap]);

  const goFolder = (id: string | null) => { setCwd(id); setOpen(null); setEditing(null); setMine(false); setNavOpen(false); };
  const goDoc = (d: { doc_id: string }, folder?: string | null) => { if (folder !== undefined) setCwd(folder); setOpen(d.doc_id); setEditing(null); setMine(false); setQ(""); setNavOpen(false); };

  const renderFolders = (parent: string | null, depth: number): React.ReactNode =>
    (byParent.get(parent) ?? []).map((f) => (
      <li key={f.folder_id}>
        <button className={`kw-node${cwd === f.folder_id && !open ? " sel" : ""}`} style={{ paddingLeft: 10 + depth * 14 }} onClick={() => goFolder(f.folder_id)} aria-label={`Folder ${f.name}`}>
          <span aria-hidden="true">▸</span> {f.name} <span className="kw-count">{f.document_count}</span>
        </button>
        {(cwd === f.folder_id || crumbs.some((c) => c.folder_id === f.folder_id)) && <ul className="kw-tree">{renderFolders(f.folder_id, depth + 1)}</ul>}
      </li>));

  return (
    <div className="kw">
      <div className="kw-shell">
        <aside className={`kw-side${navOpen ? " open" : ""}`} aria-label="Knowledge folders">
          <div className="kw-search"><input type="search" placeholder="Search names and content" aria-label="Search knowledge" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <button className="kw-btn small kw-navtoggle" onClick={() => setNavOpen(false)}>Close</button>
          {hits ? (
            <ul className="kw-tree" aria-label="Search results">
              {hits.length === 0 && <li className="kw-muted pad">No documents match.</li>}
              {hits.map((h) => { const d = tree?.documents.find((x) => x.doc_id === h.doc_id); return (
                <li key={h.doc_id}><button className="kw-node" onClick={() => goDoc(h, d?.folder_id ?? null)} aria-label={`Open ${h.title}`}>{h.title}<span className="kw-sub">{h.category}</span></button></li>); })}
            </ul>
          ) : (
            <nav>
              <ul className="kw-tree">
                <li><button className={`kw-node${cwd === null && !open ? " sel" : ""}`} onClick={() => goFolder(null)} aria-label="Knowledge base (top level)"><span aria-hidden="true">◆</span> Knowledge base <span className="kw-count">{tree?.root_document_count ?? 0}</span></button></li>
                {renderFolders(null, 0)}
              </ul>
            </nav>
          )}
          <div className="kw-side-foot"><button className={`kw-node${mine ? " sel" : ""}`} onClick={() => { setMine(true); setOpen(null); setEditing(null); setNavOpen(false); }}>{isAdmin ? "My drafts & requests" : "My change requests"}</button></div>
        </aside>

        <main className="kw-main">
          <button className="kw-btn small kw-navtoggle" onClick={() => setNavOpen(true)}>Folders</button>
          {error && <p role="alert" className="kw-error">{error}</p>}
          {notice && <p role="status" className="kw-banner ok">{notice}</p>}
          {!tree && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}

          {tree && editing ? (
            <Editor doc={editing} onClose={(n) => { setEditing(null); if (n) setNotice(n); }} onSubmitted={(n) => { setEditing(null); setNotice(n); setMine(true); setOpen(null); }} />
          ) : tree && mine ? (
            <MyRequests onResume={(docId) => { setMine(false); setOpen(docId); }} />
          ) : tree && open ? (
            <Reader key={open} docId={open} tree={tree} isAdmin={isAdmin} onCrumb={goFolder} onEdit={setEditing} onChanged={() => load()} setNotice={setNotice} />
          ) : tree && (
            <FolderPage tree={tree} cwd={cwd} crumbs={crumbs} docs={docsHere} subfolders={byParent.get(cwd) ?? []} isAdmin={isAdmin} onFolder={goFolder} onDoc={(d) => goDoc(d)}
              onChanged={(m) => { if (m) setNotice(m); load(); }} />
          )}
        </main>
      </div>
    </div>
  );
}

function Crumbs({ items, onGo }: { items: { id: string | null; name: string }[]; onGo: (id: string | null) => void }) {
  return (
    <nav aria-label="Breadcrumb" className="kw-crumbs">
      <button onClick={() => onGo(null)}>Knowledge base</button>
      {items.map((c, i) => <span key={c.id}> / {i === items.length - 1 ? <b aria-current="page">{c.name}</b> : <button onClick={() => onGo(c.id)}>{c.name}</button>}</span>)}
    </nav>
  );
}

function FolderPage({ tree, cwd, crumbs, docs, subfolders, isAdmin, onFolder, onDoc, onChanged }: {
  tree: KwTree; cwd: string | null; crumbs: KwFolder[]; docs: KwTreeDoc[]; subfolders: KwFolder[]; isAdmin: boolean;
  onFolder: (id: string | null) => void; onDoc: (d: KwTreeDoc) => void; onChanged: (m?: string) => void;
}) {
  const { withToken } = useAuth();
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const here = crumbs[crumbs.length - 1];
  useEffect(() => { setRenaming(here?.name ?? ""); setError(null); }, [here?.folder_id, here?.name]);

  const create = (e: FormEvent) => {
    e.preventDefault(); if (!name.trim()) return;
    withToken((t) => api.kwCreateFolder(t, name.trim(), cwd)).then(() => { setName(""); setError(null); onChanged(`Folder “${name.trim()}” created.`); }).catch((er) => setError(msg(er)));
  };
  const rename = (e: FormEvent) => {
    e.preventDefault(); if (!here) return;
    withToken((t) => api.kwRenameFolder(t, here.folder_id, renaming.trim(), here.row_version)).then(() => { setError(null); onChanged("Folder renamed."); }).catch((er) => setError(msg(er)));
  };
  return (
    <section aria-label="Folder contents">
      <Crumbs items={crumbs.map((c) => ({ id: c.folder_id, name: c.name }))} onGo={onFolder} />
      <h1 className="kw-title">{here ? here.name : "Knowledge base"}</h1>
      <p className="kw-muted">{subfolders.length} folder(s) · {docs.length} document(s) here</p>
      {error && <p role="alert" className="kw-error">{error}</p>}
      <ul className="kw-grid">
        {subfolders.map((f) => <li key={f.folder_id}><button className="kw-tile" onClick={() => onFolder(f.folder_id)} aria-label={`Open folder ${f.name}`}><span className="kw-ico" aria-hidden="true">▸</span><b>{f.name}</b><span className="kw-sub">{f.document_count} document(s)</span></button></li>)}
        {docs.map((d) => (
          <li key={d.doc_id}><button className="kw-tile doc" onClick={() => onDoc(d)} aria-label={`Open ${d.title}`}>
            <span className="kw-ico" aria-hidden="true">≡</span><b>{d.title}</b>
            <span className="kw-sub">{d.category} · {d.published_version ? `v${d.published_version} live` : "not live"}</span></button></li>))}
      </ul>
      {subfolders.length + docs.length === 0 && <p className="kw-muted">This folder is empty.{isAdmin ? " Create a folder below, or move documents here from their page." : ""}</p>}
      {isAdmin && (
        <div className="kw-admin">
          <form onSubmit={create} className="kw-inline"><label>New folder here<input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Course fees" /></label><button className="kw-btn primary" disabled={!name.trim()}>Create folder</button></form>
          {here && <form onSubmit={rename} className="kw-inline"><label>Rename this folder<input value={renaming} onChange={(e) => setRenaming(e.target.value)} maxLength={80} /></label><button className="kw-btn" disabled={!renaming.trim() || renaming.trim() === here.name}>Rename</button></form>}
          <p className="kw-muted tiny">New documents are created, processed and published in the Publishing view; then move them here from the document page.</p>
        </div>
      )}
    </section>
  );
}

function Reader({ docId, tree, isAdmin, onCrumb, onEdit, onChanged, setNotice }: {
  docId: string; tree: KwTree; isAdmin: boolean; onCrumb: (id: string | null) => void; onEdit: (d: KwReadDoc) => void; onChanged: () => void; setNotice: (m: string) => void;
}) {
  const { withToken } = useAuth();
  const [doc, setDoc] = useState<KwReadDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<KwHistoryRow[] | null>(null);
  const [cmp, setCmp] = useState<{ from: number; to: number; body: import("@/lib/types").DiffSeg[] } | null>(null);
  const [shown, setShown] = useState<{ version: number; content: string } | null>(null);
  const row = tree.documents.find((d) => d.doc_id === docId);

  useEffect(() => { withToken((t) => api.kwRead(t, docId)).then(setDoc).catch((e) => setError(e instanceof ApiError && e.status === 404 ? "This document is not published, so there is nothing to read here yet." : msg(e))); }, [withToken, docId]);

  const showHistory = () => withToken((t) => api.kwHistory(t, docId)).then((r) => setHistory(r.items)).catch((e) => setError(msg(e)));
  const view = (n: number) => withToken((t) => api.kwVersion(t, docId, n)).then((v) => setShown({ version: n, content: v.content })).catch((e) => setError(msg(e)));
  const compare = (from: number, to: number) => withToken((t) => fetchCompare(t, docId, from, to)).then((r) => setCmp({ from, to, body: r })).catch((e) => setError(msg(e)));
  const move = (folder: string) => doc && withToken((t) => api.kwMove(t, docId, folder || null, doc.row_version)).then(() => { setNotice("Document moved."); onChanged(); setDoc(null); return withToken((t) => api.kwRead(t, docId)).then(setDoc); }).catch((e) => setError(msg(e)));

  if (error) return <section><Crumbs items={[]} onGo={onCrumb} /><h1 className="kw-title">{row?.title ?? docId}</h1><p role="alert" className="kw-error">{error}</p></section>;
  if (!doc) return <p className="kw-muted" aria-busy="true">Loading…</p>;
  return (
    <article aria-label={doc.title}>
      <Crumbs items={doc.breadcrumbs} onGo={onCrumb} />
      <header className="kw-doc-head">
        <div>
          <h1 className="kw-title">{doc.title}</h1>
          <p className="kw-meta"><span className="kw-chip">{doc.category}</span><span className="kw-chip live">Published v{doc.version}</span>
            <span className="kw-muted">Last updated {when(doc.published_at)}{doc.published_by ? ` by ${doc.published_by}` : ""}</span></p>
          {doc.change_note && <p className="kw-muted tiny">Latest change: {doc.change_note}</p>}
        </div>
        <div className="kw-actions">
          <button className="kw-btn primary" onClick={() => onEdit(doc)}>Make changes</button>
          <button className="kw-btn" onClick={showHistory}>Version history</button>
        </div>
      </header>
      {isAdmin && (
        <label className="kw-move">Folder
          <select value={doc.folder_id ?? ""} onChange={(e) => move(e.target.value)} aria-label="Move to folder">
            <option value="">Knowledge base (top level)</option>
            {tree.folders.map((f) => <option key={f.folder_id} value={f.folder_id}>{f.path}</option>)}
          </select>
        </label>
      )}
      <div className="kw-readonly" aria-label="Published document (read only)"><pre className="kw-doc">{shown ? shown.content : doc.content}</pre></div>
      {shown && <p className="kw-banner warn">Showing the text of v{shown.version}. <button className="kw-btn small" onClick={() => setShown(null)}>Back to the current text</button></p>}
      {history && (
        <section className="kw-history" aria-label="Version history">
          <h2>Version history</h2>
          <ul>
            {history.map((h, i) => (
              <li key={h.version}>
                <b>v{h.version}</b>{h.is_current && <span className="kw-chip live">current</span>}
                <span className="kw-muted"> {when(h.published_at)} · {h.authored_by}{h.published_by ? ` · published by ${h.published_by}` : ""}</span>
                {h.change_note && <span className="kw-sub">{h.change_note}</span>}
                <span className="kw-actions">
                  <button className="kw-btn small" onClick={() => view(h.version)} aria-label={`View v${h.version}`}>View</button>
                  {history[i + 1] && <button className="kw-btn small" onClick={() => compare(history[i + 1].version, h.version)} aria-label={`Compare v${history[i + 1].version} with v${h.version}`}>Compare with previous</button>}
                </span>
              </li>))}
          </ul>
          {cmp && <div><h3>v{cmp.from} → v{cmp.to}</h3><DiffLegend /><DiffView segs={cmp.body} label={`Differences between v${cmp.from} and v${cmp.to}`} /></div>}
        </section>
      )}
    </article>
  );
}

async function fetchCompare(token: string, id: string, from: number, to: number) {
  const r = await fetch(`/api/staff/knowledge/documents/${encodeURIComponent(id)}/compare?from=${from}&to=${to}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!r.ok) throw new ApiError(r.status, "Could not compare those versions.");
  return ((await r.json()) as { diff: { body: import("@/lib/types").DiffSeg[] } }).diff.body;
}
