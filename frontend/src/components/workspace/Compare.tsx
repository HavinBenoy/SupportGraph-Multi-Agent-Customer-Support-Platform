"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DiffSeg, KwItem, KwPatch } from "@/lib/types";
import { baseText } from "./PatchView";

/* ------------------------------------------------------------------------------------------------------------------------------- word diff
 * A word-level diff computed in the browser AFTER generation, purely for display: it never changes what was generated, validated or stored.
 * Tokens are words, runs of whitespace and single punctuation marks, so a changed number, a changed word in a sentence, a changed list item or a changed table cell is
 * highlighted on its own; unchanged text stays plain. Myers' O(ND) algorithm after trimming the common prefix and suffix (edits are usually small and local). */
const TOKEN = /\s+|[\p{L}\p{N}_₹$€.,:/-]*[\p{L}\p{N}]+[\p{L}\p{N}_]*|[^\s]/gu;
export const tokens = (s: string): string[] => s.match(TOKEN) ?? [];

const MAX_D = 4000;

function myers(a: string[], b: string[]): DiffSeg[] | null {
  const n = a.length, m = b.length, max = n + m, off = max + 1;
  const v = new Int32Array(2 * max + 3);
  const trace: Int32Array[] = [];
  for (let d = 0; d <= Math.min(max, MAX_D); d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) { x++; y++; }
      v[off + k] = x;
      if (x >= n && y >= m) {
        const out: DiffSeg[] = [];
        let cx = n, cy = m;
        for (let dd = d; dd > 0; dd--) {
          const pv = trace[dd];
          const kk = cx - cy;
          const prevK = kk === -dd || (kk !== dd && pv[off + kk - 1] < pv[off + kk + 1]) ? kk + 1 : kk - 1;
          const px = pv[off + prevK], py = px - prevK;
          while (cx > px && cy > py) { out.push({ op: "equal", text: a[cx - 1] }); cx--; cy--; }
          if (cx === px) out.push({ op: "insert", text: b[cy - 1] }); else out.push({ op: "delete", text: a[cx - 1] });
          cx = px; cy = py;
        }
        while (cx > 0 && cy > 0) { out.push({ op: "equal", text: a[cx - 1] }); cx--; cy--; }
        return out.reverse();
      }
    }
  }
  return null;                                                                                    // too different to diff word by word
}

function merge(segs: DiffSeg[]): DiffSeg[] {
  const out: DiffSeg[] = [];
  for (const s of segs) {
    if (!s.text) continue;
    const last = out[out.length - 1];
    if (last && last.op === s.op) last.text += s.text; else out.push({ ...s });
  }
  return out;
}

export function wordDiff(a: string, b: string): DiffSeg[] {
  if (a === b) return a ? [{ op: "equal", text: a }] : [];
  const ta = tokens(a), tb = tokens(b);
  let p = 0;
  while (p < ta.length && p < tb.length && ta[p] === tb[p]) p++;
  let s = 0;
  while (s < ta.length - p && s < tb.length - p && ta[ta.length - 1 - s] === tb[tb.length - 1 - s]) s++;
  const midA = ta.slice(p, ta.length - s), midB = tb.slice(p, tb.length - s);
  const mid = myers(midA, midB) ?? [{ op: "delete" as const, text: midA.join("") }, { op: "insert" as const, text: midB.join("") }];
  return merge([{ op: "equal", text: ta.slice(0, p).join("") }, ...mid, { op: "equal", text: ta.slice(ta.length - s).join("") }]);
}

/** Apply line patches (offsets into the base text) to the base. */
export function applyPatches(base: string, edits: KwPatch[] | null | undefined): string {
  let out = base;
  for (const e of [...(edits ?? [])].sort((x, y) => y.start - x.start)) {
    if (base.slice(e.start, e.end) !== e.original) continue;
    out = out.slice(0, e.start) + e.replacement + out.slice(e.end);
  }
  return out;
}

/** The versions of one document in a request. `ai` is the AI's own proposal (kept even after a manual edit: history is never overwritten); `manual` is a human revision
 *  that differs from it (staff "Edit manually", or the administrator's final edit). */
export function versionsOf(it: KwItem): { original: string; ai: string | null; manual: string | null; manualBy: "staff" | "admin" | null } {
  const original = baseText(it.diff?.body);
  const ai = it.ai?.edits?.length ? applyPatches(original, it.ai.edits) : null;
  const final = it.proposed_content ?? null;
  const human = it.admin_edited ? "admin" : it.decision === "manual" || (it.origin === "manual" && !ai) ? "staff" : null;
  const manual = human && final !== null && final !== (ai ?? original) ? final : null;
  return { original, ai, manual, manualBy: manual ? human : null };
}

export const changeCount = (segs: DiffSeg[]) => {
  let blocks = 0, added = 0, removed = 0, prev = "equal";
  for (const s of segs) {
    if (s.op !== "equal" && prev === "equal") blocks++;
    const w = s.text.split(/\s+/).filter(Boolean).length;
    if (s.op === "insert") added += w; else if (s.op === "delete") removed += w;
    prev = s.op;
  }
  return { blocks, added, removed };
};

/** One fixed-height, scrollable panel with the complete text; only the changed words are marked. `show` picks which side of the diff this panel is. */
function Panel({ title, segs, show, label, tone }: { title: string; segs: DiffSeg[]; show: "old" | "new" | "both"; label: string; tone?: "ai" | "manual" }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {                                                                                // open each panel at its first change (the text above stays scrollable)
    const el = ref.current, mark = el?.querySelector("del, ins") as HTMLElement | null;
    if (el && mark) el.scrollTop = Math.max(0, mark.offsetTop - el.offsetTop - 48);
  }, [segs]);
  return (
    <div className={`kw-cmp-panel${tone ? ` ${tone}` : ""}`}>
      <p className="kw-cmp-title">{title}</p>
      <pre ref={ref} className="kw-cmp-text" role="region" aria-label={label} tabIndex={0}>
        {segs.map((s, i) => s.op === "equal" ? <span key={i}>{s.text}</span>
          : s.op === "delete" ? (show !== "new" ? <del key={i} className="kw-del"><span className="sr-only">Removed: </span>{s.text}</del> : null)
          : (show !== "old" ? <ins key={i} className="kw-ins"><span className="sr-only">Added: </span>{s.text}</ins> : null))}
      </pre>
    </div>
  );
}

/** Original | AI proposal | Manual revision, side by side. The first two panels show the same diff from each side (removed words in the original, added words in the
 *  proposal); the third, only when a person changed the AI's proposal, marks just what that person changed relative to the AI. */
export function CompareView({ it, preview, name }: { it: KwItem; preview?: string | null; name?: string }) {
  const v = useMemo(() => versionsOf(it), [it]);
  const doc = name ?? it.doc_title;
  const proposal = v.ai ?? (v.manual ?? it.proposed_content ?? v.original);
  const third = preview !== undefined && preview !== null ? (preview !== proposal ? preview : null) : v.ai ? v.manual : null;
  const first = useMemo(() => wordDiff(v.original, proposal), [v.original, proposal]);
  const second = useMemo(() => (third !== null ? wordDiff(proposal, third) : null), [proposal, third]);
  const [mode, setMode] = useState<"side" | "inline">("side");
  const n = changeCount(first);
  const n2 = second ? changeCount(second) : null;
  const firstTitle = v.ai ? "AI proposal" : v.manual ? "Manual revision" : "Proposed";
  return (
    <section className="kw-cmp" aria-label={`Comparison for ${doc}`}>
      <div className="kw-cmp-bar">
        <p className="kw-muted tiny" role="status">
          {n.blocks === 0 ? "No difference from the published text." : `${n.blocks} change${n.blocks === 1 ? "" : "s"} · +${n.added} / −${n.removed} words`}
          {n2 && n2.blocks > 0 ? ` · ${preview != null ? "your edit" : v.manualBy === "admin" ? "administrator's edit" : "manual edit"}: ${n2.blocks} more change${n2.blocks === 1 ? "" : "s"}` : ""}
        </p>
        <span className="kw-legend" aria-hidden="true"><del className="kw-del">removed</del> <ins className="kw-ins">added</ins></span>
        <div className="kw-seg small" role="group" aria-label="Layout">
          <button aria-pressed={mode === "side"} onClick={() => setMode("side")}>Side by side</button>
          <button aria-pressed={mode === "inline"} onClick={() => setMode("inline")}>Inline</button>
        </div>
      </div>
      {mode === "side" ? (
        <div className={`kw-cmp-grid${second ? " three" : ""}`}>
          <Panel title="Original (published)" segs={first} show="old" label={`Original text of ${doc}`} />
          <Panel title={firstTitle} segs={first} show="new" label={`${firstTitle} for ${doc}`} tone={v.ai ? "ai" : "manual"} />
          {second && <Panel title={preview != null ? "Your edit (not saved yet)" : v.manualBy === "admin" ? "Administrator's revision" : "Manual revision"} segs={second} show="both"
            label={`${preview != null ? "Your edit" : "Manual revision"} of ${doc}, compared with the AI proposal`} tone="manual" />}
        </div>
      ) : (
        <div className={`kw-cmp-grid${second ? " two" : " one"}`}>
          <Panel title={`Original → ${firstTitle.toLowerCase()}`} segs={first} show="both" label={`Changes to ${doc}`} tone="ai" />
          {second && <Panel title="AI proposal → manual revision" segs={second} show="both" label={`Manual changes to ${doc}`} tone="manual" />}
        </div>
      )}
    </section>
  );
}
