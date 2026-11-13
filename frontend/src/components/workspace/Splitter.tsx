"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";

/** Refinement 2.1: a draggable, keyboard-accessible divider for a list | detail layout (`.kw-md`).
 *
 * - It sets `--kw-md-left` on the container; the CSS grid reads it, so nothing re-renders the list or the detail while dragging.
 * - Width is clamped to [min, maxFraction × container width]; the window is re-clamped on resize.
 * - Pointer capture keeps the drag even when the pointer leaves the divider; text selection is suppressed only while dragging (`kw-resizing` on <body>).
 * - Keyboard: ←/→ (Shift = larger steps), Home/End, Enter or double-click resets.
 * - The width is remembered for the browser session (sessionStorage, guarded: private windows simply don't remember).
 * - Under the CSS breakpoint the layout stacks and the divider is hidden (CSS), so phones keep the one-pane-at-a-time behaviour. */
export const SPLIT_MIN = 280;
export const SPLIT_MAX_FRACTION = 0.65;
const STEP = 16;
const BIG_STEP = 64;

const readStored = (key: string): number | null => {
  try {
    const v = Number(window.sessionStorage.getItem(key));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch { return null; }
};
const store = (key: string, v: number) => { try { window.sessionStorage.setItem(key, String(Math.round(v))); } catch { /* storage unavailable: not remembered */ } };

export function clampWidth(w: number, containerWidth: number, min = SPLIT_MIN, maxFraction = SPLIT_MAX_FRACTION) {
  const max = Math.max(min, Math.floor(containerWidth * maxFraction));
  return Math.min(max, Math.max(min, Math.round(w)));
}

export function useSplitWidth(container: RefObject<HTMLElement | null>, storageKey: string, initial = 360) {
  const [width, setWidth] = useState<number>(initial);
  const [bounds, setBounds] = useState<{ min: number; max: number }>({ min: SPLIT_MIN, max: 1200 });
  const apply = useCallback((w: number) => {
    const el = container.current;
    const cw = el?.getBoundingClientRect().width || 1200;
    const next = clampWidth(w, cw);
    setBounds({ min: SPLIT_MIN, max: Math.max(SPLIT_MIN, Math.floor(cw * SPLIT_MAX_FRACTION)) });
    setWidth(next);
    el?.style.setProperty("--kw-md-left", `${next}px`);
    return next;
  }, [container]);
  useLayoutEffect(() => { apply(readStored(storageKey) ?? initial); }, [apply, storageKey, initial]);
  useEffect(() => {
    const onResize = () => apply(width);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [apply, width]);
  const commit = useCallback((w: number) => store(storageKey, apply(w)), [apply, storageKey]);
  return { width, bounds, apply, commit, initial };
}

export function Splitter({ container, split, label, controls }: {
  container: RefObject<HTMLElement | null>;
  split: ReturnType<typeof useSplitWidth>;
  label: string;
  controls?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const frame = useRef<number | null>(null);
  const keepSelectionVisible = () => {
    const sel = container.current?.querySelector<HTMLElement>(".kw-md-list .sel, .kw-md-list [aria-current='true']");
    sel?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  };
  const end = useCallback(() => {
    setDragging(false);
    document.body.classList.remove("kw-resizing");
    split.commit(split.width);
    keepSelectionVisible();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [split]);
  useEffect(() => () => document.body.classList.remove("kw-resizing"), []);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button > 0) return;                                               // primary button only (pen and touch report 0)
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDragging(true);
    document.body.classList.add("kw-resizing");
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const left = container.current?.getBoundingClientRect().left ?? 0;
    const x = e.clientX;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => { frame.current = null; split.apply(x - left); });
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    end();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? BIG_STEP : STEP;
    let next: number | null = null;
    if (e.key === "ArrowLeft") next = split.width - step;
    else if (e.key === "ArrowRight") next = split.width + step;
    else if (e.key === "Home") next = split.bounds.min;
    else if (e.key === "End") next = split.bounds.max;
    else if (e.key === "Enter") next = split.initial;
    if (next === null) return;
    e.preventDefault();
    split.commit(next);
    keepSelectionVisible();
  };
  return (
    <div className={`kw-splitter${dragging ? " dragging" : ""}`} role="separator" aria-orientation="vertical" aria-label={label} aria-controls={controls}
      aria-valuenow={split.width} aria-valuemin={split.bounds.min} aria-valuemax={split.bounds.max} tabIndex={0}
      title="Drag, or use the arrow keys, to resize. Double-click to reset."
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onLostPointerCapture={() => dragging && end()}
      onKeyDown={onKeyDown} onDoubleClick={() => { split.commit(split.initial); keepSelectionVisible(); }}>
      <span className="kw-splitter-grip" aria-hidden="true" />
    </div>
  );
}
