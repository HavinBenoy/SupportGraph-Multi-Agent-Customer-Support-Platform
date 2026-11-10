"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwAuditEvent, KwItem, KwRequest, KwStatus } from "@/lib/types";
import { DiffLegend, DiffView } from "./DiffView";
import { PatchLegend, PatchView, baseText } from "./PatchView";
import { ClassChip, DecisionChip, LABEL, Badge, msg, pct, when, STEP_STATUS } from "./ui";

const POLL_MS = 1500;
const BUSY: KwStatus[] = ["processing", "publishing"];
const FILTERS: [string, string][] = [["", "All"], ["submitted", "Awaiting review"], ["under_review", "Under review"], ["needs_revision", "Returned"], ["approved", "Approved"], ["processing", "Processing"],
  ["published", "Published"], ["failed", "Failed"], ["rejected", "Rejected"], ["cancelled", "Cancelled"]];
const ACTION_WORDS: Record<string, string> = { change_request_created: "Request created", change_analysis_started: "AI analysis started", change_analysis_completed: "AI analysis finished", change_analysis_failed: "AI analysis failed",
  change_clarification_requested: "Questions asked", change_clarification_answered: "Questions answered", change_suggestions_generated: "Suggestions generated", change_decision_recorded: "Staff decision",
  change_request_submitted: "Submitted for review", change_request_review_started: "Review started", change_item_edited: "Administrator edited a document", change_item_reviewed: "Administrator reviewed a document",
  change_request_approved: "Approved (nothing published)", change_request_rejected: "Rejected", change_request_returned: "Returned for revision", change_request_cancelled: "Cancelled",
  change_release_started: "Processing and publication started", change_item_processed: "Document processed", change_release_failed: "Publication failed — nothing published", change_release_published: "Published",
  change_release_retried: "Retry started", version_created: "Draft version created", version_published: "Version published", change_request_applied: "Draft versions created" };

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
  const load = useCallback(() => { withToken((t) => api.kwRequests(t, status || undefined)).then((r) => { setRows(r.items); setError(null); }).catch((e) => setError(msg(e))); }, [withToken, status]);
  useEffect(() => { setRows(null); load(); }, [load]);
  const open = (id: string) => withToken((t) => api.kwRequest(t, id)).then((r) => { setSel(r); setError(null); }).catch((e) => setError(msg(e)));

  if (sel) return <div className="kw"><div className="kw-requests-admin"><RequestDetail initial={sel} onBack={() => { setSel(null); load(); }} onChanged={load} /></div></div>;
  return (
    <div className="kw"><div className="kw-requests-admin">
      <section aria-label="Change requests">
        <h1 className="kw-title">Change requests</h1>
        <p className="kw-muted">Review, approve, process and publish — everything happens here.</p>
        <label className="kw-inline">Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>{FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        </label>
        {error && <p role="alert" className="kw-error">{error} <button className="kw-btn small" onClick={load}>Retry</button></p>}
        {rows === null && !error && <p className="kw-muted" aria-busy="true">Loading…</p>}
        {rows?.length === 0 && <p className="kw-muted">No requests with this status.</p>}
        {!!rows?.length && (
          <div className="kw-tablewrap"><table className="kw-table">
            <caption className="sr-only">Change requests</caption>
            <thead><tr><th scope="col">ID</th><th scope="col">Description</th><th scope="col">Submitted by</th><th scope="col">Submitted</th><th scope="col">Docs</th><th scope="col">Accepted</th><th scope="col">Edited</th><th scope="col">Discarded</th><th scope="col">Status</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.request_id}>
                <th scope="row"><button className="kw-btn small" onClick={() => open(r.request_id)} aria-label={`Open request ${r.request_id}`}>{r.request_id.slice(0, 8)}</button></th>
                <td>{r.description}</td><td>{r.submitted_by ?? r.created_by}</td><td>{when(r.submitted_at)}</td><td>{r.counts?.included ?? r.document_count}</td>
                <td>{r.counts?.accepted ?? 0}</td><td>{r.counts?.edited ?? 0}</td><td>{r.counts?.discarded ?? 0}</td><td><Badge s={r.status} /></td>
              </tr>))}</tbody></table></div>)}
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

  return (
    <section aria-label="Request detail">
      <button className="kw-btn small" onClick={onBack}>← All requests</button>
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
            {s === "published" && (
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
                  <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReview(t, req.request_id, "return", note.trim()), "Returned to the author.")}>Return for revision</button>
                  <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReview(t, req.request_id, "reject", note.trim() || undefined), "Rejected. Nothing was changed.")}>Reject</button></>}
                {s === "approved" && !confirm && <button className="kw-btn primary" disabled={busy} onClick={() => setConfirm(true)}>Process and Publish Approved Changes</button>}
                {s === "failed" && <><button className="kw-btn primary" disabled={busy} onClick={() => act((t) => api.kwPublish(t, req.request_id), "Retry started.")}>Retry publication</button>
                  <button className="kw-btn" disabled={busy} onClick={() => act((t) => api.kwReopen(t, req.request_id), "Reopened for review.")}>Reopen for review</button></>}
                {["submitted", "under_review", "approved", "failed"].includes(s) && <button className="kw-btn ghost" disabled={busy} onClick={() => act((t) => api.kwAdminCancel(t, req.request_id), "Cancelled. The record is kept.")}>Cancel request</button>}
              </div>
              {confirm && s === "approved" && (
                <div role="group" aria-label="Confirm publication"><p><b>Publish {inc.length} document(s) to the live knowledge base?</b> Each is processed and checked first; they only go live together, and if anything fails nothing goes live.</p>
                  <div className="kw-actions"><button className="kw-btn primary" disabled={busy} onClick={() => act((t) => api.kwPublish(t, req.request_id), "Processing started.")}>Confirm — process and publish</button><button className="kw-btn" onClick={() => setConfirm(false)}>Not yet</button></div></div>)}
            </div>
          </div>)}

        {tab === "documents" && (
          <div>
            {items.length === 0 && <p className="kw-muted">No documents.</p>}
            {items.map((it) => <AdminDoc key={`${it.doc_id}:${it.row_version}`} req={req} it={it} editable={reviewable} onItem={(n) => setReq((r) => ({ ...r, items: r.items?.map((x) => (x.doc_id === n.doc_id ? n : x)) }))} onDone={reload} />)}
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

function AdminDoc({ req, it, editable, onItem, onDone }: { req: KwRequest; it: KwItem; editable: boolean; onItem: (i: KwItem) => void; onDone: () => void }) {
  const { withToken } = useAuth();
  const [view, setView] = useState<"compare" | "original" | "proposed">("compare");
  const [mode, setMode] = useState<"none" | "edit" | "exclude">("none");
  const [texts, setTexts] = useState<string[]>([]);
  const [full, setFull] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = baseText(it.diff?.body);
  const edits = it.final_edits?.length ? it.final_edits : [];
  const discarded = it.decision === "discarded";
  const call = (fn: (t: string) => Promise<KwItem>) => { setBusy(true); setError(null); withToken(fn).then((n) => { onItem(n); setMode("none"); onDone(); }).catch((e) => setError(msg(e))).finally(() => setBusy(false)); };

  return (
    <article className="kw-card" aria-label={`Document ${it.doc_title}`}>
      <header className="kw-card-head">
        <div><h3>{it.doc_title}</h3><p className="kw-sub">{it.ai?.path ?? ""} · based on v{it.base_version}{it.base_stale ? ` — now v${it.current_published_version}` : ""}</p></div>
        <div>{it.origin === "ai" ? <ClassChip c={it.classification} /> : <span className="kw-chip">Staff edit</span>} {it.origin === "ai" && <span className="kw-chip">Confidence {pct(it.confidence)}</span>} <DecisionChip d={it.origin === "manual" && !it.decision ? "manual" : it.decision} />
          {it.admin_decision && <span className={`kw-chip ${it.admin_decision === "excluded" ? "dc-discarded" : "dc-accepted"}`}>{it.admin_decision === "excluded" ? "✕ Excluded by admin" : "✓ Confirmed"}</span>}
          {it.admin_edited && <span className="kw-chip dc-manual">✎ Edited by admin</span>}</div>
      </header>
      {it.base_stale && <p className="kw-banner warn" role="alert">This document was published again after the change was prepared. It cannot be published as it stands.</p>}
      {it.overlapping_requests?.map((o) => <p key={o.request_id} className="kw-banner warn">Also in request {o.request_id.slice(0, 8)} ({LABEL[o.status]}, by {o.created_by}).</p>)}
      {it.ai?.reason && <p className="kw-why"><b>AI explanation:</b> {it.ai.reason}</p>}
      {discarded && <p className="kw-banner">Staff discarded this suggestion{it.decided_by ? ` (${it.decided_by}, ${when(it.decided_at)})` : ""}. Reason: {it.rationale || "none given"}.</p>}
      {it.admin_note && <p className="kw-muted tiny">Admin note ({it.admin_by}): {it.admin_note}</p>}
      {!discarded && (
        <>
          <div className="kw-seg" role="tablist" aria-label={`View for ${it.doc_title}`}>
            {(["compare", "original", "proposed"] as const).map((v) => <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}>{v === "compare" ? "Compare" : v === "original" ? "Original" : "Proposed"}</button>)}
          </div>
          {view === "compare" && (edits.length ? <><PatchLegend /><PatchView base={base} edits={edits.map((e, i) => ({ ...e, heading: it.ai?.edits?.[i]?.heading }))} label={`Changes to ${it.doc_title}`} /></> : it.diff ? <><DiffLegend /><DiffView segs={it.diff.body} label={`Changes to ${it.doc_title}`} /></> : null)}
          {view === "original" && <pre className="kw-patch" role="region" tabIndex={0} aria-label="Original published text">{base}</pre>}
          {view === "proposed" && <pre className="kw-patch" role="region" tabIndex={0} aria-label="Proposed text">{it.proposed_content}</pre>}
        </>)}
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
          <button className="kw-btn" disabled={busy || it.admin_decision === "confirmed"} onClick={() => call((t) => api.kwItemReview(t, req.request_id, it.doc_id, "confirmed", undefined, it.row_version))}>Confirm</button>
          <button className="kw-btn" disabled={busy} onClick={() => { setTexts(edits.map((e) => e.replacement)); setFull(it.proposed_content ?? ""); setNote(""); setMode("edit"); }}>Edit final wording</button>
          <button className="kw-btn" disabled={busy} onClick={() => { setNote(""); setMode("exclude"); }}>Exclude</button>
        </div>)}
      {editable && it.admin_decision === "excluded" && mode === "none" && <div className="kw-actions"><button className="kw-btn" disabled={busy} onClick={() => call((t) => api.kwItemReview(t, req.request_id, it.doc_id, "confirmed", undefined, it.row_version))}>Include again</button></div>}
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
