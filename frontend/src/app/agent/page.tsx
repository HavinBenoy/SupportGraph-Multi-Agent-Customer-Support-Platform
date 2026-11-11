"use client";
import { RequireRole } from "@/components/RequireRole";
import { StaffConsole } from "@/components/StaffConsole";

/** The staff console, for staff and administrators (final M6: administrators keep the full console as well as /admin). The server enforces RBAC on every call. */
export default function AgentPage() {
  return <RequireRole roles={["staff", "admin"]}><StaffConsole /></RequireRole>;
}
