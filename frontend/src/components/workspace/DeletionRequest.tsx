"use client";
import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** "Request deletion" of a published document. Nothing is removed here: the request goes to the administrators' queue, and only an approval + publish archives the
 *  document (it leaves the assistant's answers; its versions and audit history are kept). Administrators confirm explicitly and go through the same review. */
export function DeletionRequest({ scope, docId, title, version, onClose, onDone }: {
  scope: "admin" | "staff"; docId: string; title: string; version: number; onClose: () => void; onDone: (message: string) => void;
}) {
  const { withToken } = useAuth();
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = scope === "admin";
  const ok = reason.trim().length >= 5 && (!isAdmin || confirm);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!ok) return;
    setBusy(true); setError(null);
    withToken((t) => api.kwRequestDeletion(t, scope, docId, { reason: reason.trim(), expected_version: version, ...(isAdmin ? { confirm: true } : {}) }))
      .then(() => onDone(`Deletion of “${title}” requested. It stays live until an administrator approves the request.`))
      .catch((er) => setError(er instanceof ApiError ? er.message : "Something went wrong."))
      .finally(() => setBusy(false));
  };
  return (
    <div className="dialog-back" role="presentation" onClick={onClose}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="delreq-h" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2 id="delreq-h">Request deletion of “{title}”</h2>
        <p className="muted">The document is <b>not</b> deleted now. An administrator reviews the request; only after approval is it archived: it stops being used in answers,
          and every version and the audit history are kept. If the document is published again before approval, the request is refused as out of date.</p>
        <label>Why should it be deleted?
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="e.g. This course is no longer offered" autoFocus /></label>
        <p className="kw-muted tiny">Document {docId} · published v{version}</p>
        {isAdmin && (
          <label className="kw-check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
            I confirm this deletion request. It goes through the same review and audit as anyone’s.</label>)}
        {error && <p role="alert" className="kw-error">{error}</p>}
        <div className="actions">
          <button type="button" className="kw-btn" onClick={onClose}>Cancel</button>
          <button className="kw-btn danger" disabled={busy || !ok}>Send deletion request</button>
        </div>
      </form>
    </div>
  );
}
