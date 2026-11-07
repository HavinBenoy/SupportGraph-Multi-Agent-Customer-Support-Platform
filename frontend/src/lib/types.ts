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

/** M4: one call as the staff API describes it. `ticket_ref` is a short reference, never the ticket id; `student_id` only comes for admins. */
export type CallStatus = "created" | "active_ai" | "staff_takeover" | "returning_to_ai" | "ended" | "failed";
export interface LiveCall {
  id: string; short_id: string; status: CallStatus; owner: "ai" | "staff" | "none"; owner_staff: string | null; epoch: number;
  ticket_ref: string; caller: "student" | "anonymous"; student_id?: string;
  created_at: string; connected_at: string | null; takeover_at: string | null; returned_at: string | null; ended_at: string | null; end_reason: string | null;
  duration_s: number; live: boolean; listeners: number; listening_staff: string[]; mic_open: boolean; is_mine: boolean; listening: boolean;
}
export type CallAction = "listen" | "takeover" | "return" | "end";
