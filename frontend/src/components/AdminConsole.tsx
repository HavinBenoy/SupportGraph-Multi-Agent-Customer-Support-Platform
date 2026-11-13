"use client";
import Link from "next/link";
import { useUrlParam } from "@/lib/nav";
import { AdminDashboard } from "./AdminDashboard";
import { TrialAdmin } from "./TrialAdmin";
import { TelephonyAdmin } from "./Telephony";
import { KnowledgeWorkspace } from "./workspace/KnowledgeWorkspace";
import { ChangeRequests } from "./workspace/AdminRequests";

type Tab = "analytics" | "knowledge" | "trial" | "telephony";
type KbView = "workspace" | "requests";

/** Administrator area: analytics, the Knowledge area (the document workspace and the change requests to review, approve and publish) and trial verification.
 *  The former separate "Publishing" tab is gone: approved AI-assisted and manual changes are processed and published from the change request itself, and new
 *  documents, drafts, versions, archiving and call-feedback proposals live inside the workspace ("Drafts & document administration", "New document"). Administrators also keep the full Staff console (/agent).
 *  The tab and view are kept in the URL, so the browser's Back button returns to the previous one. */
export function AdminConsole() {
  const [t, setT] = useUrlParam("tab", "analytics");
  const [k, setK] = useUrlParam("view", "workspace");
  const tab = (["analytics", "knowledge", "trial", "telephony"].includes(t ?? "") ? t : "analytics") as Tab;
  const kb = (k === "requests" ? "requests" : "workspace") as KbView;
  const setTab = (v: Tab) => setT(v);
  return (
    <div>
      <header className="page-head"><div><p className="eyebrow">Administration</p><h1 className="page-title">{tab === "analytics" ? "Analytics" : tab === "trial" ? "Trial verification" : tab === "telephony" ? "Telephone line" : "Knowledge"}</h1></div>
        <Link className="btn small" href="/agent">Open the Staff console →</Link></header>
      <div role="tablist" aria-label="Administration" className="tabs">
        <button role="tab" aria-selected={tab === "analytics"} onClick={() => setTab("analytics")}>Analytics</button>
        <button role="tab" aria-selected={tab === "knowledge"} onClick={() => setTab("knowledge")}>Knowledge</button>
        <button role="tab" aria-selected={tab === "trial"} onClick={() => setTab("trial")}>Trial verification</button>
        <button role="tab" aria-selected={tab === "telephony"} onClick={() => setTab("telephony")}>Telephone line</button>
      </div>
      {tab === "analytics" ? <AdminDashboard /> : tab === "trial" ? <TrialAdmin /> : tab === "telephony" ? <TelephonyAdmin /> : (
        <div>
          <div role="tablist" aria-label="Knowledge views" className="tabs sub">
            <button role="tab" aria-selected={kb === "workspace"} onClick={() => setK("workspace")}>Workspace</button>
            <button role="tab" aria-selected={kb === "requests"} onClick={() => setK("requests")}>Change requests</button>
          </div>
          {kb === "workspace" ? <KnowledgeWorkspace scope="admin" /> : <ChangeRequests />}
        </div>
      )}
    </div>
  );
}
