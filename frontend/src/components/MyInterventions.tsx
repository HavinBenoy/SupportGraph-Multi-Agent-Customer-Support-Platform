"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Interventions } from "@/lib/types";
import { FeedbackForm, REVIEW_LABEL } from "./FeedbackForm";

/** The staff member's OWN interventions (M4.1): forms still waiting, what was submitted and where it stands in review. Nobody else's feedback is listed here. */
export function MyInterventions() {
  const { withToken } = useAuth();
  const [data, setData] = useState<Interventions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const load = useCallback(() => {
    withToken((t) => api.interventions(t)).then((r) => { setData(r); setError(null); }).catch((e) => setError(e instanceof ApiError ? e.message : "Something went wrong."));
  }, [withToken]);
  useEffect(() => { load(); }, [load]);

  const status = (f: Interventions["items"][number]) => !f.call_ended ? "Call in progress" : f.state === "submitted" ? REVIEW_LABEL[f.review_status ?? ""] ?? "Submitted" : f.state === "skipped" ? "Skipped" : "Feedback needed";
  return (
    <div className="split">
      <section aria-label="My interventions">
        <div className="bar"><p className="muted">Calls you took over, and your feedback on them.</p><button className="btn small" onClick={load}>Refresh</button></div>
        {error && <p role="alert" className="error">{error}</p>}
        {!data && !error && <p className="muted" aria-busy="true">Loading…</p>}
        {data?.items.length === 0 && <p className="muted">You have not taken over any call yet.</p>}
        <ul className="list">
          {data?.items.map((f) => (
            <li key={f.call_id}>
              <button className={`row ${sel === f.call_id ? "sel" : ""}`} onClick={() => setSel(f.call_id)} aria-label={`Intervention on call ${f.short_id}: ${status(f)}`}>
                <strong>{f.short_id}</strong> <span className="pill" data-state={f.state}>{status(f)}</span>
                <span className="muted block">{f.intervened_at ? new Date(f.intervened_at).toLocaleString() : ""}{f.category_label ? ` · ${f.category_label}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="Intervention feedback">
        {!sel && <p className="muted">Select a call to write or review your feedback.</p>}
        {sel && <FeedbackForm key={sel} callId={sel} onChanged={load} />}
      </section>
    </div>
  );
}
