"use client";
import { RequireRole } from "@/components/RequireRole";
import { StaffConsole } from "@/components/StaffConsole";

export default function AgentPage() {
  return <RequireRole roles={["staff", "admin"]}><StaffConsole /></RequireRole>;
}
