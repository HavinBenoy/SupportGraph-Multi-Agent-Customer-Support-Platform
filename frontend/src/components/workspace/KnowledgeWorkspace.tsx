"use client";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwFolder, KwHistoryRow, KwReadDoc, KwTree, KwTreeDoc } from "@/lib/types";
import { DiffView, DiffLegend } from "./DiffView";
import { Editor } from "./Editor";
import { AiChange } from "./AiChange";
import { MyRequests } from "./Requests";
import { KnowledgeAdmin } from "../KnowledgeAdmin";
import { setUrlParams, useUrlParam } from "@/lib/nav";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/** Folder-and-file view of the knowledge base. Staff read published documents and start change requests; administrators also organise folders.
 *  The published text is only ever shown read-only; edits happen in the Editor, inside a change request. */
export function KnowledgeWorkspace({ scope }: { scope: "admin" | "staff" }) {
  const { withToken } = useAuth();
  const isAdmin = scope === "admin";
  const [tree, setTree] = useState<KwTree | null>(null);
  // Where the user is lives in the URL (?kb=view&f=folder&d=document&r=request): Back / Forward move between views, a reload reopens the same view.
  const [view] = useUrlParam("kb", "folder");
  const [cwd] = useUrlParam("f", null);                              // folder id; null = root
  const [docParam] = useUrlParam("d", null);
  const [reqParam] = useUrlParam("r", null);
  const open = view === "doc" || view === "edit" ? docParam : null;
  const mine = view === "mine";
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ doc_id: string; title: string; category: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingDoc, setEditingDoc] = useState<KwReadDoc | null>(null);
  const editing = view === "edit" && editingDoc && editingDoc.doc_id === docParam ? editingDoc : null;
  const [navOpen, setNavOpen] = useState(false);
  const [aiState, setAiState] = useState<{ requestId?: string; draftRequestId?: string; autoStart?: boolean; referenceDocId?: string; referenceTitle?: string; description?: string } | null>(null);
  const ai = view === "ai" ? (aiState ?? (reqParam ? { requestId: reqParam } : {})) : null;

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

  const nav = (v: string, f: string | null, d: string | null = null, r: string | null = null) => { setNotice(null); setUrlParams({ kb: v === "folder" ? null : v, f, d, r }); setNavOpen(false); };
  const goFolder = (id: string | null) => { setAiState(null); nav("folder", id); };
  const goDoc = (d: { doc_id: string }, folder?: string | null) => { setAiState(null); setQ(""); nav("doc", folder !== undefined ? folder : cwd, d.doc_id); };
  const goAi = (st: NonNullable<typeof aiState>) => { setAiState(st); nav("ai", cwd, null, st.requestId ?? null); };
  const back = () => { if (window.history.length > 1) window.history.back(); else goFolder(null); };

  const renderFolders = (parent: string | null, depth: number): React.ReactNode =>
    (byParent.get(parent) ?? []).map((f) => (
      <li key={f.folder_id}>
        <button className={`kw-node${cwd === f.folder_id && view === "folder" ? " sel" : ""}`} style={{ paddingLeft: 10 + depth * 14 }} onClick={() => goFolder(f.folder_id)} aria-label={`Folder ${f.name}`}>
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
                <li><button className={`kw-node${cwd === null && view === "folder" ? " sel" : ""}`} onClick={() => goFolder(null)} aria-label="Knowledge base (top level)"><span aria-hidden="true">◆</span> Knowledge base <span className="kw-count">{tree?.root_document_count ?? ""}</span></button></li>
                {renderFolders(null, 0)}
              </ul>
            </nav>
          )}
          <div className="kw-side-foot">
            <button className={`kw-node${view === "ai" ? " sel" : ""}`} onClick={() => goAi({})}><span aria-hidden="true">✦</span> Create AI-assisted change</button>
            <button className={`kw-node${mine ? " sel" : ""}`} onClick={() => nav("mine", cwd)}><span aria-hidden="true">☰</span> My change requests</button>
            {isAdmin && <button className={`kw-node${view === "admin" ? " sel" : ""}`} onClick={() => nav("admin", cwd)}><span aria-hidden="true">⚙</span> Drafts &amp; document administration</button>}
          </div>
        </aside>

        <main className="kw-main">
          <button className="kw-btn small kw-navtoggle" onClick={() => setNavOpen(true)}>Folders</button>
          {error && <p role="alert" className="kw-error">{error}</p>}
          {notice && <p role="status" className="kw-banner ok">{notice}</p>}
          {!tree && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}

          {tree && ai ? (
            <>
              <BackBar onBack={back} label="Back" />
              <AiChange key={ai.requestId ?? ai.draftRequestId ?? "new"} initial={ai} onClose={back} onSubmitted={(n) => { setAiState(null); nav("mine", cwd); setNotice(n); }} />
            </>
          ) : tree && editing ? (
            <Editor doc={editing} onClose={(n) => { back(); if (n) setNotice(n); }} onSubmitted={(n) => { setEditingDoc(null); nav("mine", cwd); setNotice(n); }}
              onFindRelated={(r) => goAi({ draftRequestId: r.requestId, autoStart: true, description: r.description, referenceDocId: r.docId, referenceTitle: r.title })} />
          ) : tree && mine ? (
            <>
              <BackBar onBack={back} label="Back" />
              <MyRequests onResume={(docId) => nav("doc", cwd, docId)} onResumeAi={(id) => goAi({ requestId: id })} />
            </>
          ) : tree && view === "admin" && isAdmin ? (
            <section aria-label="Document administration">
              <Crumbs items={[{ id: "admin", name: "Drafts & document administration" }]} onGo={(id) => (id === "admin" ? undefined : goFolder(id))} onBack={back} />
              <KnowledgeAdmin folderId={cwd} folderName={cwd ? folderMap.get(cwd)?.name ?? null : null} openDoc={docParam} onChanged={load}
                onPlaced={(m) => { setNotice(m); load(); }} />
            </section>
          ) : tree && open ? (
            <Reader key={open} docId={open} tree={tree} isAdmin={isAdmin} onCrumb={goFolder} onBack={back} onEdit={(d) => { setEditingDoc(d); nav("edit", cwd, d.doc_id); }}
              onManage={() => nav("admin", cwd, open)} onChanged={() => load()} setNotice={setNotice} />
          ) : tree && (
            <FolderPage tree={tree} cwd={cwd} crumbs={crumbs} docs={docsHere} subfolders={byParent.get(cwd) ?? []} isAdmin={isAdmin} onFolder={goFolder} onDoc={(d) => goDoc(d)}
              onBack={cwd ? () => goFolder(folderMap.get(cwd)?.parent_id ?? null) : undefined} onNewDocument={() => nav("admin", cwd)}
              onChanged={(m, goTo) => { if (goTo !== undefined) goFolder(goTo); if (m) setNotice(m); load(); }} />
          )}
        </main>
      </div>
    </div>
  );
}

function BackBar({ onBack, label }: { onBack: () => void; label: string }) {
  return <p className="kw-backbar"><button className="kw-btn small" onClick={onBack}><span aria-hidden="true">←</span> {label}</button></p>;
}

function Crumbs({ items, onGo, onBack }: { items: { id: string | null; name: string }[]; onGo: (id: string | null) => void; onBack?: () => void }) {
  return (
    <div className="kw-crumbbar">
      {onBack && <button className="kw-btn small" onClick={onBack} aria-label="Back"><span aria-hidden="true">←</span> Back</button>}
      <nav aria-label="Breadcrumb" className="kw-crumbs">
        <button onClick={() => onGo(null)}>Knowledge base</button>
        {items.map((c, i) => <span key={c.id}> / {i === items.length - 1 ? <b aria-current="page">{c.name}</b> : <button onClick={() => onGo(c.id)}>{c.name}</button>}</span>)}
      </nav>
    </div>
  );
}

function FolderPage({ tree, cwd, crumbs, docs, subfolders, isAdmin, onFolder, onDoc, onBack, onNewDocument, onChanged }: {
  tree: KwTree; cwd: string | null; crumbs: KwFolder[]; docs: KwTreeDoc[]; subfolders: KwFolder[]; isAdmin: boolean;
  onFolder: (id: string | null) => void; onDoc: (d: KwTreeDoc) => void; onBack?: () => void; onNewDocument: () => void; onChanged: (m?: string, goTo?: string | null) => void;
}) {
  const { withToken } = useAuth();
  const [mode, setMode] = useState<null | "new" | "rename" | "move" | "delete">(null);
  const [name, setName] = useState("");
  const [dest, setDest] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const here = crumbs[crumbs.length - 1];
  useEffect(() => { setMode(null); setError(null); }, [here?.folder_id]);
  const inside = useMemo(() => {                                         // this folder and everything below it: not a valid destination
    const out = new Set<string>(); if (!here) return out;
    const walk = (id: string) => { out.add(id); for (const f of tree.folders) if (f.parent_id === id) walk(f.folder_id); };
    walk(here.folder_id); return out;
  }, [here, tree.folders]);
  const childCount = here ? tree.folders.filter((f) => f.parent_id === here.folder_id).length : 0;
  const docCount = here ? tree.documents.filter((d) => d.folder_id === here.folder_id).length : 0;
  const run = (p: (t: string) => Promise<unknown>, done: string, goTo?: string | null) => {
    setBusy(true);
    withToken(p).then(() => { setError(null); setMode(null); setName(""); onChanged(done, goTo); }).catch((er) => setError(msg(er))).finally(() => setBusy(false));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mode === "new" && name.trim()) run((t) => api.kwCreateFolder(t, name.trim(), cwd), `Folder “${name.trim()}” created${here ? ` in “${here.name}”` : " at the top level"}.`);
    if (mode === "rename" && here && name.trim()) run((t) => api.kwRenameFolder(t, here.folder_id, name.trim(), here.row_version), "Folder renamed.");
    if (mode === "move" && here) run((t) => api.kwMoveFolder(t, here.folder_id, dest || null, here.row_version), "Folder moved.");
    if (mode === "delete" && here) run((t) => api.kwDeleteFolder(t, here.folder_id, here.row_version), `Folder “${here.name}” deleted.`, here.parent_id);
  };
  return (
    <section aria-label="Folder contents">
      <Crumbs items={crumbs.map((c) => ({ id: c.folder_id, name: c.name }))} onGo={onFolder} onBack={onBack} />
      <header className="kw-doc-head">
        <div>
          <h1 className="kw-title">{here ? here.name : "Knowledge base"}</h1>
          <p className="kw-muted">{subfolders.length} folder(s) · {docs.length} document(s) here</p>
        </div>
        {isAdmin && (
          <div className="kw-actions" role="toolbar" aria-label="Folder actions">
            <button className="kw-btn primary" onClick={onNewDocument}>New document here</button>
            <button className="kw-btn" onClick={() => { setMode("new"); setName(""); }}>{here ? "New sub-folder" : "New folder"}</button>
            {here && <button className="kw-btn" onClick={() => { setMode("rename"); setName(here.name); }}>Rename</button>}
            {here && <button className="kw-btn" onClick={() => { setMode("move"); setDest(here.parent_id ?? ""); }}>Move</button>}
            {here && <button className="kw-btn danger" onClick={() => setMode("delete")}>Delete</button>}
          </div>
        )}
      </header>
      {error && <p role="alert" className="kw-error">{error}</p>}
      {isAdmin && mode && mode !== "delete" && (
        <form onSubmit={submit} className="kw-inline kw-panel" aria-label={mode === "new" ? "New folder" : mode === "rename" ? "Rename folder" : "Move folder"}>
          {mode === "move" ? (
            <label>Move “{here?.name}” into
              <select value={dest} onChange={(e) => setDest(e.target.value)} aria-label="Destination folder">
                <option value="">Knowledge base (top level)</option>
                {tree.folders.filter((f) => !inside.has(f.folder_id)).map((f) => <option key={f.folder_id} value={f.folder_id}>{f.path}</option>)}
              </select></label>
          ) : (
            <label>{mode === "new" ? (here ? `New folder inside “${here.name}”` : "New top-level folder") : "New name"}
              <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Course fees" autoFocus /></label>
          )}
          <button className="kw-btn primary" disabled={busy || (mode !== "move" && (!name.trim() || (mode === "rename" && name.trim() === here?.name)))}>
            {mode === "new" ? "Create folder" : mode === "rename" ? "Rename" : "Move here"}</button>
          <button type="button" className="kw-btn" onClick={() => setMode(null)}>Cancel</button>
        </form>
      )}
      {isAdmin && mode === "delete" && here && (
        <div className="dialog-back" role="presentation" onClick={() => setMode(null)}>
          <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="del-h" onClick={(e) => e.stopPropagation()}>
            <h2 id="del-h">Delete “{here.name}”?</h2>
            {childCount + docCount > 0 ? (
              <p className="muted">This folder still contains {childCount} folder(s) and {docCount} document(s). Move them elsewhere first: a folder is deleted only when it is empty, so nothing is lost by accident.</p>
            ) : <p className="muted">The folder is empty. Deleting it does not touch any document or version, and it is recorded in the audit log.</p>}
            {error && <p role="alert" className="kw-error">{error}</p>}
            <div className="actions">
              <button className="kw-btn" onClick={() => setMode(null)} autoFocus>Cancel</button>
              <button className="kw-btn danger" disabled={busy || childCount + docCount > 0} onClick={submit as unknown as () => void}>Delete folder</button>
            </div>
          </div>
        </div>
      )}
      <ul className="kw-grid">
        {subfolders.map((f) => <li key={f.folder_id}><button className="kw-tile" onClick={() => onFolder(f.folder_id)} aria-label={`Open folder ${f.name}`}><span className="kw-ico" aria-hidden="true">▸</span><b>{f.name}</b><span className="kw-sub">{f.document_count} document(s)</span></button></li>)}
        {docs.map((d) => (
          <li key={d.doc_id}><button className="kw-tile doc" onClick={() => onDoc(d)} aria-label={`Open ${d.title}`}>
            <span className="kw-ico" aria-hidden="true">≡</span><b>{d.title}</b>
            <span className="kw-sub">{d.category} · {d.published_version ? `v${d.published_version} live` : "not live"}</span></button></li>))}
      </ul>
      {subfolders.length + docs.length === 0 && <p className="kw-empty">This folder is empty.{isAdmin ? " Create a sub-folder or a new document with the buttons above, or move documents here from their page." : ""}</p>}
    </section>
  );
}

function Reader({ docId, tree, isAdmin, onCrumb, onBack, onEdit, onManage, onChanged, setNotice }: {
  docId: string; tree: KwTree; isAdmin: boolean; onCrumb: (id: string | null) => void; onBack: () => void; onEdit: (d: KwReadDoc) => void; onManage: () => void; onChanged: () => void;
  setNotice: (m: string) => void;
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

  if (error) return (
    <section><Crumbs items={[]} onGo={onCrumb} onBack={onBack} /><h1 className="kw-title">{row?.title ?? docId}</h1><p role="alert" className="kw-error">{error}</p>
      {isAdmin && <button className="kw-btn" onClick={onManage}>Manage drafts and versions</button>}</section>);
  if (!doc) return <p className="kw-muted" aria-busy="true">Loading…</p>;
  return (
    <article aria-label={doc.title}>
      <Crumbs items={doc.breadcrumbs} onGo={onCrumb} onBack={onBack} />
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
          {isAdmin && <button className="kw-btn" onClick={onManage}>Manage versions</button>}
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
