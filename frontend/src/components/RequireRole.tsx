"use client";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import type { Role } from "@/lib/types";
import { LoginForm } from "./LoginForm";

/** Gate for the staff and admin pages. This is a usability gate only; the server enforces roles on every request. */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { session, ready } = useAuth();
  if (!ready) return <p className="muted" aria-busy="true">Loading…</p>;
  if (!session) return <div className="narrow"><p className="muted">This area is for institute staff.</p><LoginForm heading="Staff sign in" /></div>;
  if (!roles.includes(session.role)) return <div className="card narrow" role="alert"><h2>Not allowed</h2><p>Your account ({session.role}) cannot open this page.</p></div>;
  return <>{children}</>;
}
