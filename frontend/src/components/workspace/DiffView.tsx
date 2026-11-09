import type { DiffSeg } from "@/lib/types";

/** Renders a server-computed structured diff. Deleted text stays visible (red, struck through), added text is green (underlined), unchanged text is plain.
 *  Colour is never the only signal: screen readers hear "Deleted:" / "Added:" and the marks differ in shape. The data is rendered as text nodes, never as HTML. */
export function DiffView({ segs, label = "Changes" }: { segs: DiffSeg[]; label?: string }) {
  return (
    <pre className="kw-diff" role="region" aria-label={label} tabIndex={0}>
      {segs.map((s, i) =>
        s.op === "equal" ? <span key={i}>{s.text}</span>
        : s.op === "delete" ? <del key={i} className="kw-del"><span className="sr-only">Deleted: </span>{s.text}</del>
        : <ins key={i} className="kw-ins"><span className="sr-only">Added: </span>{s.text}</ins>)}
    </pre>
  );
}

export function DiffLegend() {
  return (
    <p className="kw-legend" aria-hidden="true">
      <del className="kw-del">Removed</del> <ins className="kw-ins">Added</ins> <span>Unchanged</span>
    </p>
  );
}

export function Stats({ added, deleted }: { added: number; deleted: number }) {
  return <span className="kw-stats"><span className="kw-plus">+{added}</span> <span className="kw-minus">−{deleted}</span></span>;
}
