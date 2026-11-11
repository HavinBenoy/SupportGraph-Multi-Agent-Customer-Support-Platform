"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { RequireRole } from "@/components/RequireRole";
import { StaffConsole } from "@/components/StaffConsole";
import { useAuth } from "@/lib/auth";

/** The staff console is for staff. Administrators have their own area (/admin) and are sent there; the server's RBAC is unchanged (it still lets an administrator
 *  call staff APIs used by admin workflows). */
export default function AgentPage() {
  const { session } = useAuth();
  const router = useRouter();
  useEffect(() => { if (session?.role === "admin") router.replace("/admin"); }, [session, router]);
  if (session?.role === "admin") return <p className="muted">Administrators use <Link href="/admin">Administration</Link>…</p>;
  return <RequireRole roles={["staff"]}><StaffConsole /></RequireRole>;
}
