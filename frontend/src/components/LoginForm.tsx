"use client";
import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Session } from "@/lib/types";

export function LoginForm({ onDone, heading = "Sign in" }: { onDone?: (s: Session) => void; heading?: string }) {
  const { signIn } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { onDone?.(await signIn(username, password)); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Sign-in failed."); }
    finally { setBusy(false); }
  }

  return (
    <form className="form" onSubmit={submit} aria-labelledby="login-h">
      <h2 id="login-h">{heading}</h2>
      <p className="muted tiny">Use the account your administrator gave you.</p>
      <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required /></label>
      <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
      {error && <p role="alert" className="error">{error}</p>}
      <button className="btn primary" disabled={busy || !username || !password}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}
