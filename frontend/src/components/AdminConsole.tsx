"use client";
import { useState } from "react";
import { AdminDashboard } from "./AdminDashboard";
import { KnowledgeAdmin } from "./KnowledgeAdmin";
import { TrialAdmin } from "./TrialAdmin";

type Tab = "analytics" | "knowledge" | "trial";

/** Administrator area: the analytics (unchanged), from M5 Trial verification and from M6 the Knowledge store. */
export function AdminConsole() {
  const [tab, setTab] = useState<Tab>("analytics");
  return (
    <div>
      <div role="tablist" aria-label="Administration" className="tabs">
        <button role="tab" aria-selected={tab === "analytics"} onClick={() => setTab("analytics")}>Analytics</button>
        <button role="tab" aria-selected={tab === "knowledge"} onClick={() => setTab("knowledge")}>Knowledge</button>
        <button role="tab" aria-selected={tab === "trial"} onClick={() => setTab("trial")}>Trial verification</button>
      </div>
      {tab === "analytics" ? <AdminDashboard /> : tab === "knowledge" ? <KnowledgeAdmin /> : <TrialAdmin />}
    </div>
  );
}
