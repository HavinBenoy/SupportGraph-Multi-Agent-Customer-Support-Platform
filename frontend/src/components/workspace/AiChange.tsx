"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { KwItem, KwQuestion, KwRequest } from "@/lib/types";
import { Decisions } from "./Decisions";
import { Badge, msg } from "./ui";

const STAGES: Record<string, string> = { parsing: "Understanding the change", discovering: "Looking for related documents", retrieving: "Reading the relevant sections", evaluating: "Judging each document", reading: "Reading the sections with the language model", finishing: "Preparing suggestions" };
const POLL_MS = 1500;

/** The AI-assisted change page (workflow B, and workflow A after "Find related documents with AI"). Staff describe the change, optionally name a reference document and
 *  Analyse. The server analyses in the background; this page polls, asks follow-up questions when the change is ambiguous, then shows the decision workspace. */
export function AiChange({ initial, onClose, onSubmitted }: {
  initial?: { requestId?: string; draftRequestId?: string; autoStart?: boolean; referenceDocId?: string; referenceTitle?: string; description?: string };
  onClose: () => void; onSubmitted: (note: string) => void;
}) {
  const { withToken, session } = useAuth();
  const [desc, setDesc] = useState(initial?.description ?? "");
  const [ref, setRef] = useState<{ doc_id: string; title: string } | null>(initial?.referenceDocId ? { doc_id: initial.referenceDocId, title: initial.referenceTitle ?? initial.referenceDocId } : null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ doc_id: string; title: string; category: string }[]>([]);
  const [req, setReq] = useState<KwRequest | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loaded = useRef(false);

  const run = (fn: () => Promise<unknown>) => { setBusy(true); setError(null); fn().catch((e) => setError(msg(e))).finally(() => setBusy(false)); };
  const refresh = useCallback((id: string) => withToken((t) => api.kwMyRequest(t, id)).then((r) => { setReq(r); return r; }), [withToken]);
  useEffect(() => {
    if (!session || loaded.current || !initial?.requestId) return;
    loaded.current = true;
    refresh(initial.requestId).then((r) => setDesc(r.description ?? "")).catch((e) => setError(msg(e)));
  }, [initial?.requestId, refresh, session]);

  const auto = useRef(false);
  useEffect(() => {                                           // workflow A: the staff member already described the change in the editor
    if (!session || auto.current || !initial?.autoStart || !initial.description) return;
    auto.current = true;
    run(async () => {
      const r = await withToken((t) => api.kwAiStart(t, initial.description!.trim(), { ...(initial.referenceDocId ? { reference_doc_id: initial.referenceDocId } : {}), ...(initial.draftRequestId ? { request_id: initial.draftRequestId } : {}) }));
      const out = await withToken((t) => api.kwAnalyse(t, r.request_id));
      setReq(out.request);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  // Poll while the server is analysing. The page never claims progress it has not been told about.
  const analysing = req?.status === "analysing";
  useEffect(() => {
    if (!analysing || !req) return;
    const h = setInterval(() => { refresh(req.request_id).catch((e) => setError(msg(e))); }, POLL_MS);
    return () => clearInterval(h);
  }, [analysing, req, refresh]);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits([]); return; }
    let live = true;
    const t = setTimeout(() => withToken((tk) => api.kwSearch(tk, term, "staff")).then((r) => live && setHits(r.items.slice(0, 6))).catch(() => undefined), 250);
    return () => { live = false; clearTimeout(t); };
  }, [q, withToken]);

  const analyse = () => run(async () => {
    const r = req ?? await withToken((t) => api.kwAiStart(t, desc.trim(), { ...(ref ? { reference_doc_id: ref.doc_id } : {}), ...(initial?.draftRequestId ? { request_id: initial.draftRequestId } : {}) }));
    const out = await withToken((t) => api.kwAnalyse(t, r.request_id, req ? { description: desc.trim() } : {}));
    setReq(out.request);
  });
  const answer = () => req && run(async () => { const out = await withToken((t) => api.kwAnalyse(t, req.request_id, { answers })); setReq(out.request); });
  const submit = () => req && run(async () => { const r = await withToken((t) => api.kwSubmit(t, req.request_id, req.description ?? desc.trim())); onSubmitted(`Submitted for review: ${r.description ?? "your change"}.`); });
  const onItem = (it: KwItem) => setReq((r) => (r ? { ...r, items: r.items?.map((x) => (x.doc_id === it.doc_id ? it : x)) } : r));
  const refreshAfterItem = (it: KwItem) => { onItem(it); if (req) refresh(req.request_id).catch(() => undefined); };

  const st = req?.status;
  const an = req?.analysis;
  const editableForm = !req || st === "draft";
  const qs: KwQuestion[] = an?.questions ?? [];

  return (
    <section aria-label="AI-assisted change" className="kw-ai">
      <p className="kw-eyebrow">AI-assisted change</p>
      <h1 className="kw-title">Describe the change</h1>
      <p className="kw-muted">The assistant finds every document the change may affect and drafts the new wording. <b>Nothing is published</b> until you submit it and an administrator approves it.</p>
      {req && <p><Badge s={req.status} /></p>}
      {error && <p role="alert" className="kw-error">{error}</p>}

      {editableForm && (
        <div className="kw-ask">
          <label>What is changing?
            <textarea rows={4} maxLength={2000} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="e.g. The Python Full Stack fee increases from ₹45,000 to ₹48,000 from 1 December." />
          </label>
          <div>
            {ref ? <p>Reference document: <b>{ref.title}</b> <button className="kw-btn small" onClick={() => setRef(null)} aria-label="Remove reference document">Remove</button></p> : (
              <label>Reference document (optional)
                <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search for a document the change is about" />
              </label>)}
            {!ref && hits.length > 0 && <ul className="kw-list" aria-label="Matching documents">{hits.map((h) => <li key={h.doc_id}><button className="kw-row" onClick={() => { setRef(h); setQ(""); setHits([]); }}>{h.title}<span className="kw-sub">{h.category}</span></button></li>)}</ul>}
          </div>
          {an?.error && <p role="alert" className="kw-error">The last analysis failed: {an.error} You can try again.</p>}
          <div className="kw-actions"><button className="kw-btn primary" disabled={busy || desc.trim().length < 8} onClick={analyse}>{an?.error ? "Retry analysis" : "Analyse"}</button><button className="kw-btn" onClick={onClose}>Back</button></div>
        </div>)}

      {analysing && (
        <div className="kw-progress-wrap" role="status" aria-live="polite">
          <p><b>{STAGES[an?.stage ?? ""] ?? "Analysing"}…</b></p>
          <div className="kw-progress" role="progressbar" aria-label="Analysis progress" aria-valuemin={0} aria-valuemax={an?.total ?? 100} aria-valuenow={an?.done ?? 0}>
            <span style={{ width: `${an?.total ? Math.min(100, Math.round(((an.done ?? 0) / an.total) * 100)) : 8}%` }} />
          </div>
          <p className="kw-muted tiny">{an?.total ? `${an.done ?? 0} of ${an.total} documents checked` : "Starting"} · you can leave this page and come back from “My change requests”.</p>
        </div>)}

      {st === "awaiting_clarification" && (
        <div className="kw-ask" role="group" aria-label="Questions from the assistant">
          <p><b>A few questions before I look further</b> — I would rather ask than guess.</p>
          {qs.map((qn) => (
            <fieldset key={qn.id} className="kw-fieldset"><legend>{qn.text}</legend>
              {qn.options.length > 0 ? qn.options.map((o) => (
                <label key={o} className="kw-radio"><input type="radio" name={qn.id} checked={answers[qn.id] === o} onChange={() => setAnswers((a) => ({ ...a, [qn.id]: o }))} /> {o}</label>
              )) : <input aria-label={qn.text} value={answers[qn.id] ?? ""} onChange={(e) => setAnswers((a) => ({ ...a, [qn.id]: e.target.value }))} />}
            </fieldset>))}
          <div className="kw-actions"><button className="kw-btn primary" disabled={busy || qs.some((qn) => !answers[qn.id]?.trim())} onClick={answer}>Continue analysis</button></div>
        </div>)}

      {req && (st === "awaiting_decisions" || st === "needs_revision") && (
        <>
          {st === "needs_revision" && req.return_note && <p className="kw-banner warn">Returned by the administrator: {req.return_note}</p>}
          <p className="kw-muted"><b>{req.description}</b></p>
          <Decisions req={req} busy={busy} onItem={refreshAfterItem} onSubmit={submit} onReanalyse={() => { setDesc(req.description ?? ""); run(async () => { const out = await withToken((t) => api.kwAnalyse(t, req.request_id, { description: req.description ?? undefined })); setReq(out.request); }); }} />
        </>)}

      {req && ["submitted", "under_review", "approved", "rejected", "published", "failed", "cancelled", "processing", "publishing", "applied"].includes(req.status) && (
        <div><p className="kw-banner">This request has left your hands and is now “{req.status.replace("_", " ")}”.</p><Decisions req={req} readOnly onItem={() => undefined} onSubmit={() => undefined} /></div>)}
      {req && !editableForm && <p><button className="kw-btn" onClick={onClose}>Back to the knowledge base</button></p>}
    </section>
  );
}
