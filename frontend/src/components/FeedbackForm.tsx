"use client";
import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Feedback, FeedbackInput } from "@/lib/types";

export const REVIEW_LABEL: Record<string, string> = { pending_review: "Pending review", reviewed: "Reviewed", approved_for_update: "Approved for knowledge update", rejected: "Rejected" };
const FALLBACK_CATEGORIES: Record<string, string> = {
  incorrect_information: "Incorrect factual information", outdated_knowledge: "Outdated knowledge", misleading_response: "Misleading response",
  policy_compliance: "Policy or compliance issue", customer_dissatisfaction: "Customer dissatisfaction", missing_knowledge: "Missing knowledge", other: "Other",
};
const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

/** "Call intervention feedback" (M4.1). Shown only to the staff member who took the call over, after it ended. What is written here is a PROPOSED correction:
 *  it waits for an administrator's review and never changes the knowledge base by itself. `aiMessages` lets the author point at the AI message concerned. */
export function FeedbackForm({ callId, aiMessages = [], onChanged }: { callId: string; aiMessages?: { index: number; text: string }[]; onChanged?: () => void }) {
  const { withToken } = useAuth();
  const [fb, setFb] = useState<Feedback | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [category, setCategory] = useState("");
  const [wrong, setWrong] = useState("");
  const [correct, setCorrect] = useState("");
  const [related, setRelated] = useState("");

  useEffect(() => {
    let alive = true;
    setFb(null); setLoadError(null);
    withToken((t) => api.feedback(t, callId)).then((r) => { if (alive) setFb(r); }).catch((e) => { if (alive) setLoadError(msg(e)); });
    return () => { alive = false; };
  }, [withToken, callId]);

  const cats = fb?.categories ?? FALLBACK_CATEGORIES;
  const input = (): FeedbackInput => ({ category, what_was_wrong: wrong.trim(), ...(correct.trim() ? { suggested_correction: correct.trim() } : {}), ...(related !== "" ? { related_index: Number(related) } : {}) });
  const valid = category !== "" && wrong.trim().length >= 5;

  async function act(fn: () => Promise<Feedback>) {
    if (busy) return;
    setBusy(true); setError(null);
    try { const r = await fn(); setFb({ ...r, categories: fb?.categories }); setEditing(false); onChanged?.(); } catch (e) { setError(msg(e)); } finally { setBusy(false); }
  }
  const submit = (e: FormEvent) => { e.preventDefault(); if (!valid) return; void act(() => withToken((t) => (fb?.state === "submitted" ? api.editFeedback(t, callId, input()) : api.submitFeedback(t, callId, input())))); };
  const skip = () => act(() => withToken((t) => api.skipFeedback(t, callId)));
  const startEdit = () => { if (!fb) return; setCategory(fb.category ?? ""); setWrong(fb.what_was_wrong ?? ""); setCorrect(fb.suggested_correction ?? ""); setRelated(fb.related_index != null ? String(fb.related_index) : ""); setEditing(true); };

  if (loadError) return <p role="alert" className="error">Feedback is not available: {loadError}</p>;
  if (!fb) return <p className="muted" aria-busy="true">Loading feedback…</p>;
  if (!fb.call_ended) return <p className="muted">The feedback form opens when the call has ended.</p>;

  if (fb.state === "submitted" && !editing) {
    return (
      <section className="card feedback" aria-label="Call intervention feedback">
        <h3>Call intervention feedback</h3>
        <p role="status" className="banner done">Feedback submitted. <span className="pill" data-review={fb.review_status}>{REVIEW_LABEL[fb.review_status ?? ""] ?? fb.review_status}</span></p>
        <dl>
          <dt>Category</dt><dd>{fb.category_label ?? fb.category}</dd>
          <dt>What went wrong</dt><dd>{fb.what_was_wrong}</dd>
          {fb.suggested_correction && <><dt>Correct information</dt><dd>{fb.suggested_correction}</dd></>}
          {fb.review_note && <><dt>Reviewer note</dt><dd>{fb.review_note}</dd></>}
        </dl>
        <p className="muted tiny">This is a proposed correction. It does not change the assistant’s knowledge until an administrator approves it and the knowledge is updated.</p>
        {error && <p role="alert" className="error">{error}</p>}
        {fb.editable ? <button className="btn" onClick={startEdit}>Edit feedback</button> : <p className="muted tiny">It has been reviewed and can no longer be edited.</p>}
      </section>
    );
  }

  return (
    <form className="card feedback form" aria-label="Call intervention feedback" onSubmit={submit}>
      <h3>Call intervention feedback</h3>
      {fb.state === "skipped" && !editing && <p className="muted" role="status">Feedback was skipped. You can still add it now.</p>}
      <label>Category
        <select value={category} onChange={(e) => setCategory(e.target.value)} required>
          <option value="">Choose a category…</option>
          {Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label>What did the AI say or do that required your intervention?
        <textarea value={wrong} onChange={(e) => setWrong(e.target.value)} rows={3} maxLength={2000} required />
      </label>
      <label>What is the correct information? (optional)
        <textarea value={correct} onChange={(e) => setCorrect(e.target.value)} rows={3} maxLength={2000} />
      </label>
      {aiMessages.length > 0 && (
        <label>Which AI message was it? (optional)
          <select value={related} onChange={(e) => setRelated(e.target.value)}>
            <option value="">Not sure / more than one</option>
            {aiMessages.map((m) => <option key={m.index} value={m.index}>{m.text.length > 80 ? `${m.text.slice(0, 80)}…` : m.text}</option>)}
          </select>
        </label>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="actions">
        <button className="btn primary" disabled={busy || !valid}>{fb.state === "submitted" ? "Save changes" : "Submit feedback"}</button>
        {fb.state === "submitted" ? <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button>
          : fb.state === "skipped" ? null : <button type="button" className="btn" disabled={busy} onClick={() => void skip()}>Skip</button>}
      </div>
      <p className="muted tiny">Your answer is a proposed correction for an administrator to review. It never changes the assistant’s knowledge by itself.</p>
    </form>
  );
}
