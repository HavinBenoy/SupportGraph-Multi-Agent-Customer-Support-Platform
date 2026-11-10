import { ApiError } from "@/lib/api";
import type { KwClass, KwStatus } from "@/lib/types";

export const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");
export const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");
export const pct = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${Math.round(n * 100)}%`);

export const LABEL: Record<KwStatus, string> = {
  draft: "Draft", analysing: "Analysing", awaiting_clarification: "Needs your answer", awaiting_decisions: "Awaiting your decisions", submitted: "Awaiting review", under_review: "Under review",
  needs_revision: "Returned for revision", rejected: "Rejected", approved: "Approved — not yet published", processing: "Processing", publishing: "Publishing", published: "Published",
  failed: "Failed — nothing published", cancelled: "Cancelled", applied: "Draft versions created",
};
/** Every status has a word AND a symbol, so colour is never the only signal. */
const MARK: Record<KwStatus, string> = {
  draft: "✎", analysing: "◌", awaiting_clarification: "?", awaiting_decisions: "◐", submitted: "◔", under_review: "◑", needs_revision: "↩", rejected: "✕", approved: "✓", processing: "◌",
  publishing: "◌", published: "●", failed: "!", cancelled: "—", applied: "▣",
};
export const Badge = ({ s }: { s: KwStatus }) => <span className={`kw-chip st-${s}`}><span aria-hidden="true">{MARK[s]} </span>{LABEL[s]}</span>;

export const CLASS_LABEL: Record<KwClass, string> = { definite: "Definitely affected", potential: "Potentially affected — needs confirmation", related_no_change: "Related — no change needed" };
export const ClassChip = ({ c }: { c: KwClass | null | undefined }) => (c ? <span className={`kw-chip cl-${c}`}><span aria-hidden="true">{c === "definite" ? "■" : c === "potential" ? "▲" : "○"} </span>{CLASS_LABEL[c]}</span> : null);

export const DECISION_LABEL: Record<string, string> = { accepted: "Accepted", manual: "Edited by you", discarded: "Discarded" };
export const DecisionChip = ({ d }: { d: string | null | undefined }) => (
  <span className={`kw-chip dc-${d ?? "pending"}`}><span aria-hidden="true">{d === "accepted" ? "✓ " : d === "manual" ? "✎ " : d === "discarded" ? "✕ " : "… "}</span>{d ? DECISION_LABEL[d] ?? d : "Needs a decision"}</span>
);

export const STEP_STATUS: KwStatus[] = ["submitted", "under_review", "approved", "processing", "publishing", "published"];

export const KIND_LABEL: Record<string, string> = { definite: "Definitely affected", potential: "Potentially affected", ambiguous: "Contradictory or ambiguous", related: "Related — no change", historical: "Historical — keep", not_relevant: "Not relevant" };
