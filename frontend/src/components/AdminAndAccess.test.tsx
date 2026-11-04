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
};
afterEach(() => vi.unstubAllGlobals());

describe("AdminDashboard", () => {
  it("renders real numbers from the API, including n/a for missing values", async () => {
    signedIn("admin", "admin1");
    const f = mockApi({ "GET /api/admin/analytics": () => ({ json: { ...analytics, chat: { ...analytics.chat, abstain_rate: null } } }) });
    renderWithAuth(<AdminDashboard />);
    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
    expect(screen.getByText("Open escalations").parentElement).toHaveTextContent("4"); // open + claimed
    expect(screen.getByText("Open escalations").parentElement).toHaveTextContent("1 past SLA");
    expect(screen.getByRole("img", { name: /2026-10-24 32/ })).toBeInTheDocument();
    expect(screen.getAllByText("accounts").length).toBeGreaterThan(0);
    expect(f.mock.calls[0][0]).toContain("days=14");
  });

  it("re-queries when the period changes and shows errors", async () => {
    signedIn("admin", "admin1");
    let fail = false;
    const f = mockApi({ "GET /api/admin/analytics": () => (fail ? { status: 500, json: { detail: "something went wrong (reference ab12)" } } : { json: analytics }) });
    renderWithAuth(<AdminDashboard />);
    await screen.findByText("42");
    fail = true;
    await userEvent.selectOptions(screen.getByLabelText("Period"), "30");
    expect(await screen.findByRole("alert")).toHaveTextContent("reference ab12");
    expect(f.mock.calls.at(-1)![0]).toContain("days=30");
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
