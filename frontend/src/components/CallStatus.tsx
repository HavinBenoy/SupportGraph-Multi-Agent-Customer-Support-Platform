import type { CallCategory, LiveCall } from "@/lib/types";

/** The status colours of M4.1. The colour is never the only signal: every badge carries its words. */
export const CATEGORY_LABEL: Record<CallCategory, string> = {
  ai_handling: "AI handling",
  human_handling: "Human handling",
  ended_normally: "Ended normally",
  ended_after_intervention: "Ended after intervention",
  failed: "Failed",
};
export const CATEGORY_HINT: Record<CallCategory, string> = {
  ai_handling: "The assistant is speaking with the caller (staff may be listening: listening is not taking over).",
  human_handling: "A staff member took this call over and the assistant is silent.",
  ended_normally: "The call ended without a staff member taking it over.",
  ended_after_intervention: "A staff member took this call over before it ended.",
  failed: "The call broke down without a staff member taking it over.",
};

export function StatusBadge({ call }: { call: Pick<LiveCall, "category"> }) {
  const c = call.category;
  return <span className={`pill status status-${c}`} title={CATEGORY_HINT[c]} data-category={c}>{CATEGORY_LABEL[c]}</span>;
}
