"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwHistoryRow, KwReadDoc, KwTree } from "@/lib/types";
import { DiffView, DiffLegend } from "./DiffView";
import { Editor } from "./Editor";
import { AiChange } from "./AiChange";
import { MyRequests } from "./Requests";
import { KnowledgeAdmin } from "../KnowledgeAdmin";
import { DeletionRequest } from "./DeletionRequest";
import { goBack, setUrlParams, useUrlParam } from "@/lib/nav";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

const STATUS_FILTERS = ["live", "all", "not_live", "archived"];
const LIVE: Record<string, string> = { published: "Live", draft: "Draft", archived: "Archived", processing: "Processing", ready_for_review: "Ready", failed: "Failed" };

/** The knowledge base as one flat, searchable list of documents (final M6: no folders). Staff read published documents, request changes and deletions; administrators
 *  also see drafts and archived documents and manage versions. The published text is only ever shown read-only; edits happen in the Editor, inside a change request. */
export function KnowledgeWorkspace({ scope }: { scope: "admin" | "staff" }) {
  const { withToken } = useAuth();
  const isAdmin = scope === "admin";
  const [tree, setTree] = useState<KwTree | null>(null);
  // Where the user is lives in the URL (?kb=view&d=document&r=request&cat=&st=): Back / Forward move between views, a reload reopens the same view.
  const [view] = useUrlParam("kb", "list");
  const [docParam] = useUrlParam("d", null);
  const [reqParam] = useUrlParam("r", null);
  const [cat] = useUrlParam("cat", "");
  // Administrators: the status filter defaults to Live (the URL keeps only a non-default choice, so a reload or Back restores it); "all" = every status.
  const [stParam] = useUrlParam("st", null);
  const st = isAdmin ? (stParam && STATUS_FILTERS.includes(stParam) ? stParam : "live") : "";
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

  const categories = useMemo(() => [...new Set((tree?.documents ?? []).map((d) => d.category))].sort(), [tree]);
  const shown = useMemo(() => {
    const all = tree?.documents ?? [];
    const base = hits ? hits.map((h) => all.find((d) => d.doc_id === h.doc_id) ?? { ...h, folder_id: null, status: "published", published_version: null, latest_version: 0, updated_at: null }) : all;
    return base.filter((d) => (!cat || d.category === cat) && (!st || st === "all" || (st === "live" ? d.published_version != null : st === "not_live" ? d.published_version == null && d.status !== "archived" : d.status === st)))
      .sort((a, b) => (hits ? 0 : a.title.localeCompare(b.title)));
  }, [tree, hits, cat, st]);

  // Every view change made here marks the new history entry with the view it was opened FROM ("kb:<view>"); the in-app Back button uses history.back() only
  // for such entries, and otherwise (a document URL opened directly, from a bookmark or after another part of the app pushed an entry) returns to the list.
  const nav = (v: string, d: string | null = null, r: string | null = null) => {
    setNotice(null); setUrlParams({ kb: v === "list" ? null : v, d, r }, "push", `kb:${view ?? "list"}`); setNavOpen(false);
  };
  const goList = () => { setAiState(null); nav("list"); };
  const toListFallback = () => { setAiState(null); setNotice(null); setUrlParams({ kb: null, d: null, r: null }, "replace"); };
  const goDoc = (d: { doc_id: string }) => { setAiState(null); nav("doc", d.doc_id); };
  const goAi = (st: NonNullable<typeof aiState>) => { setAiState(st); nav("ai", null, st.requestId ?? null); };
  const back = () => goBack("kb:", toListFallback);
  const filtered = Boolean(cat || (st && st !== "all") || hits);

  return (
    <div className="kw">
      <div className="kw-shell">
        <aside className={`kw-side${navOpen ? " open" : ""}`} aria-label="Knowledge navigation">
          <div className="kw-search"><input type="search" placeholder="Search names and content" aria-label="Search knowledge" value={q}
            onChange={(e) => { setQ(e.target.value); if (view !== "list") nav("list"); }} /></div>
          <button className="kw-btn small kw-navtoggle" onClick={() => setNavOpen(false)}>Close</button>
          <div className="kw-filters" role="group" aria-label="Filters">
            <label>Category<select value={cat ?? ""} onChange={(e) => setUrlParams({ cat: e.target.value || null, kb: null, d: null })} aria-label="Filter by category">
              <option value="">All categories</option>{categories.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
            {isAdmin && <label>Status<select value={st} onChange={(e) => setUrlParams({ st: e.target.value === "live" ? null : e.target.value, kb: null, d: null })} aria-label="Filter by status">
              <option value="live">Live</option><option value="all">Every status</option><option value="not_live">Not live (drafts)</option><option value="archived">Archived</option></select></label>}
          </div>
          <div className="kw-side-foot">
            <button className={`kw-node${view === "list" ? " sel" : ""}`} onClick={goList}><span aria-hidden="true">≡</span> All documents <span className="kw-count">{tree?.documents.length ?? ""}</span></button>
            <button className={`kw-node${view === "ai" ? " sel" : ""}`} onClick={() => goAi({})}><span aria-hidden="true">✦</span> Create AI-assisted change</button>
            <button className={`kw-node${mine ? " sel" : ""}`} onClick={() => nav("mine")}><span aria-hidden="true">☰</span> My change requests</button>
            {isAdmin && <button className={`kw-node${view === "admin" ? " sel" : ""}`} onClick={() => nav("admin")}><span aria-hidden="true">⚙</span> Drafts &amp; document administration</button>}
          </div>
        </aside>

        <main className="kw-main">
          <button className="kw-btn small kw-navtoggle" onClick={() => setNavOpen(true)}>Search &amp; filters</button>
          {error && <p role="alert" className="kw-error">{error}</p>}
          {notice && <p role="status" className="kw-banner ok">{notice}</p>}
          {!tree && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}

          {tree && ai ? (
            <>
              <BackBar onBack={back} label="Back" />
              <AiChange key={ai.requestId ?? ai.draftRequestId ?? "new"} initial={ai} onClose={back} onSubmitted={(n) => { setAiState(null); nav("mine"); setNotice(n); }} />
            </>
          ) : tree && editing ? (
            <Editor doc={editing} onClose={(n) => { back(); if (n) setNotice(n); }} onSubmitted={(n) => { setEditingDoc(null); nav("mine"); setNotice(n); }}
              onFindRelated={(r) => goAi({ draftRequestId: r.requestId, autoStart: true, description: r.description, referenceDocId: r.docId, referenceTitle: r.title })} />
          ) : tree && mine ? (
            <>
              <BackBar onBack={back} label="Back" />
              <MyRequests onResume={(docId) => nav("doc", docId)} onResumeAi={(id) => goAi({ requestId: id })} />
            </>
          ) : tree && view === "admin" && isAdmin ? (
            <section aria-label="Document administration">
              <BackBar onBack={back} label="Back" />
              <KnowledgeAdmin openDoc={docParam} onChanged={load} />
            </section>
          ) : tree && open ? (
            <Reader key={open} docId={open} tree={tree} scope={scope} onBack={back} onEdit={(d) => { setEditingDoc(d); nav("edit", d.doc_id); }}
              onManage={() => nav("admin", open)} setNotice={(m) => { setNotice(m); }} />
          ) : tree && (
            <section aria-label="Documents">
              <header className="kw-doc-head">
                <div>
                  <h1 className="kw-title">Knowledge base</h1>
                  <p className="kw-muted">{filtered ? `${shown.length} of ${tree.documents.length} document(s)` : `${tree.documents.length} document(s)`}{hits ? ` matching “${q.trim()}”` : ""}</p>
                </div>
                {isAdmin && <div className="kw-actions" role="toolbar" aria-label="Document actions"><button className="kw-btn primary" onClick={() => nav("admin")}>New document</button></div>}
              </header>
              {shown.length === 0 ? <p className="kw-empty">{filtered ? "No documents match the search or filters." : "No documents yet."}</p> : (
                <ul className="kw-doclist">
                  {shown.map((d) => (
                    <li key={d.doc_id}><button className="kw-docrow" onClick={() => goDoc(d)} aria-label={`Open ${d.title}`}>
                      <span className="kw-ico" aria-hidden="true">≡</span>
                      <span className="kw-docrow-main"><b>{d.title}</b><span className="kw-sub">{d.doc_id}</span></span>
                      <span className="kw-chip">{d.category}</span>
                      <span className={`kw-chip${d.published_version ? " live" : ""}`}>{d.published_version ? `v${d.published_version} live` : LIVE[d.status] ?? d.status}</span>
                      <span className="kw-sub kw-docrow-date">{d.updated_at ? when(d.updated_at) : ""}</span>
                    </button></li>))}
                </ul>)}
            </section>
          )}
        </main>
      </div>
    </div>
  );
}

function BackBar({ onBack, label }: { onBack: () => void; label: string }) {
  return <p className="kw-backbar"><button className="kw-btn small" onClick={onBack}><span aria-hidden="true">←</span> {label}</button></p>;
}

function Reader({ docId, tree, scope, onBack, onEdit, onManage, setNotice }: {
  docId: string; tree: KwTree; scope: "admin" | "staff"; onBack: () => void; onEdit: (d: KwReadDoc) => void; onManage: () => void; setNotice: (m: string) => void;
}) {
  const isAdmin = scope === "admin";
  const [deleting, setDeleting] = useState(false);
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

  if (error) return (
    <section><BackBar onBack={onBack} label="Back" /><h1 className="kw-title">{row?.title ?? docId}</h1><p role="alert" className="kw-error">{error}</p>
      {isAdmin && <button className="kw-btn" onClick={onManage}>Manage drafts and versions</button>}</section>);
  if (!doc) return <p className="kw-muted" aria-busy="true">Loading…</p>;
  return (
    <article aria-label={doc.title}>
      <BackBar onBack={onBack} label="All documents" />
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
          <button className="kw-btn danger ghost" onClick={() => setDeleting(true)}>Request deletion</button>
        </div>
      </header>
      {deleting && <DeletionRequest scope={scope} docId={doc.doc_id} title={doc.title} version={doc.version} onClose={() => setDeleting(false)}
        onDone={(m) => { setDeleting(false); setNotice(m); }} />}
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
