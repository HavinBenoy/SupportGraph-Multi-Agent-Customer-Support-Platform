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

// ---- Knowledge workspace (Part 1) ----------------------------------------------------------------------------------------------
export interface KwFolder { folder_id: string; parent_id: string | null; name: string; path: string; row_version: number; document_count: number }
export interface KwTreeDoc { doc_id: string; title: string; category: string; folder_id: string | null; status: string; published_version: number | null; latest_version: number; updated_at: string | null }
export interface KwTree { folders: KwFolder[]; documents: KwTreeDoc[]; root_document_count: number }
export interface KwCrumb { id: string; name: string }
export interface KwReadDoc {
  doc_id: string; title: string; category: string; tags: string[]; version: number; published_at: string | null; published_by: string | null; change_note: string; authored_by: string;
  checksum: string; size_bytes: number; row_version: number; content: string; folder_id: string | null; breadcrumbs: KwCrumb[]; path: string;
}
export interface KwHistoryRow { version: number; title: string; change_note: string; authored_by: string; published_at: string | null; published_by: string | null; is_current: boolean; size_bytes: number }
export type DiffOp = "equal" | "delete" | "insert";
export interface DiffSeg { op: DiffOp; text: string }
export interface KwDiff { title: DiffSeg[]; body: DiffSeg[] }
export interface KwStats { added_chars: number; deleted_chars: number; changed_blocks: number; title_changed?: boolean }
export interface KwPreview { doc_id: string; base_version: number; diff: KwDiff; stats: KwStats; unchanged: boolean }
export type KwStatus = "draft" | "analysing" | "awaiting_clarification" | "awaiting_decisions" | "submitted" | "under_review" | "needs_revision" | "rejected" | "approved" | "processing"
  | "publishing" | "published" | "failed" | "cancelled" | "applied";
export type KwClass = "definite" | "potential" | "related_no_change";
export type KwDecision = "accepted" | "manual" | "discarded";
/** One proposed edit of a whole line of the BASE (published) text: [start, end) offsets into it. */
export interface KwPatch { start: number; end: number; original: string; replacement: string }
export interface KwAiEdit extends KwPatch { heading: string; verdict: string; confidence: number; reason: string }
export interface KwAiPassage { n: number; start: number; end: number; text: string; heading: string; verdict: string; confidence: number; reason: string; replacement: string | null }
export interface KwAi {
  reason: string | null; signals: Record<string, unknown> | null; uncertainty: string[] | null; flags: Record<string, number> | null; path: string | null;
  passages: KwAiPassage[] | null; edits: KwAiEdit[] | null;
}
export interface KwItem {
  doc_id: string; doc_title: string; base_version: number; current_published_version: number | null; base_stale: boolean; proposed_title: string; stats: KwStats; origin: string;
  decision: string | null; rationale: string | null; row_version: number; result_version: number | null; diff?: KwDiff; proposed_content?: string;
  base_checksum?: string; proposed_checksum?: string; classification?: KwClass | null; confidence?: number | null; decided_by?: string | null; decided_at?: string | null;
  admin_decision?: "confirmed" | "excluded" | null; admin_note?: string | null; admin_by?: string | null; admin_at?: string | null; admin_edited?: boolean;
  included?: boolean; staff_included?: boolean; actionable?: boolean; process_status?: "pending" | "processing" | "ready" | "failed" | null;
  publish_status?: "pending" | "published" | "not_published" | "failed" | null; process_error?: string | null; ai?: KwAi | null; final_edits?: KwPatch[] | null;
  overlapping_requests?: { request_id: string; status: KwStatus; created_by: string }[];
}
export interface KwQuestion { id: string; doc_id?: string | null; text: string; options: string[] }
export interface KwAnalysis {
  stage: string | null; done: number | null; total: number | null; error: string | null; model: string | null; attempt: number; started_at: string | null; completed_at: string | null;
  questions: KwQuestion[]; answers: Record<string, string>;
  summary: { discovered: number; definite: number; potential: number; related_no_change: number; not_relevant: { doc_id: string; title: string; reason: string }[];
    flags: { unsupported: number; historical: number; scope: number } } | null;
  spec: { summary: string; facts: { attribute: string; old_value: string | null; new_value: string | null; effective: string | null }[]; entity_terms: string[] } | null;
}
export interface KwCounts { accepted: number; edited: number; discarded: number; pending: number; manual_items: number; related: number; included: number; excluded_by_admin: number }
export interface KwRelease { versions: Record<string, number>; previous: Record<string, number>; attempts: number; model: string; published_at: string }
export interface KwFailure { stage: string; message: string; documents: string[] }
export interface KwRequest {
  request_id: string; status: KwStatus; source: string; description: string | null; row_version: number; created_by: string; created_at: string | null; updated_at: string | null;
  submitted_by: string | null; submitted_at: string | null; reviewed_by: string | null; reviewed_at: string | null; review_note: string | null; applied_by: string | null; applied_at: string | null;
  document_count: number; documents: { doc_id: string; title: string }[]; added_chars: number; deleted_chars: number; items?: KwItem[];
  return_note?: string | null; reference_doc_id?: string | null; failure?: KwFailure | null; published_by?: string | null; published_at?: string | null; release_attempt?: number;
  release?: KwRelease | null; phase_started_at?: string | null; counts?: KwCounts; analysis?: KwAnalysis | null;
}
export interface KwAuditEvent { id: number; at: string | null; actor: string; action: string; doc_id: string | null; version: number | null; summary: Record<string, string | number | boolean> }
export interface KwRequestList { items: KwRequest[]; total: number; page: number; page_size: number }
