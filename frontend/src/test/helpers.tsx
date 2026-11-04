import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import { saveSession } from "@/lib/api";
import type { Role, Session } from "@/lib/types";

type Reply = { status?: number; json?: unknown; headers?: Record<string, string> };
type Handler = (body: any, url: string) => Reply | Promise<Reply>;

/** Replaces fetch with a router keyed "METHOD /api/path" (query string ignored). Returns the mock so tests can assert on calls. */
export function mockApi(routes: Record<string, Handler>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = `${init?.method ?? "GET"} ${url.split("?")[0]}`;
    const h = routes[key];
    if (!h) throw new Error(`unmocked request: ${key}`);
    const r = await h(init?.body ? JSON.parse(String(init.body)) : undefined, url);
    return new Response(JSON.stringify(r.json ?? {}), { status: r.status ?? 200, headers: { "Content-Type": "application/json", ...(r.headers ?? {}) } });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function signedIn(role: Role, username = "u1"): Session {
  const s: Session = { token: `tok-${username}`, role, username, tenantId: "t1", studentId: role === "student" ? "stu_1" : null, expiresAt: Date.now() + 3600_000 };
  saveSession(s);
  return s;
}

export const renderWithAuth = (ui: ReactElement) => render(<AuthProvider>{ui}</AuthProvider>);
export const CONFIG = { tenant_id: "t1", tenant_name: "Test Institute", is_sample: true };
