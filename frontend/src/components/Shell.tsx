"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

/** The application bar: brand, the sections the signed-in role may use, and the account. One bar for every page (the landing page included), collapsing into a menu
 *  on small screens. Role visibility is unchanged: the server enforces it again on every request. */
export function Shell({ children }: { children: ReactNode }) {
  const { session, signOut } = useAuth();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  const staff = session?.role === "staff";
  const link = (href: string, label: string) => (
    <Link href={href} aria-current={path === href || (href !== "/" && path?.startsWith(href + "/")) ? "page" : undefined}>{label}</Link>);
  return (
    <>
      <a className="skip" href="#content">Skip to content</a>
      <header className={`top${open ? " open" : ""}`}>
        <Link href="/" className="brand" aria-label="SupportGraph home"><span className="brand-mark" aria-hidden="true">SG</span>SupportGraph</Link>
        <button className="btn small ghost menu-toggle" aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "Menu"}</button>
        <nav aria-label="Main" id="main-nav">
          {link("/chat", "Chat")}
          {link("/voice", "Voice")}
          {staff && link("/agent", "Staff console")}
          {session?.role === "admin" && link("/admin", "Administration")}
        </nav>
        <span className="who">
          {session ? <>{session.username} <span className="pill">{session.role}</span> <button className="btn small" onClick={signOut}>Sign out</button></>
            : <Link className="btn small primary" href="/login">Sign in</Link>}
        </span>
      </header>
      <main id="content">{children}</main>
    </>
  );
}
