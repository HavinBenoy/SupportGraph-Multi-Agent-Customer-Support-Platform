"use client";
import { useState } from "react";
import { AdminDashboard } from "./AdminDashboard";
import { TrialAdmin } from "./TrialAdmin";

type Tab = "analytics" | "trial";

/** Administrator area: the analytics (unchanged) and, from M5, Trial verification. */
export function AdminConsole() {
  const [tab, setTab] = useState<Tab>("analytics");
  return (
    <div>
      <div role="tablist" aria-label="Administration" className="tabs">
        <button role="tab" aria-selected={tab === "analytics"} onClick={() => setTab("analytics")}>Analytics</button>
        <button role="tab" aria-selected={tab === "trial"} onClick={() => setTab("trial")}>Trial verification</button>
      </div>
      {tab === "analytics" ? <AdminDashboard /> : <TrialAdmin />}
    </div>
  );
}
