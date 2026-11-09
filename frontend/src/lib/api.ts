/** Typed client for the SupportGraph API. All calls go to same-origin `/api` (proxied by Next). Errors become `ApiError` with a message
 *  that is safe to show: the server's `detail` when it is a string, otherwise a generic one. Tokens live in sessionStorage (cleared when the
 *  tab closes); see docs/STEP5.md for the trade-off. */
import type { Analytics, Approval, CallAction, ChatReply, Decision, EscalationDetail, EscalationRow, Feedback, FeedbackInput, FeedbackQueue, History, Interventions, LiveCall,
  KbAuditRow, KbDoc, KwFolder, KwHistoryRow, KwItem, KwPreview, KwReadDoc, KwRequest, KwRequestList, KwTree, KbFeedbackRow, KbList, KbMeta, KbNewDoc, KbProposalRow, KbVersionRow, KbVersions, PublicConfig, PurgeResult, RecordingRow, RetentionStatus, Role, Session, Transcript, TrialEventRow, TrialMetrics, TrialPatch, TrialView } from "./types";

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
  kbMeta: (token: string) => call<KbMeta>("/admin/knowledge/meta", { token }),
  kbDocs: (token: string, q?: string, status?: string) => call<KbList>(`/admin/knowledge/documents?${new URLSearchParams({ page_size: "50", ...(q ? { q } : {}), ...(status ? { status } : {}) })}`, { token }),
  kbCreate: (token: string, d: KbNewDoc) => call<{ document: KbDoc; version: KbVersionRow }>("/admin/knowledge/documents", { method: "POST", token, body: d }),
  kbDoc: (token: string, id: string) => call<KbDoc>(`/admin/knowledge/documents/${encodeURIComponent(id)}`, { token }),
  kbVersions: (token: string, id: string) => call<KbVersions>(`/admin/knowledge/documents/${encodeURIComponent(id)}/versions`, { token }),
  kbVersion: (token: string, id: string, n: number) => call<KbVersionRow>(`/admin/knowledge/documents/${encodeURIComponent(id)}/versions/${n}`, { token }),
  kbAddVersion: (token: string, id: string, content: string, change_note: string, expected_row_version: number) =>
    call<{ document: KbDoc; version: KbVersionRow }>(`/admin/knowledge/documents/${encodeURIComponent(id)}/versions`, { method: "POST", token, body: { content, change_note, expected_row_version } }),
  kbRestore: (token: string, id: string, n: number) => call<{ document: KbDoc; version: KbVersionRow }>(`/admin/knowledge/documents/${encodeURIComponent(id)}/versions/${n}/restore`, { method: "POST", token, body: {} }),
  kbProcess: (token: string, id: string, n: number) => call<{ status: string; chunks: number; error?: string }>(`/admin/knowledge/documents/${encodeURIComponent(id)}/versions/${n}/process`, { method: "POST", token }),
  kbPublish: (token: string, id: string, n: number) => call<{ version: number; previous_version: number | null }>(`/admin/knowledge/documents/${encodeURIComponent(id)}/versions/${n}/publish`, { method: "POST", token }),
  kbDocAction: (token: string, id: string, action: "unpublish" | "archive" | "unarchive") => call<Record<string, unknown>>(`/admin/knowledge/documents/${encodeURIComponent(id)}/${action}`, { method: "POST", token }),
  kbFeedback: (token: string) => call<{ items: KbFeedbackRow[]; total: number }>("/admin/knowledge/feedback", { token }),
  kbPropose: (token: string, call_id: string, content: string, target: { doc_id: string } | { new_document: { doc_id: string; title: string; category: string } }) =>
    call<{ proposal: KbProposalRow; version: KbVersionRow }>("/admin/knowledge/proposals", { method: "POST", token, body: { call_id, content, ...target } }),
  kbReview: (token: string, call_id: string, decision: "approve" | "reject", note?: string) =>
    call<KbProposalRow>(`/admin/knowledge/proposals/${encodeURIComponent(call_id)}/review`, { method: "POST", token, body: { decision, ...(note ? { note } : {}) } }),
  kbAudit: (token: string, docId?: string) => call<{ items: KbAuditRow[]; next_before_id: number | null }>(`/admin/knowledge/audit?${new URLSearchParams({ limit: "20", ...(docId ? { doc_id: docId } : {}) })}`, { token }),
  // Knowledge workspace (Part 1). Staff routes also accept administrators; folder and review routes are administrators only.
  kwTree: (token: string, scope: "admin" | "staff") => call<KwTree>(`/${scope}/knowledge/tree`, { token }),
  kwRead: (token: string, id: string) => call<KwReadDoc>(`/staff/knowledge/documents/${encodeURIComponent(id)}`, { token }),
  kwSearch: (token: string, q: string, scope: "admin" | "staff") => call<{ items: { doc_id: string; title: string; category: string }[]; total: number }>(`/${scope}/knowledge/documents?${new URLSearchParams({ q, page_size: "30" })}`, { token }),
  kwHistory: (token: string, id: string) => call<{ items: KwHistoryRow[]; total: number }>(`/staff/knowledge/documents/${encodeURIComponent(id)}/versions`, { token }),
  kwVersion: (token: string, id: string, n: number) => call<KwHistoryRow & { content: string }>(`/staff/knowledge/documents/${encodeURIComponent(id)}/versions/${n}`, { token }),
  kwCreateFolder: (token: string, name: string, parent_id: string | null) => call<KwFolder>("/admin/knowledge/folders", { method: "POST", token, body: { name, ...(parent_id ? { parent_id } : {}) } }),
  kwRenameFolder: (token: string, id: string, name: string, expected_row_version: number) => call<KwFolder>(`/admin/knowledge/folders/${encodeURIComponent(id)}`, { method: "PUT", token, body: { name, expected_row_version } }),
  kwMove: (token: string, docId: string, folder_id: string | null, expected_row_version: number) =>
    call<{ doc_id: string; folder_id: string | null; row_version: number }>(`/admin/knowledge/documents/${encodeURIComponent(docId)}/folder`, { method: "PUT", token, body: { folder_id, expected_row_version } }),
  kwPreview: (token: string, docId: string, content: string, title?: string) => call<KwPreview>("/staff/knowledge/diff-preview", { method: "POST", token, body: { doc_id: docId, content, ...(title ? { title } : {}) } }),
  kwStart: (token: string, docId: string) => call<{ request: KwRequest; existing: boolean }>("/staff/knowledge/changes", { method: "POST", token, body: { doc_id: docId } }),
  kwMine: (token: string) => call<KwRequestList>("/staff/knowledge/changes?page_size=50", { token }),
  kwMyRequest: (token: string, id: string) => call<KwRequest>(`/staff/knowledge/changes/${encodeURIComponent(id)}`, { token }),
  kwSaveItem: (token: string, id: string, docId: string, content: string, expected_row_version: number) =>
    call<KwItem>(`/staff/knowledge/changes/${encodeURIComponent(id)}/items/${encodeURIComponent(docId)}`, { method: "PUT", token, body: { content, expected_row_version } }),
  kwSubmit: (token: string, id: string, description: string) => call<KwRequest>(`/staff/knowledge/changes/${encodeURIComponent(id)}/submit`, { method: "POST", token, body: { description } }),
  kwCancel: (token: string, id: string) => call<KwRequest>(`/staff/knowledge/changes/${encodeURIComponent(id)}/cancel`, { method: "POST", token }),
  kwRequests: (token: string, status?: string) => call<KwRequestList>(`/admin/knowledge/changes?${new URLSearchParams({ page_size: "50", ...(status ? { status } : {}) })}`, { token }),
  kwRequest: (token: string, id: string) => call<KwRequest>(`/admin/knowledge/changes/${encodeURIComponent(id)}`, { token }),
  kwReview: (token: string, id: string, decision: "approve" | "reject", note?: string) =>
    call<KwRequest>(`/admin/knowledge/changes/${encodeURIComponent(id)}/review`, { method: "POST", token, body: { decision, ...(note ? { note } : {}) } }),
  kwApply: (token: string, id: string) => call<KwRequest>(`/admin/knowledge/changes/${encodeURIComponent(id)}/apply`, { method: "POST", token }),
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
