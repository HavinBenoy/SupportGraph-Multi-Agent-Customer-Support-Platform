"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwItem, KwPatch, KwRequest } from "@/lib/types";
import { PatchLegend, PatchView, baseText } from "./PatchView";
import { CompareView, applyPatches } from "./Compare";
import { ProcessingLog } from "./ProcessingLog";
import { CHANGE_LABEL, ClassChip, DecisionChip, JUDGE_LABEL, KIND_LABEL, msg, pct, when } from "./ui";

/** The edits to show for an item: the decided patch if there is one, otherwise the AI's suggestion. */
export const editsOf = (it: KwItem): (KwPatch & { heading?: string })[] =>
  it.decision && it.decision !== "discarded" && it.final_edits?.length ? it.final_edits : (it.ai?.edits ?? []);

const taskLine = (it: KwItem, req: KwRequest) => {
  const trail = it.ai?.tasks ?? [];
  const tasks = req.analysis?.intents ?? [];
  if (!trail.length || tasks.length < 2) return null;
  return trail.filter((t) => t.edits > 0 || t.classification !== "related_no_change").map((t) => {
    const task = tasks.find((x) => x.intent === t.task);
    return `Task ${t.task}${task ? `: ${task.text}` : ""}`;
  }).join(" · ");
};

/** Per-task outcome of a multi-task request (decomposed into one task per requested change). Every task is listed with its status, so nothing is silently dropped. */
export function TaskStatus({ req }: { req: KwRequest }) {
  const tasks = req.analysis?.intents ?? [];
  const dec = req.analysis?.decomposition;
  const problems = req.analysis?.final_check ?? [];
  if (tasks.length < 2 && !problems.length && !dec?.note) return null;
  return (
    <section className="kw-tasks" aria-label="Tasks in this request">
      <h2>Tasks ({tasks.length})</h2>
      {dec?.note && <p className={dec.uncovered?.length ? "kw-banner warn" : "kw-muted tiny"}>{dec.note}</p>}
      <ol className="kw-task-list">
        {tasks.map((t) => (
          <li key={t.intent} className={`kw-task st-${t.status ?? "completed"}`}>
            <span className="kw-task-n">{t.intent}</span>
            <span className="kw-task-text">{t.text}</span>
            <span className={`kw-chip ts-${t.status ?? "completed"}`}>{t.status_label ?? "Completed proposal"}</span>
          </li>))}
      </ol>
      {problems.length > 0 && (
        <div className="kw-banner warn" role="alert"><b>Check before submitting:</b><ul>{problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div>)}
      <p className="kw-muted tiny">The tasks were applied one after another to an unpublished working copy of each document, so later tasks build on earlier ones. This is an automatic check, not an approval: you and an administrator still decide.</p>
    </section>
  );
}

/** One candidate document, as a compact row that expands (one at a time) into the full comparison, the explanation and the decisions. */
function Candidate({ req, it, readOnly, open, onToggle, onItem, canSubmit, onSubmit }: {
  req: KwRequest; it: KwItem; readOnly: boolean; open: boolean; onToggle: () => void; onItem: (i: KwItem) => void; canSubmit: boolean; onSubmit: () => void;
}) {
  const { withToken } = useAuth();
  const [mode, setMode] = useState<"view" | "edit" | "discard">("view");
  const [texts, setTexts] = useState<string[]>([]);
  const [full, setFull] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const base = baseText(it.diff?.body);
  const ai = it.ai?.edits ?? [];
  const hasPatch = ai.length > 0;
  const shown = editsOf(it);
  const locked = readOnly || it.base_stale;
  const needs = !readOnly && !it.decision && !!it.actionable;
  const tl = taskLine(it, req);

  const decide = (body: Parameters<typeof api.kwDecide>[3]) => {
    setBusy(true); setError(null);
    withToken((t) => api.kwDecide(t, req.request_id, it.doc_id, { ...body, expected_row_version: it.row_version }))
      .then((r) => { onItem(r); setMode("view"); }).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  const startEdit = () => { setTexts(shown.map((e) => e.replacement)); setFull(it.proposed_content ?? base); setMode("edit"); };
  const live = hasPatch ? ai.map((e, i) => ({ ...e, replacement: texts[i] ?? e.replacement })) : [];
  const preview = mode === "edit" ? (hasPatch ? applyPatches(base, live) : full) : undefined;
  const bodyId = `cand-${it.doc_id}`;

  return (
    <article className={`kw-acc${open ? " open" : ""}${needs ? " needs" : ""}`} aria-label={`Suggestion for ${it.doc_title}`}>
      <button className="kw-acc-head" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
        <span className="kw-acc-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="kw-acc-main"><b>{it.doc_title}</b>
          <span className="kw-sub">{tl ?? (it.ai?.reason ? (it.ai.reason.length > 110 ? `${it.ai.reason.slice(0, 110)}…` : it.ai.reason) : `${ai.length} proposed change(s)`)}</span></span>
        <span className="kw-acc-chips"><ClassChip c={it.classification} /> <DecisionChip d={it.decision} />{needs && <span className="kw-chip kw-need">Action required</span>}
          {it.base_stale && <span className="kw-chip st-failed">Out of date</span>}</span>
      </button>
      {open && (
        <div className="kw-acc-body" id={bodyId}>
          <p className="kw-sub">{it.ai?.path ?? "Knowledge base"} · published v{it.current_published_version ?? "—"} (analysed v{it.base_version}) · <span title="The model's own estimate, not a guarantee">confidence {pct(it.confidence)}</span></p>
          {it.base_stale && <p className="kw-banner warn" role="alert">This document was published again after the analysis, so the suggestion can no longer be applied. Discard it, or re-run the analysis.</p>}
          {it.ai?.reason && <p className="kw-why"><b>Why the AI suggests this:</b> {it.ai.reason}</p>}
          {!!it.ai?.tasks?.length && (req.analysis?.intents?.length ?? 0) > 1 && (
            <ul className="kw-unsure" aria-label="Tasks applied to this document">{it.ai.tasks.map((t) => (
              <li key={t.task}>Task {t.task}: {t.edits ? `${t.edits} change(s)` : "no change"} — {t.reason}</li>))}</ul>)}
          {!!it.ai?.uncertainty?.length && <ul className="kw-unsure" aria-label="What the AI is unsure about">{it.ai.uncertainty.map((u, i) => <li key={i}>{u}</li>)}</ul>}
          {it.classification === "potential" && !it.decision && <p className="kw-banner">The AI is not certain this passage must change. Please confirm before accepting.</p>}
          {it.decision === "discarded" && mode !== "edit" ? (
            <p className="kw-muted">Discarded. The published text stays as it is.{it.rationale ? ` Reason: ${it.rationale}` : ""}</p>
          ) : hasPatch || it.decision === "manual" || mode === "edit" ? (
            <CompareView it={it} preview={preview} />
          ) : (
            <p className="kw-banner warn">The AI found a related passage but could not write a safe replacement. Edit it yourself, or discard.</p>
          )}
          {!!it.ai?.passages?.length && (
            <details className="kw-details"><summary>How each passage was judged ({it.ai.passages.length})</summary>
              <ul className="kw-evidence" aria-label={`Evidence for ${it.doc_title}`}>
                {it.ai.passages.map((p) => (
                  <li key={p.n}><b>{KIND_LABEL[p.kind ?? ""] ?? p.verdict}</b>{p.heading ? <> · <i>{p.heading}</i></> : null}: “{p.text.length > 160 ? `${p.text.slice(0, 160)}…` : p.text}”
                    {p.evidence && <div className="kw-muted tiny">Evidence: {p.evidence}</div>}
                    {p.judge && <div className="kw-muted tiny">{JUDGE_LABEL[p.judge]}</div>}
                    {p.change && <div className="kw-muted tiny">Proposed: {CHANGE_LABEL[p.change.kind] ?? p.change.kind}
                      {p.change.removed.length > 0 && <> — removes {p.change.removed.length} line{p.change.removed.length === 1 ? "" : "s"}</>}
                      {p.change.added.length > 0 && <> — adds {p.change.added.length} line{p.change.added.length === 1 ? "" : "s"}</>}</div>}
                    {p.change?.flags?.map((f, k) => <div key={k} className="kw-muted tiny kw-flag">Check: {f}</div>)}
                    {p.reason && <div className="kw-muted tiny">{p.reason}</div>}</li>))}
              </ul></details>)}
          {hasPatch && mode !== "edit" && (
            <details className="kw-details"><summary>Passages and replacements</summary><PatchLegend />
              <PatchView base={base} edits={shown.map((e, i) => ({ ...e, heading: ai[i]?.heading, kind: ai[i]?.kind }))} label={`Proposed change to ${it.doc_title}`} /></details>)}
          {mode === "edit" && (
            <div className="kw-ask">
              {hasPatch ? ai.map((e, i) => (
                <label key={i}>Replacement {ai.length > 1 ? `${i + 1} ` : ""}for “{e.original}”
                  <textarea rows={3} value={texts[i] ?? ""} onChange={(ev) => setTexts((p) => p.map((x, j) => (j === i ? ev.target.value : x)))} />
                </label>
              )) : <label>Edited document text<textarea rows={10} value={full} onChange={(ev) => setFull(ev.target.value)} /></label>}
              {hasPatch && <><p className="kw-muted tiny">Your wording, passage by passage:</p><PatchLegend /><PatchView base={base} edits={live} label="Your edit, before saving" /></>}
              <div className="kw-actions">
                <button className="kw-btn primary" disabled={busy} onClick={() => decide(hasPatch ? { decision: "manual", replacements: texts.map((replacement, index) => ({ index, replacement })) } : { decision: "manual", content: full })}>Save my edit</button>
                <button className="kw-btn" onClick={() => setMode("view")}>Cancel</button>
              </div>
            </div>)}
          {mode === "discard" && (
            <div className="kw-ask">
              <label>Reason (optional)<input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="e.g. historical record, must stay as published" /></label>
              <div className="kw-actions"><button className="kw-btn primary" disabled={busy} onClick={() => decide({ decision: "discarded", reason: reason.trim() || undefined })}>Confirm discard</button><button className="kw-btn" onClick={() => setMode("view")}>Cancel</button></div>
            </div>)}
          {error && <p role="alert" className="kw-error">{error}</p>}
          {it.decided_by && <p className="kw-muted tiny">Decided by {it.decided_by} · {when(it.decided_at)}</p>}
          {!readOnly && mode === "view" && (
            <div className="kw-actions" role="group" aria-label={`Decision for ${it.doc_title}`}>
              {it.decision === "accepted" ? (
                <>
                  <span className="kw-done" role="status">✓ Accepted — this proposal is part of the request</span>
                  {canSubmit && <button className="kw-btn primary" disabled={busy} onClick={onSubmit}>Submit for review</button>}
                  <button className="kw-btn" disabled={busy || locked} onClick={startEdit}>Edit manually</button>
                  <button className="kw-btn" disabled={busy} onClick={() => setMode("discard")}>Discard</button>
                </>
              ) : it.decision === "manual" ? (
                <>
                  <span className="kw-done" role="status">✎ Your edit is saved — the AI proposal is kept for comparison</span>
                  {canSubmit && <button className="kw-btn primary" disabled={busy} onClick={onSubmit}>Submit for review</button>}
                  <button className="kw-btn" disabled={busy || locked} onClick={startEdit}>Edit again</button>
                  {hasPatch && <button className="kw-btn" disabled={busy || locked} onClick={() => decide({ decision: "accepted" })}>Use the AI proposal instead</button>}
                  <button className="kw-btn" disabled={busy} onClick={() => setMode("discard")}>Discard</button>
                </>
              ) : (
                <>
                  <button className="kw-btn primary" disabled={busy || locked || !hasPatch} onClick={() => decide({ decision: "accepted" })}>{it.decision === "discarded" ? "Accept after all" : "Accept"}</button>
                  <button className="kw-btn" disabled={busy || locked} onClick={startEdit}>Edit manually</button>
                  {it.decision !== "discarded" && <button className="kw-btn" disabled={busy} onClick={() => setMode("discard")}>Discard</button>}
                </>
              )}
            </div>)}
        </div>)}
    </article>
  );
}

/** The decision workspace: the tasks, a compact list of the documents with a proposed change (one expanded at a time), the examined-but-unchanged documents in a
 *  separate diagnostic summary, a consolidated table, then Submit. */
export function Decisions({ req, readOnly = false, onItem, onSubmit, onReanalyse, busy }: {
  req: KwRequest; readOnly?: boolean; onItem: (i: KwItem) => void; onSubmit: () => void; onReanalyse?: () => void; busy?: boolean;
}) {
  const items = req.items ?? [];
  const cands = items.filter((i) => i.actionable);
  const manual = items.filter((i) => i.origin === "manual");
  const related = items.filter((i) => i.classification === "related_no_change");
  const pending = cands.filter((i) => !i.decision).length;
  const summary = req.analysis?.summary;
  const [openId, setOpenId] = useState<string | null>(null);
  const canSubmit = !readOnly && !busy && pending === 0 && (req.counts?.included ?? 0) > 0;
  return (
    <div>
      {summary && (
        <p className="kw-banner" role="status">
          Found {summary.discovered} possible document(s): <b>{summary.definite}</b> definitely affected, <b>{summary.potential}</b> to confirm, <b>{summary.related_no_change}</b> related with no change.
          {!!summary.flags.historical && <> {summary.flags.historical} dated or historical record(s) were left alone.</>}
          {!!summary.learned?.length && <> Because the request did not say what the old value was, the documents were read to learn it: {summary.learned.join(", ")}.</>}
          {!!summary.kinds?.ambiguous && <> {summary.kinds.ambiguous} passage(s) were contradictory or ambiguous and are listed for your judgement.</>}
        </p>)}
      <TaskStatus req={req} />
      {cands.length === 0 && <p className="kw-banner warn">The AI found no document that needs to change. Nothing is forced: you can re-describe the change, or close this request.</p>}
      {cands.length > 0 && (
        <section aria-label="Documents with a proposed change">
          <h2>Proposed changes ({cands.length}){pending > 0 && !readOnly ? <span className="kw-muted"> · {pending} need your decision</span> : null}</h2>
          <div className="kw-acc-list">
            {cands.map((it) => <Candidate key={`${it.doc_id}:${it.row_version}`} req={req} it={it} readOnly={readOnly} open={openId === it.doc_id}
              onToggle={() => setOpenId((o) => (o === it.doc_id ? null : it.doc_id))} onItem={onItem} canSubmit={canSubmit} onSubmit={onSubmit} />)}
          </div>
        </section>)}
      {(related.length > 0 || !!summary?.not_relevant?.length) && (
        <details className="kw-details kw-diag"><summary>Examined, no change needed ({related.length + (summary?.not_relevant?.length ?? 0)})</summary>
          {related.length > 0 && <ul className="kw-list" aria-label="Related documents with no change">{related.map((it) => <li key={it.doc_id} className="kw-row static"><b>{it.doc_title}</b><span className="kw-sub">{it.ai?.reason ?? "Mentions the topic but the fact does not change."}</span></li>)}</ul>}
          {!!summary?.not_relevant?.length && <ul aria-label="Documents judged not relevant">{summary.not_relevant.map((n) => <li key={n.doc_id}>{n.title} — <span className="kw-muted">{n.reason}</span></li>)}</ul>}
        </details>)}
      <ProcessingLog trace={req.analysis?.trace} />
      {(cands.length > 0 || manual.length > 0) && (
        <section aria-label="Summary of your decisions"><h2>Summary</h2>
          <div className="kw-tablewrap"><table className="kw-table">
            <caption className="sr-only">Every document in this change and what happens to it</caption>
            <thead><tr><th scope="col">Document</th><th scope="col">AI says</th><th scope="col">Confidence</th><th scope="col">Your decision</th></tr></thead>
            <tbody>
              {manual.map((it) => <tr key={it.doc_id}><th scope="row">{it.doc_title}</th><td>Your own edit</td><td>—</td><td><span className="kw-chip">✎ Included</span></td></tr>)}
              {cands.map((it) => <tr key={it.doc_id}><th scope="row">{it.doc_title}</th><td><ClassChip c={it.classification} /></td><td>{pct(it.confidence)}</td><td><DecisionChip d={it.decision} /></td></tr>)}
            </tbody></table></div>
        </section>)}
      {!readOnly && (
        <div className="kw-actions kw-submitbar">
          <button className="kw-btn primary" disabled={busy || pending > 0 || (cands.length === 0 && manual.length === 0) || (req.counts?.included ?? 0) === 0} onClick={onSubmit}>Submit for administrator review</button>
          {onReanalyse && <button className="kw-btn" disabled={busy} onClick={onReanalyse}>Re-run analysis</button>}
          {pending > 0 && <span className="kw-muted" role="status">{pending} document(s) still need a decision.</span>}
          {pending === 0 && cands.length > 0 && (req.counts?.included ?? 0) === 0 && <span className="kw-muted">Everything is discarded, so there is nothing to submit.</span>}
        </div>)}
    </div>
  );
}
