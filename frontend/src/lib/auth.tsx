"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, loadSession, saveSession } from "./api";
import type { Role, Session } from "./types";

interface AuthCtx {
  session: Session | null;
  ready: boolean; // false until sessionStorage has been read (avoids a flash of the signed-out UI)
  signIn: (username: string, password: string) => Promise<Session>;
  signOut: () => void;
  /** Run an API call with the token; a 401 (expired/disabled) signs the user out so the UI returns to the login screen. */
  withToken: <T>(fn: (token: string) => Promise<T>) => Promise<T>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => { setSession(loadSession()); setReady(true); }, []);

  const signOut = useCallback(() => { saveSession(null); setSession(null); }, []);
  const signIn = useCallback(async (u: string, p: string) => {
    const s = await api.login(u.trim(), p);
    saveSession(s); setSession(s);
    return s;
  }, []);
  const withToken = useCallback(async <T,>(fn: (t: string) => Promise<T>) => {
    if (!session) throw new ApiError(401, "Please sign in.");
    try { return await fn(session.token); }
    catch (e) { if (e instanceof ApiError && e.isAuth) signOut(); throw e; }
  }, [session, signOut]);

  const value = useMemo(() => ({ session, ready, signIn, signOut, withToken }), [session, ready, signIn, signOut, withToken]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth must be used inside <AuthProvider>");
  return c;
}

export const homeFor = (role: Role) => (role === "admin" ? "/admin" : role === "staff" ? "/agent" : "/");
