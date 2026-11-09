"use client";
import { useState } from "react";
import { AdminDashboard } from "./AdminDashboard";
import { KnowledgeAdmin } from "./KnowledgeAdmin";
import { TrialAdmin } from "./TrialAdmin";
import { KnowledgeWorkspace } from "./workspace/KnowledgeWorkspace";
import { ChangeRequests } from "./workspace/AdminRequests";

type Tab = "analytics" | "knowledge" | "trial";
type KbView = "workspace" | "requests" | "publishing";

/** Administrator area: the analytics (unchanged), from M5 Trial verification, and the Knowledge area: the folder Workspace, change Requests to review, and
 *  Publishing (the M6 process / publish / archive / audit screen, unchanged). */
export function AdminConsole() {
  const [tab, setTab] = useState<Tab>("analytics");
  const [kb, setKb] = useState<KbView>("workspace");
  return (
    <div>
      <div role="tablist" aria-label="Administration" className="tabs">
        <button role="tab" aria-selected={tab === "analytics"} onClick={() => setTab("analytics")}>Analytics</button>
        <button role="tab" aria-selected={tab === "knowledge"} onClick={() => setTab("knowledge")}>Knowledge</button>
        <button role="tab" aria-selected={tab === "trial"} onClick={() => setTab("trial")}>Trial verification</button>
      </div>
      {tab === "analytics" ? <AdminDashboard /> : tab === "trial" ? <TrialAdmin /> : (
        <div>
          <div role="tablist" aria-label="Knowledge views" className="tabs sub">
            <button role="tab" aria-selected={kb === "workspace"} onClick={() => setKb("workspace")}>Workspace</button>
            <button role="tab" aria-selected={kb === "requests"} onClick={() => setKb("requests")}>Change requests</button>
            <button role="tab" aria-selected={kb === "publishing"} onClick={() => setKb("publishing")}>Publishing</button>
          </div>
          {kb === "workspace" ? <KnowledgeWorkspace scope="admin" /> : kb === "requests" ? <ChangeRequests /> : <KnowledgeAdmin />}
        </div>
      )}
    </div>
  );
}
