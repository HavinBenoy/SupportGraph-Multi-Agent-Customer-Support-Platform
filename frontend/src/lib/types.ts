export type Role = "student" | "staff" | "admin";

export interface Session { token: string; role: Role; username: string; tenantId: string; studentId: string | null; expiresAt: number }

export type ChatStatus = "answered" | "needs_detail" | "escalated" | "awaiting_staff" | "awaiting_confirmation" | "done";
export interface Citation { id: string; heading: string }
export interface ChatReply { ticket_id: string; reply: string; status: ChatStatus; citations: Citation[]; escalation_id: string | null; signed_in: boolean }
export interface HistoryMessage { role: "user" | "assistant"; content: string }
export interface History { ticket_id: string; messages: HistoryMessage[]; awaiting: string | null }
export interface PublicConfig { tenant_id: string; tenant_name: string; is_sample: boolean }

export interface EscalationRow { id: string; ticket_id: string; team: string; priority: string; reason: string; status: string; claimed_by: string | null; created_at: string }
export interface EscalationBrief {
  student_id?: string; student_name?: string | null; summary?: string; excerpt?: string; suggested_next_step?: string; sla_hours?: number;
  batches?: { batch_id: string; attendance_pct?: number | null }[]; trail?: Record<string, unknown>; [k: string]: unknown;
}
export interface EscalationDetail extends EscalationRow { brief: EscalationBrief; resolution_note: string | null; resolved_at: string | null }
export interface Approval { request_id: string; ticket_id: string | null; student_id: string; student_name: string | null; batch_id: string; session_date: string; reason: string; created_at: string }
export interface Decision { outcome: string; message: string }

export interface Analytics {
  tenant_id: string; days: number; generated_at: string;
  chat: { turns: number; by_route: Record<string, number>; signed_in_share: number | null; abstain_rate: number | null; qa_blocked: number;
          self_service_rate: number | null; latency_ms: { p50: number | null; p95: number | null }; per_day: { date: string; turns: number }[]; [k: string]: unknown };
  escalations: { created: number; open: number; claimed: number; resolved: number; by_team: Record<string, number>; by_priority: Record<string, number>;
                 by_reason: Record<string, number>; median_hours_to_resolve: number | null; sla_breaches_open: number; oldest_open_hours: number | null };
  approvals: { pending: number; approved: number; rejected: number; median_hours_to_decide: number | null; [k: string]: unknown };
  tickets_by_status: Record<string, number>;
  notes: string[];
}

/** M4/M4.1: one call as the staff API describes it. `ticket_ref` is a short reference, never the ticket id; `student_id` only comes for admins.
 *  `returning_to_ai` exists only so a row left by M4 still displays: a call can no longer go back to the assistant. */
export type CallStatus = "created" | "active_ai" | "staff_takeover" | "returning_to_ai" | "ended" | "failed";
/** The four status colours (plus `failed` for a call that broke down with no human involved). Listening is never an intervention. */
export type CallCategory = "ai_handling" | "human_handling" | "ended_normally" | "ended_after_intervention" | "failed";
export type FeedbackState = "awaiting" | "skipped" | "submitted";
export type ReviewStatus = "pending_review" | "reviewed" | "approved_for_update" | "rejected";
export interface FeedbackBrief { state: FeedbackState; form_open: boolean; review_status: ReviewStatus | null }
export interface LiveCall {
  id: string; short_id: string; status: CallStatus; category: CallCategory; owner: "ai" | "staff" | "none"; owner_staff: string | null; epoch: number;
  ticket_ref: string; caller: "student" | "anonymous"; student_id?: string;
  created_at: string; connected_at: string | null; takeover_at: string | null; ended_at: string | null; end_reason: string | null;
  duration_s: number; intervened: boolean; intervention_by: string | null; trial_mode: boolean;
  live: boolean; listeners: number; listening_staff: string[]; mic_open: boolean; announcing: boolean; is_mine: boolean; listening: boolean;
  can_takeover: boolean; can_end: boolean; can_listen: boolean;
  feedback?: FeedbackBrief;        // only present for the staff member who took the call over
}
export type CallAction = "listen" | "takeover" | "end";

/** M4.1: the complete transcript of a call (customer, assistant, system lines, and when staff spoke: never their words). */
export interface TranscriptEntry {
  speaker: "customer" | "ai" | "staff" | "system"; text: string | null; at: string | null; index?: number; message_id?: string | null; earlier?: boolean;
  code?: string; kind?: "speech"; until?: string | null; duration_s?: number | null; staff?: string; note?: string; status?: string; reason?: string | null;
}
export interface Transcript { call_id: string; entries: TranscriptEntry[]; notes: string[]; complete: boolean }

/** M4.1: post-call intervention feedback (a PROPOSED correction: nothing here ever enters the knowledge base by itself). */
export interface Feedback {
  call_id: string; short_id: string; call_status: CallStatus; call_ended: boolean; state: FeedbackState; form_open: boolean;
  category: string | null; category_label: string | null; review_status: ReviewStatus | null; intervened_at: string | null; submitted_at: string | null; updated_at: string | null;
  skipped_at: string | null; reviewed_at: string | null; related_index: number | null; related_message_id: string | null; editable: boolean;
  what_was_wrong?: string | null; suggested_correction?: string | null; review_note?: string | null; staff?: string; reviewed_by?: string | null;
  categories?: Record<string, string>;
}
export interface FeedbackInput { category: string; what_was_wrong: string; suggested_correction?: string; related_message_id?: string; related_index?: number }
export interface Interventions { items: Feedback[]; categories: Record<string, string> }
export interface FeedbackQueue { items: Feedback[]; counts: { by_state: Record<string, number>; by_review_status: Record<string, number> }; categories: Record<string, string> }

/** M5: Trial Verification Mode. */
export interface TrialSettings {
  tenant_id: string; enabled: boolean; version: number; recording_offered: boolean; disclosure_text: string; recording_notice: string; consent_policy_version: number;
  retention_recording_days: number; retention_consent_days: number; retention_feedback_days: number; withdrawal_policy: "delete" | "keep_until_expiry";
  updated_by: string | null; updated_at: string | null; recording_notice_rendered: string;
}
export interface TrialView { settings: TrialSettings; recording: { provider: string; operational: boolean; note: string } }
export type TrialPatch = Partial<Pick<TrialSettings, "enabled" | "recording_offered" | "disclosure_text" | "recording_notice" | "retention_recording_days" | "retention_consent_days"
  | "retention_feedback_days" | "withdrawal_policy">>;
export interface TrialEventRow { id: number; kind: string; actor: string; detail: Record<string, unknown>; created_at: string | null }
export interface TrialMetrics {
  period: { from: string; to: string }; empty: boolean; truncated: boolean; calls_outside_trial: number;
  calls: { total: number; ai_handled: number; ongoing: number; ended_normally: number; failed: number; ended_after_intervention: number; requiring_escalation: number };
  human: { calls_with_listeners: number; listener_sessions: number; takeovers: number; calls_with_takeover: number; intervention_rate: number | null };
  customers: { unique_signed_in: number; anonymous_calls: number };
  durations: { avg_call_s: number | null; calls_measured: number; avg_intervention_s: number | null; interventions_measured: number };
  consent: { offered: number; granted: number; ever_granted: number; declined: number; withdrawn: number; undecided: number; not_offered: number;
             granted_rate: number | null; declined_rate: number | null; withdrawn_rate: number | null };
  recording: { recordings: number; by_status: Record<string, number>; calls_recorded: number };
  feedback: { by_state: Record<string, number>; by_review_status: Record<string, number>; reasons: Record<string, number>; no_reason_given: number };
  notes: string[];
}
export interface RetentionStatus {
  policy: { recording_days: number; consent_days: number; feedback_days: number; withdrawal_policy: string };
  provider: { name: string; operational: boolean; recording_offered: boolean };
  recordings: { by_status: Record<string, number>; held: number; eligible_now: number; purge_failing: number; next_expiry: string | null };
  consents: { eligible_now: number }; feedback: { eligible_now: number };
  last_run: ({ at: string | null; actor: string } & Record<string, unknown>) | null;
}
export interface PurgeResult { purged: number; failed: number; held: number; consents: number; feedback: number; dry_run: boolean }
export interface RecordingRow {
  id: string; call_id: string; status: "recording" | "completed" | "failed" | "purged"; started_at: string | null; stopped_at: string | null; stop_reason: string | null;
  size_bytes: number; expires_at: string | null; legal_hold: boolean; hold_reason: string | null; purged_at: string | null; purge_attempts: number; purge_error: string | null;
  consent_policy_version: number; provider: string; tracks: string[];
}

// ---- M6: knowledge store (administrators) ----
export type KbLifecycle = "draft" | "processing" | "ready_for_review" | "published" | "failed" | "superseded" | "archived";
export interface KbDoc {
  doc_id: string; title: string; category: string; description: string | null; tags: string[]; status: "draft" | "published" | "archived"; origin: "store" | "imported";
  latest_version: number; published_version: number | null; row_version: number; latest_status: KbLifecycle | null; published_at: string | null; published_by: string | null;
  created_by: string; created_at: string | null; updated_by: string | null; updated_at: string | null; archived_by: string | null; archived_at: string | null;
}
export interface KbVersionRow {
  version: number; title: string; status: string; lifecycle: KbLifecycle; checksum: string; size_bytes: number; change_note: string; created_by: string; created_at: string | null;
  restored_from: number | null; chunk_count: number | null; embedding_model: string | null; processed_at: string | null; processing_error: string | null;
  published_at: string | null; published_by: string | null; proposal: { call_id: string; status: string } | null; content?: string;
}
export interface KbList { items: KbDoc[]; total: number; page: number; page_size: number }
export interface KbVersions { items: KbVersionRow[]; total: number }
export interface KbMeta { categories: string[]; limits: { max_bytes: number; max_chunks: number }; embedding_model: string; unmanaged_disk_documents: string[] }
export interface KbNewDoc { doc_id: string; title: string; category: string; content: string; description?: string }
export interface KbProposalRow { call_id: string; doc_id: string; version: number; status: "proposed" | "approved" | "rejected" | "published"; proposed_by: string; reviewed_by: string | null; published_by: string | null; review_note: string | null }
export interface KbFeedbackRow { call_id: string; category: string; what_was_wrong: string; suggested_correction: string | null; written_by: string; proposal: KbProposalRow | null }
export interface KbAuditRow { id: number; at: string | null; actor: string; action: string; resource_id: string; doc_id: string | null; version: number | null }
