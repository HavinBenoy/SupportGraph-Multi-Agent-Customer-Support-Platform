"use client";
import { AdminDashboard } from "@/components/AdminDashboard";
import { RequireRole } from "@/components/RequireRole";

export default function AdminPage() {
  return <RequireRole roles={["admin"]}><AdminDashboard /></RequireRole>;
}
