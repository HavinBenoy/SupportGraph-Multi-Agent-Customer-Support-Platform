/** Typed client for the SupportGraph API. All calls go to same-origin `/api` (proxied by Next). Errors become `ApiError` with a message
 *  that is safe to show: the server's `detail` when it is a string, otherwise a generic one. Tokens live in sessionStorage (cleared when the
 *  tab closes); see docs/STEP5.md for the trade-off. */
import type { Analytics, Approval, CallAction, ChatReply, Decision, EscalationDetail, EscalationRow, Feedback, FeedbackInput, FeedbackQueue, History, Interventions, LiveCall,
  PublicConfig, PurgeResult, RecordingRow, RetentionStatus, Role, Session, Transcript, TrialEventRow, TrialMetrics, TrialPatch, TrialView } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter?: number) { super(message); this.name = "ApiError"; }
  get isAuth() { return this.status === 401; }
}

const KEY = "sg.session";

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s.expiresAt > Date.now() ? s : null;
  } catch { return null; }
}
export function saveSession(s: Session | null) {
  try { if (s) sessionStorage.setItem(KEY, JSON.stringify(s)); else sessionStorage.removeItem(KEY); } catch { /* storage blocked: stay signed in for this page only */ }
}

type Opts = { method?: string; body?: unknown; token?: string | null };

async function call<T>(path: string, { method = "GET", body, token }: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    let msg = res.status >= 500 ? "The server had a problem. Please try again." : "Request failed.";
    try { const j = await res.json(); if (typeof j.detail === "string") msg = j.detail; else if (res.status === 422) msg = "That input was not accepted."; } catch { /* keep generic */ }
    throw new ApiError(res.status, msg, Number(res.headers.get("Retry-After")) || undefined);
  }
  return (await res.json()) as T;
}

export const api = {
  config: (tenant?: string) => call<PublicConfig>(`/config${tenant ? `?tenant=${encodeURIComponent(tenant)}` : ""}`),
  async login(username: string, password: string, tenant?: string): Promise<Session> {
    const r = await call<{ token: string; role: Role; username: string; tenant_id: string; student_id: string | null; expires_in: number }>(
      "/auth/login", { method: "POST", body: { username, password, ...(tenant ? { tenant } : {}) } });
    return { token: r.token, role: r.role, username: r.username, tenantId: r.tenant_id, studentId: r.student_id, expiresAt: Date.now() + r.expires_in * 1000 };
  },
  chat: (text: string, o: { ticketId?: string | null; token?: string | null; tenant?: string }) =>
    call<ChatReply>("/chat", { method: "POST", token: o.token, body: { text, ...(o.ticketId ? { ticket_id: o.ticketId } : {}), ...(!o.token && o.tenant ? { tenant: o.tenant } : {}) } }),
  history: (ticketId: string, o: { token?: string | null; tenant?: string }) =>
    call<History>(`/chat/${encodeURIComponent(ticketId)}${!o.token && o.tenant ? `?tenant=${encodeURIComponent(o.tenant)}` : ""}`, { token: o.token }),
  escalations: (token: string, status: string) => call<EscalationRow[]>(`/staff/escalations?status=${encodeURIComponent(status)}`, { token }),
  escalation: (token: string, id: string) => call<EscalationDetail>(`/staff/escalations/${encodeURIComponent(id)}`, { token }),
  claim: (token: string, id: string) => call<EscalationDetail>(`/staff/escalations/${encodeURIComponent(id)}/claim`, { method: "POST", token }),
  resolve: (token: string, id: string, note: string) => call<EscalationDetail>(`/staff/escalations/${encodeURIComponent(id)}/resolve`, { method: "POST", token, body: { note } }),
  approvals: (token: string) => call<Approval[]>("/staff/approvals", { token }),
  decide: (token: string, requestId: string, approve: boolean) => call<Decision>(`/staff/approvals/${encodeURIComponent(requestId)}/decision`, { method: "POST", token, body: { approve } }),
  calls: (token: string) => call<LiveCall[]>("/staff/calls", { token }),
  callAction: (token: string, id: string, action: CallAction) => call<LiveCall>(`/staff/calls/${encodeURIComponent(id)}/${action}`, { method: "POST", token }),
  analytics: (token: string, days: number) => call<Analytics>(`/admin/analytics?days=${days}`, { token }),
  // M4.1: transcript and intervention feedback (the server decides who may read or write them: the call's intervention owner alone writes)
  transcript: (token: string, id: string) => call<Transcript>(`/staff/calls/${encodeURIComponent(id)}/transcript`, { token }),
  feedback: (token: string, id: string) => call<Feedback>(`/staff/calls/${encodeURIComponent(id)}/feedback`, { token }),
  submitFeedback: (token: string, id: string, body: FeedbackInput) => call<Feedback>(`/staff/calls/${encodeURIComponent(id)}/feedback`, { method: "POST", token, body }),
  editFeedback: (token: string, id: string, body: FeedbackInput) => call<Feedback>(`/staff/calls/${encodeURIComponent(id)}/feedback`, { method: "PUT", token, body }),
  skipFeedback: (token: string, id: string) => call<Feedback>(`/staff/calls/${encodeURIComponent(id)}/feedback/skip`, { method: "POST", token }),
  interventions: (token: string) => call<Interventions>("/staff/interventions", { token }),
  // M5: Trial Verification Mode (administrators only, except the read-only flag)
  trialStatus: (token: string) => call<{ enabled: boolean; recording_offered: boolean }>("/staff/trial", { token }),
  trial: (token: string) => call<TrialView>("/admin/trial", { token }),
  saveTrial: (token: string, expectedVersion: number, patch: TrialPatch) => call<TrialView>("/admin/trial", { method: "PUT", token, body: { expected_version: expectedVersion, ...patch } }),
  trialEvents: (token: string) => call<TrialEventRow[]>("/admin/trial/events?limit=50", { token }),
  trialMetrics: (token: string, from?: string, to?: string) =>
    call<TrialMetrics>(`/admin/trial/metrics${from || to ? `?${new URLSearchParams({ ...(from ? { from } : {}), ...(to ? { to } : {}) })}` : ""}`, { token }),
  trialFeedback: (token: string, status?: string) => call<FeedbackQueue>(`/admin/trial/feedback${status ? `?status=${encodeURIComponent(status)}` : ""}`, { token }),
  reviewFeedback: (token: string, callId: string, status: string, note?: string) =>
    call<Feedback>(`/admin/trial/feedback/${encodeURIComponent(callId)}/review`, { method: "POST", token, body: { status, ...(note ? { note } : {}) } }),
  retention: (token: string) => call<RetentionStatus>("/admin/trial/retention", { token }),
  purge: (token: string, dryRun: boolean) => call<PurgeResult>("/admin/trial/retention/purge", { method: "POST", token, body: { dry_run: dryRun } }),
  recordings: (token: string) => call<RecordingRow[]>("/admin/trial/recordings", { token }),
  holdRecording: (token: string, id: string, hold: boolean, reason?: string) =>
    call<RecordingRow>(`/admin/trial/recordings/${encodeURIComponent(id)}/hold`, { method: "POST", token, body: { hold, ...(reason ? { reason } : {}) } }),
};
