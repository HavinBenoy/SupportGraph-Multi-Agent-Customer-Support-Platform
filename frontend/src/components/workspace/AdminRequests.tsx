"use client";
import { useUrlParam } from "@/lib/nav";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwAuditEvent, KwItem, KwRequest, KwStatus } from "@/lib/types";
import { PatchLegend, PatchView, baseText } from "./PatchView";
import { CompareView, applyPatches } from "./Compare";
import { TaskStatus } from "./Decisions";
import { ClassChip, DecisionChip, LABEL, Badge, editedBy, msg, pct, when, STEP_STATUS } from "./ui";

const POLL_MS = 1500;
const BUSY: KwStatus[] = ["processing", "publishing"];
const FILTERS: [string, string][] = [["", "All"], ["submitted", "Awaiting review"], ["under_review", "Under review"], ["needs_revision", "Returned"], ["approved", "Approved"], ["processing", "Processing"],
  ["published", "Published"], ["failed", "Failed"], ["rejected", "Rejected"], ["cancelled", "Cancelled"]];
const ACTION_WORDS: Record<string, string> = { change_request_created: "Request created", change_analysis_started: "AI analysis started", change_analysis_completed: "AI analysis finished", change_analysis_failed: "AI analysis failed",
  change_clarification_requested: "Questions asked", change_clarification_answered: "Questions answered", change_suggestions_generated: "Suggestions generated", change_decision_recorded: "Staff decision",
  change_request_submitted: "Submitted for review", change_request_review_started: "Review started", change_item_edited: "Administrator edited a document", change_item_reviewed: "Administrator reviewed a document",
  change_request_approved: "Approved (nothing published)", change_request_rejected: "Rejected", change_request_returned: "Returned for revision", change_request_cancelled: "Cancelled",
  change_release_started: "Processing and publication started", change_item_processed: "Document processed", change_release_failed: "Publication failed — nothing published", change_release_published: "Published",
  change_release_retried: "Retry started", document_deletion_requested: "Deletion requested", document_deletion_published: "Document archived (deletion approved)", document_archived: "Document archived", version_created: "Draft version created", version_published: "Version published", change_request_applied: "Draft versions created" };

const statusLine = (it: KwItem) => {
  if (!it.included) return "Not part of publication";
  if (it.publish_status === "published") return "Published";
  if (it.publish_status === "failed" || it.process_status === "failed") return "Failed";
  if (it.process_status === "ready") return "Processed — waiting to publish";
  if (it.process_status === "processing") return "Processing";
  return "Waiting";
};

/** Administrator queue and the complete review → approve → process → publish workflow, all on one page. */
export function ChangeRequests() {
  const { withToken } = useAuth();
  const [status, setStatus] = useState("submitted");
  const [rows, setRows] = useState<KwRequest[] | null>(null);
  const [sel, setSel] = useState<KwRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selId, setSelId] = useUrlParam("cr", null);                     // master-detail: the list stays, the selection is in the URL (Back works, also on phones)
  const load = useCallback(() => { withToken((t) => api.kwRequests(t, status || undefined)).then((r) => { setRows(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [withToken, status]);
  useEffect(() => { setRows(null); load(); }, [load]);
  useEffect(() => {
    if (!selId) { setSel(null); return; }
    let live = true;
    withToken((t) => api.kwRequest(t, selId)).then((r) => { if (live) { setSel(r); setError(null); } }).catch((e) => live && setError(msg(e)));
    return () => { live = false; };
  }, [selId, withToken]);

  return (
    <div className="kw"><div className={`kw-requests-admin kw-md${selId ? " has-sel" : ""}`}>
      <section aria-label="Change requests" className="kw-md-list">
        <h1 className="kw-title">Change requests</h1>
        <p className="kw-muted">Review, approve, process and publish — everything happens here.</p>
        <label className="kw-inline">Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>{FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        </label>
        {error && <p role="alert" className="kw-error">{error} <button className="kw-btn small" onClick={load}>Retry</button></p>}
        {rows === null && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}
        {rows?.length === 0 && <p className="kw-muted">No requests with this status.</p>}
        <ul className="kw-list" aria-label="Requests">
          {rows?.map((r) => (
            <li key={r.request_id}>
              <button className={`kw-row${selId === r.request_id ? " sel" : ""}`} aria-current={selId === r.request_id ? "true" : undefined} onClick={() => setSelId(r.request_id)}
                aria-label={`Open request ${r.request_id}`}>
                <span className="kw-row-top"><Badge s={r.status} />{r.analysis?.kind === "deletion" && <span className="kw-chip st-failed">Deletion</span>} <span className="kw-sub">{r.request_id.slice(0, 8)} · {when(r.submitted_at)}</span></span>
                <b>{r.description}</b>
                <span className="kw-sub">by {r.submitted_by ?? r.created_by} · {r.counts?.included ?? r.document_count} document(s) · {r.counts?.accepted ?? 0} accepted · {r.counts?.edited ?? 0} edited · {r.counts?.discarded ?? 0} discarded</span>
              </button>
            </li>))}
        </ul>
      </section>
      <section className="kw-md-detail" aria-label="Request details">
        {!selId && <p className="kw-muted kw-md-hint">Select a request to review it here: status, author, affected documents, proposals and the approval controls.</p>}
        {selId && !sel && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}
        {sel && sel.request_id === selId && (
          <div className="kw-enter" key={sel.request_id}>
            <RequestDetail initial={sel} onBack={() => { setSelId(null); load(); }} onChanged={load} />
          </div>)}
      </section>
    </div></div>
  );
}

function RequestDetail({ initial, onBack, onChanged }: { initial: KwRequest; onBack: () => void; onChanged: () => void }) {
  const { withToken } = useAuth();
  const [req, setReq] = useState<KwRequest>(initial);
  const [tab, setTab] = useState<"overview" | "documents" | "audit">("overview");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [audit, setAudit] = useState<KwAuditEvent[] | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  const reload = useCallback(() => withToken((t) => api.kwRequest(t, initial.request_id)).then(setReq).catch((e) => setError(msg(e))), [withToken, initial.request_id]);
  useEffect(() => {
    if (!BUSY.includes(req.status)) return;
    const h = setInterval(() => { reload(); }, POLL_MS);
    return () => clearInterval(h);
  }, [req.status, reload]);
  useEffect(() => {
    if (tab !== "audit") return;
    withToken((t) => api.kwAudit(t, req.request_id)).then((r) => setAudit(r.items)).catch((e) => setError(msg(e)));
  }, [tab, req.request_id, req.status, withToken]);

  const act = (fn: (t: string) => Promise<unknown>, ok: string) => {
    setBusy(true); setError(null); setInfo(null);
    withToken(fn).then(() => { setInfo(ok); setNote(""); setConfirm(false); return reload(); }).then(() => onChanged()).catch((e) => { setError(msg(e)); reload(); }).finally(() => setBusy(false));
  };
  const s = req.status;
  const items = req.items ?? [];
  const inc = items.filter((i) => i.included);
  const c = req.counts;
  const reviewable = ["submitted", "under_review"].includes(s);
  const progress = ["processing", "publishing", "published", "failed"].includes(s);
  const stale = items.filter((i) => i.included && i.base_stale);
  const idx = STEP_STATUS.indexOf(s);
  const deletion = req.analysis?.kind === "deletion";
  const hasChange = (i: KwItem) => !!(i.stats as { delete?: boolean } | undefined)?.delete || i.origin === "manual" || !!i.ai?.edits?.length || i.decision === "manual" || i.admin_edited;
  const changed = items.filter(hasChange);
  const unchanged = items.filter((i) => !hasChange(i));

  return (
    <section aria-label="Request detail">
      <button className="kw-btn small kw-md-backbtn" onClick={onBack}>← Back to the list</button>
      <h1 className="kw-title">{req.description} </h1>
      <p><Badge s={s} /> <span className="kw-muted">Request {req.request_id.slice(0, 8)} · by {req.submitted_by ?? req.created_by} · {when(req.submitted_at)}</span></p>
      <ol className="kw-steps" aria-label="Progress of this request">
        {[["Submitted", 0], ["In review", 1], ["Approved", 2], ["Processing", 3], ["Publishing", 4], ["Published", 5]].map(([l, n]) => <li key={String(l)} className={idx > (n as number) || (s === "published" && n === 5) ? "done" : idx === n ? "now" : ""}>{idx > (n as number) || (s === "published" && n === 5) ? "✓ " : ""}{l}</li>)}
      </ol>
      {error && <p role="alert" className="kw-error">{error}</p>}
      {info && <p role="status" className="kw-banner ok">{info}</p>}
      <div className="kw-tabs" role="tablist" aria-label="Request sections">
        {(["overview", "documents", "audit"] as const).map((t) => <button key={t} role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls={`panel-${t}`} onClick={() => setTab(t)}>{t === "overview" ? "Overview" : t === "documents" ? `Documents (${items.length})` : "Audit history"}</button>)}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "overview" && (
          <div>
            {deletion && <p className="kw-banner warn" role="note"><b>Deletion request.</b> {req.analysis?.deletion_reason ? `Reason: ${req.analysis.deletion_reason}. ` : ""}
              Approving and publishing archives the document: it stops being used in answers, and its versions and audit history are kept. If it was published again since the request, approval is refused.</p>}
            <p>{c?.included ?? 0} document(s) go forward · <b>{c?.accepted ?? 0}</b> accepted · <b>{c?.edited ?? 0}</b> edited by staff · <b>{c?.discarded ?? 0}</b> discarded by staff{c?.excluded_by_admin ? ` · ${c.excluded_by_admin} excluded by an administrator` : ""}</p>
            {req.review_note && <p className="kw-banner">Reviewer note: {req.review_note}</p>}
            {stale.length > 0 && <p className="kw-banner warn" role="alert">Published again since this was prepared: {stale.map((i) => i.doc_title).join(", ")}. Exclude them or return the request.</p>}
            {items.some((i) => i.overlapping_requests?.length) && <p className="kw-banner warn">Some documents are also in another open request: {items.filter((i) => i.overlapping_requests?.length).map((i) => i.doc_title).join(", ")}.</p>}
            {progress && (
              <div className="kw-tablewrap"><table className="kw-table">
                <caption>Per-document progress{BUSY.includes(s) ? " (updating)" : ""}</caption>
                <thead><tr><th scope="col">Document</th><th scope="col">Base</th><th scope="col">Step</th><th scope="col">Result</th></tr></thead>
                <tbody>{inc.map((i) => (
                  <tr key={i.doc_id}><th scope="row">{i.doc_title}</th><td>v{i.base_version}</td><td>{statusLine(i)}</td>
                    <td>{i.publish_status === "published" ? <>Live as v{i.result_version ?? req.release?.versions?.[i.doc_id]}</> : i.process_error ? <span className="kw-error">{i.process_error}</span> : "—"}</td></tr>))}</tbody></table></div>)}
            {BUSY.includes(s) && <div className="kw-progress-wrap" role="status"><p><b>{s === "processing" ? "Preparing every document" : "Publishing all documents together"}…</b></p>
              <div className="kw-progress" role="progressbar" aria-label="Release progress" aria-valuemin={0} aria-valuemax={inc.length} aria-valuenow={inc.filter((i) => i.publish_status === "published" || i.process_status === "ready").length}>
                <span style={{ width: `${inc.length ? Math.round((inc.filter((i) => i.publish_status === "published" || i.process_status === "ready").length / inc.length) * 100) : 5}%` }} /></div>
              <p className="kw-muted tiny">Nothing is live until every document is ready and they are published in a single step.</p></div>}
            {s === "failed" && req.failure && (
              <div className="kw-failbox" role="alert"><p><b>Publication failed at “{req.failure.stage}”. Nothing was published; the live knowledge is unchanged.</b></p><p>{req.failure.message}</p>
                {!!req.failure.documents?.length && <p className="kw-muted">Affected: {req.failure.documents.join(", ")}</p>}</div>)}
            {s === "published" && deletion && <p className="kw-banner ok" role="status"><b>Deleted (archived)</b> by {req.published_by} on {when(req.published_at)}. It no longer answers questions; its versions and audit history are kept.</p>}
            {s === "published" && !deletion && (
              <div className="kw-banner ok" role="status"><p><b>Published</b> by {req.published_by} on {when(req.published_at)}. Every document went live together.</p>
                <ul>{Object.entries(req.release?.versions ?? {}).map(([d, v]) => <li key={d}>{items.find((i) => i.doc_id === d)?.doc_title ?? d}: v{req.release?.previous?.[d] ?? "—"} → <b>v{v}</b></li>)}</ul>
                <Verification req={req} onRun={() => act((t) => api.kwVerify(t, req.request_id), "Checked the live knowledge again.")} busy={busy} /></div>)}
            {s === "needs_revision" && <p className="kw-banner">Returned to {req.created_by}: {req.return_note}</p>}
            {s === "approved" && <p className="kw-banner">Approved. <b>Nothing has been published yet.</b> Use “Process and Publish Approved Changes” when you are ready.</p>}

            <div className="kw-ask">
              {(reviewable) && <label>Note (required to return, optional otherwise)<input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>}
              <div className="kw-actions">
                {s === "submitted" && <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwStartReview(t, req.request_id), "Review started.")}>Start review</button>}
                {reviewable && <>
                  <button className="kw-btn primary" disabled={busy || inc.length === 0} onClick={() => act((t) => api.kwReview(t, req.request_id, "approve", note.trim() || undefined), "Approved. Nothing has been published.")}>Approve</button>
                  {!deletion && <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReview(t, req.request_id, "return", note.trim()), "Returned to the author.")}>Return for revision</button>}
                  <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReview(t, req.request_id, "reject", note.trim() || undefined), "Rejected. Nothing was changed.")}>Reject</button></>}
                {s === "approved" && !confirm && <button className="kw-btn primary" disabled={busy} onClick={() => setConfirm(true)}>{deletion ? "Archive the document now" : "Process and Publish Approved Changes"}</button>}
                {s === "failed" && <><button className="kw-btn primary" disabled={busy} onClick={() => act((t) => api.kwPublish(t, req.request_id), "Retry started.")}>Retry publication</button>
                  <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReopen(t, req.request_id), "Reopened for review.")}>Reopen for review</button></>}
                {["submitted", "under_review", "approved", "failed"].includes(s) && <button className="kw-btn ghost" disabled={busy} onClick={() => act((t) => api.kwAdminCancel(t, req.request_id), "Cancelled. The record is kept.")}>Cancel request</button>}
              </div>
              {confirm && s === "approved" && (
                <div role="group" aria-label="Confirm publication">{deletion ? <p><b>Archive {inc.map((i) => `“${i.doc_title}”`).join(", ")}?</b> It stops being used in answers at once. Every version and the audit history are kept.</p>
                  : <p><b>Publish {inc.length} document(s) to the live knowledge base?</b> Each is processed and checked first; they only go live together, and if anything fails nothing goes live.</p>}
                  <div className="kw-actions"><button className="kw-btn primary" disabled={busy} onClick={() => act((t) => api.kwPublish(t, req.request_id), deletion ? "Archived. The document no longer answers questions; its history is kept." : "Processing started.")}>{deletion ? "Confirm — archive" : "Confirm — process and publish"}</button><button className="kw-btn" onClick={() => setConfirm(false)}>Not yet</button></div></div>)}
            </div>
          </div>)}

        {tab === "documents" && (
          <div>
            <TaskStatus req={req} />
            {changed.length === 0 && <p className="kw-muted">No documents.</p>}
            <div className="kw-acc-list">
              {changed.map((it) => <AdminDoc key={`${it.doc_id}:${it.row_version}`} req={req} it={it} editable={reviewable} open={openDoc === it.doc_id}
                onToggle={() => setOpenDoc((o) => (o === it.doc_id ? null : it.doc_id))}
                onItem={(n) => setReq((r) => ({ ...r, items: r.items?.map((x) => (x.doc_id === n.doc_id ? n : x)) }))} onDone={reload} />)}
            </div>
            {(unchanged.length > 0 || !!req.analysis?.summary?.not_relevant?.length) && (
              <details className="kw-details kw-diag"><summary>Examined, no change proposed ({unchanged.length + (req.analysis?.summary?.not_relevant?.length ?? 0)})</summary>
                <ul>{unchanged.map((it) => <li key={it.doc_id}><b>{it.doc_title}</b> — <span className="kw-muted">{it.ai?.reason ?? "No change needed."}</span></li>)}
                  {req.analysis?.summary?.not_relevant?.map((n) => <li key={n.doc_id}>{n.title} — <span className="kw-muted">{n.reason}</span></li>)}</ul></details>)}
          </div>)}

        {tab === "audit" && (
          <div>{audit === null ? <p className="kw-muted" aria-busy="true">Loading…</p> : audit.length === 0 ? <p className="kw-muted">No events.</p> : (
            <ol className="kw-events" aria-label="Audit history">{audit.map((e) => (
              <li key={e.id}><b>{ACTION_WORDS[e.action] ?? e.action}</b> <span className="kw-muted">— {e.actor} · {when(e.at)}{e.doc_id ? ` · ${e.doc_id}` : ""}{e.version ? ` · v${e.version}` : ""}</span>
                {e.summary && Object.keys(e.summary).length > 0 && <span className="kw-sub">{Object.entries(e.summary).map(([k, v]) => `${k}: ${v}`).join(" · ")}</span>}</li>))}</ol>)}</div>)}
      </div>
    </section>
  );
}

function AdminDoc({ req, it, editable, open, onToggle, onItem, onDone }: { req: KwRequest; it: KwItem; editable: boolean; open: boolean; onToggle: () => void; onItem: (i: KwItem) => void; onDone: () => void }) {
  const { withToken, session } = useAuth();
  const me = session?.username ?? null;
  const [mode, setMode] = useState<"none" | "edit" | "exclude">("none");
  const [texts, setTexts] = useState<string[]>([]);
  const [full, setFull] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = baseText(it.diff?.body);
  const edits = it.final_edits?.length ? it.final_edits : [];
  const discarded = it.decision === "discarded";
  const deletion = !!(it.stats as { delete?: boolean } | undefined)?.delete;
  const call = (fn: (t: string) => Promise<KwItem>) => { setBusy(true); setError(null); withToken(fn).then((n) => { onItem(n); setMode("none"); onDone(); }).catch((e) => setError(msg(e))).finally(() => setBusy(false)); };
  const preview = mode === "edit" ? (edits.length ? applyPatches(base, edits.map((e, i) => ({ ...e, replacement: texts[i] ?? e.replacement }))) : full) : undefined;
  const needs = editable && !discarded && it.included !== false && !it.admin_decision;
  const bodyId = `adoc-${it.doc_id}`;
  const trail = (it.ai?.tasks ?? []).filter((t) => t.edits > 0).map((t) => `Task ${t.task}`).join(", ");

  return (
    <article className={`kw-acc${open ? " open" : ""}${needs ? " needs" : ""}`} aria-label={`Document ${it.doc_title}`}>
      <button className="kw-acc-head" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
        <span className="kw-acc-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="kw-acc-main"><b>{it.doc_title}</b>
          <span className="kw-sub">{deletion ? "Deletion: the document will be archived" : discarded ? `Discarded by staff${it.rationale ? `: ${it.rationale}` : ""}` : trail || (it.ai?.reason ? (it.ai.reason.length > 110 ? `${it.ai.reason.slice(0, 110)}…` : it.ai.reason) : "Staff edit")}</span></span>
        <span className="kw-acc-chips">
          {deletion ? <span className="kw-chip st-failed">Delete</span> : it.origin === "ai" ? <ClassChip c={it.classification} /> : <span className="kw-chip">Staff edit</span>}{" "}
          {!deletion && <DecisionChip d={it.origin === "manual" && !it.decision ? "manual" : it.decision} editor={it.staff_edited_by} me={me} />}
          {it.admin_decision && <span className={`kw-chip ${it.admin_decision === "excluded" ? "dc-discarded" : "dc-accepted"}`}>{it.admin_decision === "excluded" ? "✕ Excluded by admin" : "✓ Confirmed"}</span>}
          {it.admin_edited && <span className="kw-chip dc-manual">✎ Final wording edited by {editedBy(it.admin_edited_by, me) ?? "an administrator"}</span>}
          {it.base_stale && <span className="kw-chip st-failed">Out of date</span>}
          {needs && <span className="kw-chip kw-need">Action required</span>}</span>
      </button>
      {open && (
        <div className="kw-acc-body" id={bodyId}>
          <p className="kw-sub">{it.ai?.path ?? ""} · based on v{it.base_version}{it.base_stale ? ` — now v${it.current_published_version}` : ""}{it.origin === "ai" ? ` · confidence ${pct(it.confidence)}` : ""}</p>
          <p className="kw-sub kw-attrib">Requested by {req.created_by} · {it.origin === "ai" ? "text proposed by the AI" : "text written by the requester"}{it.last_edited_by ? ` · last edited by ${editedBy(it.last_edited_by, me)}${it.last_edited_at ? ` (${when(it.last_edited_at)})` : ""}` : ""}</p>
          {it.base_stale && <p className="kw-banner warn" role="alert">This document was published again after the change was prepared. It cannot be published as it stands.</p>}
          {it.overlapping_requests?.map((o) => <p key={o.request_id} className="kw-banner warn">Also in request {o.request_id.slice(0, 8)} ({LABEL[o.status]}, by {o.created_by}).</p>)}
          {it.ai?.reason && <p className="kw-why"><b>AI explanation:</b> {it.ai.reason}</p>}
          {!!it.ai?.tasks?.length && (req.analysis?.intents?.length ?? 0) > 1 && (
            <ul className="kw-unsure" aria-label="Tasks applied to this document">{it.ai.tasks.map((t) => <li key={t.task}>Task {t.task}: {t.edits ? `${t.edits} change(s)` : "no change"} — {t.reason}</li>)}</ul>)}
          {discarded && <p className="kw-banner">Staff discarded this suggestion{it.decided_by ? ` (${it.decided_by}, ${when(it.decided_at)})` : ""}. Reason: {it.rationale || "none given"}.</p>}
          {it.admin_note && <p className="kw-muted tiny">Admin note ({it.admin_by}): {it.admin_note}</p>}
          {deletion ? (
            <div className="kw-banner warn"><p><b>Approving and publishing this request archives “{it.doc_title}”.</b> It stops being used in answers; every version and the audit history are kept, and it can be unarchived later.</p>
              <p className="kw-muted tiny">Reason given: {it.rationale}</p>
              <details className="kw-details"><summary>Text that will be removed from answers</summary><pre className="kw-cmp-text" tabIndex={0} aria-label={`Published text of ${it.doc_title}`}>{base}</pre></details></div>
          ) : !discarded && <CompareView it={it} preview={preview} />}
          {!deletion && !discarded && edits.length > 0 && mode === "none" && (
            <details className="kw-details"><summary>Passages and replacements</summary><PatchLegend />
              <PatchView base={base} edits={edits.map((e, i) => ({ ...e, heading: it.ai?.edits?.[i]?.heading }))} label={`Changes to ${it.doc_title}`} /></details>)}
          {mode === "edit" && (
            <div className="kw-ask">
              {edits.length ? edits.map((e, i) => <label key={i}>Final wording {edits.length > 1 ? i + 1 : ""}<textarea rows={3} value={texts[i] ?? ""} onChange={(ev) => setTexts((p) => p.map((x, j) => (j === i ? ev.target.value : x)))} /></label>)
                : <label>Final document text<textarea rows={10} value={full} onChange={(ev) => setFull(ev.target.value)} /></label>}
              <label>Why (optional)<input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
              <div className="kw-actions"><button className="kw-btn primary" disabled={busy} onClick={() => call((t) => api.kwAdminEdit(t, req.request_id, it.doc_id, { ...(edits.length ? { replacements: texts.map((replacement, index) => ({ index, replacement })) } : { content: full }), note: note.trim() || undefined, expected_row_version: it.row_version }))}>Save final edit</button><button className="kw-btn" onClick={() => setMode("none")}>Cancel</button></div>
            </div>)}
          {mode === "exclude" && (
            <div className="kw-ask"><label>Reason for excluding (required)<input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
              <div className="kw-actions"><button className="kw-btn primary" disabled={busy || !note.trim()} onClick={() => call((t) => api.kwItemReview(t, req.request_id, it.doc_id, "excluded", note.trim(), it.row_version))}>Exclude from publication</button><button className="kw-btn" onClick={() => setMode("none")}>Cancel</button></div></div>)}
          {error && <p role="alert" className="kw-error">{error}</p>}
          {editable && !discarded && it.included !== false && mode === "none" && (
            <div className="kw-actions" role="group" aria-label={`Actions for ${it.doc_title}`}>
              {it.admin_decision === "confirmed" ? <span className="kw-done" role="status">✓ Confirmed for publication</span>
                : <button className="kw-btn" disabled={busy} onClick={() => call((t) => api.kwItemReview(t, req.request_id, it.doc_id, "confirmed", undefined, it.row_version))}>Confirm</button>}
              {!deletion && <button className="kw-btn" disabled={busy} onClick={() => { setTexts(edits.map((e) => e.replacement)); setFull(it.proposed_content ?? ""); setNote(""); setMode("edit"); }}>Edit final wording</button>}
              <button className="kw-btn" disabled={busy} onClick={() => { setNote(""); setMode("exclude"); }}>Exclude</button>
            </div>)}
          {editable && it.admin_decision === "excluded" && mode === "none" && <div className="kw-actions"><button className="kw-btn" disabled={busy} onClick={() => call((t) => api.kwItemReview(t, req.request_id, it.doc_id, "confirmed", undefined, it.row_version))}>Include again</button></div>}
        </div>)}
    </article>
  );
}


/** After publication: what a search of the live knowledge still finds that looks out of date. Advisory only; it never changes anything. */
function Verification({ req, onRun, busy }: { req: KwRequest; onRun: () => void; busy: boolean }) {
  const v = req.release?.verification;
  return (
    <section aria-label="Check of the live knowledge" className="kw-verify">
      {!v ? <p className="kw-muted tiny">No follow-up check has been recorded for this release.</p>
        : v.mode === "statements" ? (
          <><p role="status"><b>Follow-up check (not a value replacement): {v.remaining_total === 0 && !v.edits_missing ? "the new text is live and no removed statement was found elsewhere" : `${v.remaining_total} removed or rewritten statement${v.remaining_total === 1 ? "" : "s"} still appear${v.remaining_total === 1 ? "s" : ""} elsewhere${v.edits_missing ? `; ${v.edits_missing} edit(s) are not in the live text` : ""}`}</b> ({v.documents_scanned} live documents, {when(v.checked_at)}).</p>
            <p className="kw-muted tiny">{v.note ?? "This kind of change cannot be fully verified automatically: please read the updated documents."}</p>
            {v.remaining.length > 0 && <ul>{v.remaining.map((r, i) => <li key={i}><b>{r.title}</b> — “{r.text.length > 160 ? `${r.text.slice(0, 160)}…` : r.text}” <span className="kw-muted tiny">{r.evidence}</span></li>)}</ul>}</>)
        : v.clean ? <p role="status"><b>Follow-up check: nothing outdated found</b> in {v.documents_scanned} live documents ({when(v.checked_at)}).</p>
        : (<><p role="status"><b>Follow-up check: {v.remaining_total} passage{v.remaining_total === 1 ? "" : "s"} may still be out of date</b>{v.unreviewed === 0 ? " — all were left unchanged by a reviewer" : ""} ({when(v.checked_at)}). Nothing was changed automatically.</p>
          <ul>{v.remaining.map((r, i) => (
            <li key={i}><b>{r.title}</b>{r.heading ? <> · {r.heading}</> : null} — “{r.text.length > 160 ? `${r.text.slice(0, 160)}…` : r.text}”
              <span className="kw-muted tiny"> {r.kind === "outdated" ? "Still shows the old value." : "Possibly related."}{r.left_by_reviewer ? " A reviewer chose to leave this unchanged." : ""}{r.evidence ? ` ${r.evidence}` : ""}</span></li>))}</ul></>)}
      <div className="kw-actions"><button className="kw-btn" disabled={busy} onClick={onRun}>Check again</button></div>
    </section>
  );
}
