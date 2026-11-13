"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwItem, KwRequest } from "@/lib/types";
import { CompareView } from "./Compare";
import { msg } from "./ui";

/** Refinement 2.2: revising a request an administrator returned.
 *
 *  Two clearly separate options, both inside the SAME request and lifecycle (needs_revision → submitted → review); nothing here publishes:
 *   A. AI-assisted — run the analysis again with the reviewer's note in mind (the existing AI workflow).
 *   B. Manual — edit the complete returned proposal by hand: add, remove, replace or restructure anything. The server rebuilds the diff from the immutable
 *      published version; the AI's own proposal is kept for comparison; the edit is attributed and audited as a revision after the return.
 *  The published document and its versions are never touched until an administrator approves and publishes again. */
export const SOURCE_LABEL: Record<string, string> = { ai: "AI-assisted", manual: "Manual", unchanged: "No change" };

export function SourceChip({ it }: { it: KwItem }) {
  if (!it.revision_source) return null;
  return (
    <span className={`kw-chip src-${it.revision_source}`} title="Where the proposed text came from">
      {SOURCE_LABEL[it.revision_source]}{it.revised_after_return ? " · revised after return" : ""}
    </span>
  );
}

const revisable = (it: KwItem) => it.revision_source !== "unchanged" && (it.origin === "manual" || !!it.actionable);

export function RevisionPanel({ req, onChanged, onAi }: { req: KwRequest; onChanged: (r: KwRequest, note?: string) => void; onAi?: (requestId: string) => void }) {
  const { withToken } = useAuth();
  const [mode, setMode] = useState<"choose" | "manual">("choose");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const items = (req.items ?? []).filter(revisable);
  const reload = () => withToken((t) => api.kwMyRequest(t, req.request_id)).then(onChanged);

  const startAi = () => {
    if (busy) return;
    setBusy(true); setError(null);
    const go = req.source === "ai_assisted" ? Promise.resolve() : withToken((t) => api.kwAiStart(t, req.description ?? "Revision", { request_id: req.request_id })).then(() => undefined);
    go.then(() => onAi?.(req.request_id)).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  const resubmit = () => {
    if (busy) return;
    setBusy(true); setError(null); setInfo(null);
    withToken((t) => api.kwSubmit(t, req.request_id, req.description ?? "Revision"))
      .then((r) => { setMode("choose"); onChanged(r, "Resubmitted for review. Nothing is published until an administrator approves it."); })
      .catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };

  return (
    <section className="kw-revision" aria-label="Revise this request">
      <h3>Revise this request</h3>
      <p className="kw-muted tiny">The published document stays as it is. Choose how to revise the proposal, then resubmit it for review.</p>
      {error && <p role="alert" className="kw-error">{error}</p>}
      {info && <p role="status" className="kw-banner ok">{info}</p>}
      <div className="kw-rev-options" role="group" aria-label="Revision options">
        <button className="kw-rev-option" disabled={busy || !onAi} onClick={startAi}>
          <b>A · Revise with the AI</b><span className="kw-muted tiny">Run the analysis again and decide on its new suggestions.</span>
        </button>
        <button className={`kw-rev-option${mode === "manual" ? " on" : ""}`} aria-pressed={mode === "manual"} disabled={busy || items.length === 0} onClick={() => setMode("manual")}>
          <b>B · Edit manually</b><span className="kw-muted tiny">Edit the returned proposal yourself: add, remove, replace or restructure text.</span>
        </button>
      </div>
      {mode === "manual" && (
        <div className="kw-rev-manual">
          {items.map((it) => <ManualRevision key={`${it.doc_id}:${it.row_version}`} requestId={req.request_id} it={it} onSaved={() => reload().catch((e) => setError(msg(e)))} />)}
          <div className="kw-actions">
            <button className="kw-btn" disabled={busy} onClick={() => setMode("choose")}>Close the editor</button>
            <button className="kw-btn primary" disabled={busy} onClick={resubmit}>{busy ? "Resubmitting…" : "Resubmit for review"}</button>
          </div>
        </div>)}
      {mode === "choose" && items.some((i) => i.revised_after_return) && (
        <div className="kw-actions"><button className="kw-btn primary" disabled={busy} onClick={resubmit}>{busy ? "Resubmitting…" : "Resubmit for review"}</button></div>)}
    </section>
  );
}

export function ManualRevision({ requestId, it, onSaved }: { requestId: string; it: KwItem; onSaved: () => void }) {
  const { withToken } = useAuth();
  const start = it.proposed_content ?? "";
  const [text, setText] = useState(start);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const dirty = text !== start;
  const save = () => {
    if (busy || !dirty) return;
    setBusy(true); setError(null);
    const call = it.origin === "ai"
      ? (t: string) => api.kwDecide(t, requestId, it.doc_id, { decision: "manual", content: text, expected_row_version: it.row_version })
      : (t: string) => api.kwSaveItem(t, requestId, it.doc_id, text, it.row_version);
    withToken(call).then(() => { setSaved(true); onSaved(); }).catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  const useAi = () => {
    if (busy) return;
    setBusy(true); setError(null);
    withToken((t) => api.kwDecide(t, requestId, it.doc_id, { decision: "accepted", expected_row_version: it.row_version })).then(onSaved)
      .catch((e) => setError(msg(e))).finally(() => setBusy(false));
  };
  const id = `rev-${it.doc_id}`;
  return (
    <div className="kw-rev-doc">
      <h4>{it.doc_title} <SourceChip it={it} /></h4>
      {it.base_stale && <p className="kw-banner warn">The published text changed after this proposal was made. Revise with the AI, or discard and start again.</p>}
      {error && <p role="alert" className="kw-error">{error}</p>}
      {saved && !dirty && <p role="status" className="kw-banner ok">Saved as your manual revision.</p>}
      <label htmlFor={id} className="kw-label">Full proposed text (Markdown)</label>
      <textarea id={id} className="kw-textarea" rows={14} value={text} onChange={(e) => { setText(e.target.value); setSaved(false); }} spellCheck />
      <div className="kw-actions">
        <button className="kw-btn" disabled={busy || !dirty} onClick={() => setText(start)}>Undo my changes</button>
        {it.origin === "ai" && it.decision === "manual" && <button className="kw-btn" disabled={busy} onClick={useAi}>Use the AI proposal instead</button>}
        <button className="kw-btn primary" disabled={busy || !dirty || !text.trim()} onClick={save}>{busy ? "Saving…" : "Save revision"}</button>
      </div>
      <CompareView it={it} preview={dirty ? text : null} />
    </div>
  );
}
