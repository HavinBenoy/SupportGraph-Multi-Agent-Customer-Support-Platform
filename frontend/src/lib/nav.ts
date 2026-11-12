"use client";
import { useCallback, useEffect, useState } from "react";

/** A piece of view state kept in the URL query (?key=value), so the browser's Back and Forward buttons move between views and a reload or a shared link reopens the
 *  same view. `push` adds a history entry (navigation); `replace` does not (e.g. tidying up). Values never hold secrets or document text, only ids and view names. */
export function useUrlParam(key: string, fallback: string | null = null): [string | null, (v: string | null, mode?: "push" | "replace") => void] {
  const read = useCallback(() => (typeof window === "undefined" ? fallback : new URLSearchParams(window.location.search).get(key) ?? fallback), [key, fallback]);
  const [value, setValue] = useState<string | null>(read);
  useEffect(() => {
    const on = () => setValue(read());
    window.addEventListener("popstate", on);
    window.addEventListener("sg:url", on);
    return () => { window.removeEventListener("popstate", on); window.removeEventListener("sg:url", on); };
  }, [read]);
  const set = useCallback((v: string | null, mode: "push" | "replace" = "push") => {
    const u = new URL(window.location.href);
    if (v === null || v === fallback) u.searchParams.delete(key); else u.searchParams.set(key, v);
    if (u.href !== window.location.href) window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", u);
    setValue(v ?? fallback);
    window.dispatchEvent(new Event("sg:url"));
  }, [key, fallback]);
  return [value, set];
}

/** Several keys changed as one navigation step (one Back press undoes all of them). `origin` marks the new history entry as opened from inside the app
 *  (e.g. "kb:list" = from the Knowledge Base list): an in-app Back button may then safely use history.back(); without the mark it must use its fallback. */
export function setUrlParams(values: Record<string, string | null>, mode: "push" | "replace" = "push", origin?: string) {
  const u = new URL(window.location.href);
  for (const [k, v] of Object.entries(values)) { if (v === null) u.searchParams.delete(k); else u.searchParams.set(k, v); }
  const state = origin ? { sgOrigin: origin } : {};
  if (u.href !== window.location.href) window.history[mode === "push" ? "pushState" : "replaceState"](state, "", u);
  window.dispatchEvent(new Event("sg:url"));
}

/** Where the current history entry was opened from inside the app (see setUrlParams `origin`), or null when it was not (a typed / bookmarked / shared URL,
 *  a reload of such a URL, or an entry pushed by another part of the app). */
export function urlOrigin(): string | null {
  if (typeof window === "undefined") return null;
  const st = window.history.state as { sgOrigin?: unknown } | null;
  return st && typeof st.sgOrigin === "string" ? st.sgOrigin : null;
}

/** In-app Back: return to the view this entry was opened from when the app opened it (history.back() lands exactly there), otherwise go to `fallback`
 *  WITHOUT touching the browser history before it (so a document opened from a bookmark never "goes back" into an unrelated page or another admin view). */
export function goBack(prefix: string, fallback: () => void) {
  const o = urlOrigin();
  if (o && o.startsWith(prefix) && window.history.length > 1) window.history.back();
  else fallback();
}
