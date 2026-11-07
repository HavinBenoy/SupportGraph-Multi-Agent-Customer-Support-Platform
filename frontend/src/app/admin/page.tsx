"use client";
import { AdminConsole } from "@/components/AdminConsole";
import { RequireRole } from "@/components/RequireRole";

export default function AdminPage() {
  return <RequireRole roles={["admin"]}><AdminConsole /></RequireRole>;
}
