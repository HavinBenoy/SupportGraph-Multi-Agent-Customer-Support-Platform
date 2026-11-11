import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminDashboard } from "./AdminDashboard";
import { LoginForm } from "./LoginForm";
import { RequireRole } from "./RequireRole";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";

const analytics = {
  tenant_id: "t1", days: 14, generated_at: "2026-10-24T10:00:00Z",
  chat: { turns: 42, by_route: { knowledge: 30, write: 6, escalate: 6 }, signed_in_share: 0.5, abstain_rate: 0.125, qa_blocked: 1, self_service_rate: 0.85,
          latency_ms: { p50: 120, p95: 400 }, per_day: [{ date: "2026-10-23", turns: 10 }, { date: "2026-10-24", turns: 32 }] },
  escalations: { created: 6, open: 3, claimed: 1, resolved: 2, by_team: { accounts: 4, academics: 2 }, by_priority: { high: 4, normal: 2 }, by_reason: { payment_dispute: 4, complaint: 2 },
                 median_hours_to_resolve: 3.5, sla_breaches_open: 1, oldest_open_hours: 30 },
  approvals: { pending: 2, approved: 3, rejected: 1, median_hours_to_decide: 1.2 },
  tickets_by_status: { resolved: 5, escalated: 3 }, notes: ["Counts what the system did."],
  support: { conversations: 20, ai_resolved: 17, escalated: 3, ai_resolution_rate: 0.85, calls: 4, calls_taken_over: 1, takeover_rate: 0.25, calls_failed: 0, escalation_resolution_rate: 0.3333,
    median_reply_ms: 120, median_hours_to_resolve: 3.5, unresolved: 4, qa_blocked: 1, definitions: { conversations: "Distinct conversations (ticket ids).", takeover_rate: "Calls taken over / calls." } },
};
const knowledge = {
  tenant_id: "t1", days: 14, generated_at: "2026-10-24T10:00:00Z", definitions: { funnel: "Requests → … → published.", manual_edit_rate: "edited / (accepted + edited)." },
  knowledge: { requests: 10, by_kind: { ai_assisted: 7, manual: 2, deletion: 1 }, by_status: { published: 4, rejected: 1, awaiting_decisions: 5 }, documents_examined: 30, ai_proposals: 12, documents_affected: 8,
    staff: { accepted: 6, edited: 2, discarded: 4, pending: 0, acceptance_rate: 0.6667, manual_edit_rate: 0.25 }, admin: { submitted: 5, approved: 4, rejected: 1, approval_rate: 0.8, median_hours_to_review: 2 },
    published: 4, published_items: { ai_unmodified: 5, human_modified: 3 }, median_hours_to_publication: null, failed: 0, incomplete: 5,
    funnel: [{ step: "Requested", count: 10 }, { step: "Proposal ready", count: 8 }, { step: "Submitted by staff", count: 5 }, { step: "Approved by admin", count: 4 }, { step: "Published", count: 4 }],
    per_day: [{ date: "2026-10-24", turns: 10 }],
    contributions: [{ staff: "staff1", requests: 10, accepted: 6, edited: 2, submitted: 5, approved: 4, rejected: 1, published: 4, approval_rate: 0.8, median_hours_to_review: 2 }] },
};
afterEach(() => vi.unstubAllGlobals());

describe("AdminDashboard", () => {
  it("renders real numbers from the API, including n/a for missing values", async () => {
    signedIn("admin", "admin1");
    const f = mockApi({ "GET /api/admin/analytics": () => ({ json: analytics }), "GET /api/admin/analytics/knowledge": () => ({ json: knowledge }) });
    renderWithAuth(<AdminDashboard />);
    const ops = await screen.findByRole("region", { name: "Support operations" });
    expect(screen.getByText("Conversations").parentElement).toHaveTextContent("20");
    expect(screen.getByText("Resolved by the AI").parentElement).toHaveTextContent("85%");
    expect(screen.getByText("Voice takeover rate").parentElement).toHaveTextContent("25%");
    expect(screen.getByText("Escalated to staff").parentElement).toHaveTextContent("1 open past SLA");
    expect(screen.getByText("Conversations").parentElement).toHaveAttribute("title", "Distinct conversations (ticket ids).");   // every figure says how it is computed
    expect(screen.getByRole("img", { name: /2026-10-24 32/ })).toBeInTheDocument();
    const gov = await screen.findByRole("region", { name: "Knowledge governance" });
    expect(gov).toHaveTextContent("7 AI-assisted · 2 manual · 1 deletions");
    expect(screen.getByText("Manual-edit rate").parentElement).toHaveTextContent("25%");
    expect(gov).toHaveTextContent("median n/a from request");     // n/a, never a made-up value
    expect(screen.getByText("From request to publication").parentElement).toHaveTextContent(/Submitted by staff\s*5/);
    expect(screen.getByRole("region", { name: "Staff contributions" })).toHaveTextContent("staff1");
    expect(f.mock.calls[0][0]).toContain("days=14");
  });

  it("re-queries when the period changes and shows errors", async () => {
    signedIn("admin", "admin1");
    let fail = false;
    const f = mockApi({ "GET /api/admin/analytics": () => (fail ? { status: 500, json: { detail: "something went wrong (reference ab12)" } } : { json: analytics }),
      "GET /api/admin/analytics/knowledge": () => ({ json: knowledge }) });
    renderWithAuth(<AdminDashboard />);
    await screen.findByText("Conversations");
    fail = true;
    await userEvent.selectOptions(screen.getByLabelText("Period"), "30");
    expect(await screen.findByRole("alert")).toHaveTextContent("reference ab12");
    expect(f.mock.calls.some((c) => String(c[0]).includes("/admin/analytics?days=30"))).toBe(true);
  });
});

describe("RequireRole", () => {
  it("shows a sign-in form to anonymous visitors", async () => {
    mockApi({});
    renderWithAuth(<RequireRole roles={["staff"]}><p>secret console</p></RequireRole>);
    expect(await screen.findByText("Staff sign in")).toBeInTheDocument();
    expect(screen.queryByText("secret console")).not.toBeInTheDocument();
  });
  it("blocks a student from staff pages and allows staff", async () => {
    signedIn("student", "asha");
    const { unmount } = renderWithAuth(<RequireRole roles={["staff", "admin"]}><p>secret console</p></RequireRole>);
    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot open this page/);
    expect(screen.queryByText("secret console")).not.toBeInTheDocument();
    unmount();
    signedIn("staff", "s1");
    renderWithAuth(<RequireRole roles={["staff", "admin"]}><p>secret console</p></RequireRole>);
    expect(await screen.findByText("secret console")).toBeInTheDocument();
  });
  it("staff cannot open the admin-only page", async () => {
    signedIn("staff", "s1");
    renderWithAuth(<RequireRole roles={["admin"]}><p>analytics</p></RequireRole>);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("LoginForm", () => {
  it("signs in and stores the session; shows the server message on failure", async () => {
    let ok = false;
    mockApi({ "POST /api/auth/login": () => (ok ? { json: { token: "T", role: "staff", username: "s1", tenant_id: "t1", student_id: null, expires_in: 600 } } : { status: 401, json: { detail: "wrong username or password" } }) });
    const done = vi.fn();
    renderWithAuth(<LoginForm onDone={done} />);
    await userEvent.type(screen.getByLabelText("Username"), "s1");
    await userEvent.type(screen.getByLabelText("Password"), "badbadbadbad");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong username or password");
    expect(done).not.toHaveBeenCalled();
    ok = true;
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(expect.objectContaining({ role: "staff" })));
    expect(JSON.parse(sessionStorage.getItem("sg.session")!).token).toBe("T");
  });
});
