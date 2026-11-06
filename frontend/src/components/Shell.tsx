"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/auth";

export function Shell({ children }: { children: ReactNode }) {
  const { session, signOut } = useAuth();
  const path = usePathname();
  const staff = session && (session.role === "staff" || session.role === "admin");
  const link = (href: string, label: string) => <Link href={href} aria-current={path === href ? "page" : undefined}>{label}</Link>;
  return (
    <>
      <header className="top">
        <strong className="brand">SupportGraph</strong>
        <nav aria-label="Main">
          {link("/", "Chat")}
          {link("/voice", "Voice")}
          {staff && link("/agent", "Staff console")}
          {session?.role === "admin" && link("/admin", "Analytics")}
        </nav>
        <span className="who">
          {session ? <>{session.username} <span className="pill">{session.role}</span> <button className="btn small" onClick={signOut}>Sign out</button></> : <Link href="/login">Sign in</Link>}
        </span>
      </header>
      <main>{children}</main>
    </>
  );
}
