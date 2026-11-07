"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Transcript as T, TranscriptEntry } from "@/lib/types";

const SPEAKER: Record<TranscriptEntry["speaker"], string> = { customer: "Customer", ai: "AI assistant", staff: "Human staff", system: "System" };
const time = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString() : "");

/** The complete chronological conversation of a call (M4.1), read from the server's single conversation history. Staff speech appears as when they spoke,
 *  never as words: nothing is transcribed from the staff microphone. Reloads while the call is live. */
export function Transcript({ callId, live, onData }: { callId: string; live: boolean; onData?: (t: T) => void }) {
  const { withToken } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(() => {
    const n = ++seq.current;
    withToken((t) => api.transcript(t, callId)).then((r) => { if (n === seq.current) { setData(r); setError(null); onData?.(r); } })
      .catch((e) => { if (n === seq.current) setError(e instanceof ApiError ? e.message : "Something went wrong."); });
  }, [withToken, callId, onData]);

  useEffect(() => { setData(null); setError(null); load(); }, [load]);
  useEffect(() => { if (!live) return; const t = setInterval(load, 5000); return () => clearInterval(t); }, [live, load]);

  return (
    <section className="transcript" aria-label="Full call transcript">
      <div className="bar"><h3>Full transcript</h3><button className="btn small" onClick={load}>Refresh transcript</button></div>
      {error && <p role="alert" className="error">Could not load the transcript: {error}</p>}
      {!data && !error && <p className="muted" aria-busy="true">Loading transcript…</p>}
      {data && data.entries.length === 0 && <p className="muted">Nothing has been said on this call yet.</p>}
      {data && data.entries.length > 0 && (
        <ol className="tlist">
          {data.entries.map((e, i) => (
            <li key={i} className={`t t-${e.speaker}${e.earlier ? " earlier" : ""}`}>
              <span className="tmeta"><time>{time(e.at)}</time> <strong>{SPEAKER[e.speaker]}</strong>{e.staff && e.speaker === "staff" ? ` (${e.staff})` : ""}{e.earlier ? " · earlier on this ticket" : ""}</span>
              {e.kind === "speech"
                ? <p className="muted">{e.note ?? "Spoke to the caller (staff audio is not transcribed)"}{e.duration_s != null ? ` · ${e.duration_s}s` : ""}</p>
                : <p>{e.text}</p>}
            </li>
          ))}
        </ol>
      )}
      {data?.notes.map((n) => <p key={n} className="muted tiny">{n}</p>)}
    </section>
  );
}
