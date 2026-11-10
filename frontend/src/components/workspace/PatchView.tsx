import type { DiffSeg, KwPatch } from "@/lib/types";

/** The original (published) text of a document, rebuilt from the server's structured diff: everything that is not an insertion. */
export const baseText = (segs: DiffSeg[] | undefined) => (segs ?? []).filter((s) => s.op !== "insert").map((s) => s.text).join("");

export interface PatchEdit { start: number; end: number; original: string; replacement: string | null; heading?: string; kind?: "replace" | "add" | "remove" | "modify" }
const KIND_TAG: Record<string, string> = { add: "Added", remove: "Removed", modify: "Rewritten" };

/** Renders a structured patch over the ORIGINAL text:
 *   orange, tagged "Existing"  = the passage the AI says needs to change
 *   blue,   tagged "New"       = the replacement text
 *  Each mark carries a visible tag and a screen-reader label, and the two differ in border style, so colour is never the only signal. Everything is rendered as text nodes, never
 *  as HTML. Only a window around each edit is shown unless `full` is set. If an edit no longer matches the text it was made for, that is said instead of guessing. */
export function PatchView({ base, edits, label, full = false, context = 90 }: { base: string; edits: PatchEdit[]; label: string; full?: boolean; context?: number }) {
  const sorted = [...edits].sort((a, b) => a.start - b.start);
  const bad = sorted.some((e) => base.slice(e.start, e.end) !== e.original);
  const parts: React.ReactNode[] = [];
  let pos = 0;
  const gap = (from: number, to: number, first: boolean, last: boolean, key: string) => {
    const text = base.slice(from, to);
    if (full || text.length <= context * 2) return parts.push(<span key={key}>{text}</span>);
    parts.push(<span key={key}>{first ? "" : text.slice(0, context)}{first ? "…" : last ? "" : " … "}{last ? "" : text.slice(-context)}{first ? text.slice(-context) : ""}{last ? "…" : ""}</span>);
  };
  sorted.forEach((e, i) => {
    if (e.start > pos) gap(pos, e.start, i === 0, false, `g${i}`);
    if (e.heading) parts.push(<span key={`h${i}`} className="kw-where">{e.heading}</span>);
    if (e.kind && KIND_TAG[e.kind]) parts.push(<span key={`k${i}`} className="kw-chip kw-kind" title="What kind of change the AI proposes here">{KIND_TAG[e.kind]}</span>);
    parts.push(<mark key={`o${i}`} className="kw-old"><span className="kw-tag" aria-hidden="true">Existing</span><span className="sr-only">Existing passage the AI says needs to change: </span>{e.original}</mark>);
    if (e.replacement !== null) parts.push(<mark key={`n${i}`} className="kw-new"><span className="kw-tag" aria-hidden="true">New</span><span className="sr-only">AI replacement: </span>{e.replacement === "" ? "(removed)" : e.replacement}</mark>);
    pos = e.end;
  });
  if (pos < base.length) {
    const text = base.slice(pos);
    parts.push(<span key="tail">{full || text.length <= context ? text : `${text.slice(0, context)}…`}</span>);
  }
  return (
    <div>
      {bad && <p className="kw-banner warn">This suggestion no longer lines up with the text it was made for. Re-run the analysis.</p>}
      <pre className="kw-patch" role="region" aria-label={label} tabIndex={0}>{parts}</pre>
    </div>
  );
}

export function PatchLegend() {
  return (
    <p className="kw-legend" aria-hidden="true">
      <mark className="kw-old"><span className="kw-tag">Existing</span> passage to modify</mark> <mark className="kw-new"><span className="kw-tag">New</span> replacement</mark>
    </p>
  );
}

export const toEdits = (patches: KwPatch[] | null | undefined, headings?: (string | undefined)[]): PatchEdit[] => (patches ?? []).map((p, i) => ({ ...p, heading: headings?.[i] }));
