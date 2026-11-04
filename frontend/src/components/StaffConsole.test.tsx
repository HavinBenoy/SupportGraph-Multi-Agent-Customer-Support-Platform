import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StaffConsole } from "./StaffConsole";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";

const row = { id: "ESC-1", ticket_id: "s-1", team: "accounts", priority: "high", reason: "payment_dispute", status: "open", claimed_by: null as string | null, created_at: "2026-10-24T10:00:00Z" };
const detail = { ...row, brief: { student_id: "stu_asha", student_name: "Asha", summary: "Paid twice", excerpt: "call me on [phone]", suggested_next_step: "Check the ledger", sla_hours: 24, batches: [{ batch_id: "PYF-101", attendance_pct: 80 }] }, resolution_note: null, resolved_at: null };
const approval = { request_id: "MK-1", ticket_id: "s-2", student_id: "stu_meera", student_name: "Meera", batch_id: "PYD-301", session_date: "2026-10-20", reason: "was ill", created_at: "2026-10-24T09:00:00Z" };

afterEach(() => vi.unstubAllGlobals());

describe("StaffConsole", () => {
  it("lists escalations, shows the masked brief, then claims and resolves", async () => {
    signedIn("staff", "staff1");
    let cur = { ...detail };
    const f = mockApi({
      "GET /api/staff/escalations": () => ({ json: [row] }),
      "GET /api/staff/escalations/ESC-1": () => ({ json: cur }),
      "POST /api/staff/escalations/ESC-1/claim": () => { cur = { ...cur, status: "claimed", claimed_by: "staff1" }; return { json: cur }; },
      "POST /api/staff/escalations/ESC-1/resolve": (b) => { cur = { ...cur, status: "resolved", resolution_note: b.note }; return { json: cur }; },
    });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(await screen.findByRole("button", { name: /ESC-1/ }));
    const detailPane = await screen.findByRole("region", { name: "Escalation detail" });
    expect(within(detailPane).getByText("call me on [phone]")).toBeInTheDocument();
    expect(within(detailPane).getByText("Check the ledger")).toBeInTheDocument();
    expect(within(detailPane).getByText(/PYF-101 \(80% attendance\)/)).toBeInTheDocument();

    await userEvent.click(within(detailPane).getByRole("button", { name: "Claim" }));
    await waitFor(() => expect(within(detailPane).getByText("claimed")).toBeInTheDocument());
    // the body of a claim request carries nothing: the server takes the identity from the token
    const claim = f.mock.calls.find((c) => String(c[0]).endsWith("/claim"))!;
    expect(claim[1]!.body).toBeUndefined();

    await userEvent.type(within(detailPane).getByLabelText("Resolution note"), "Refunded duplicate");
    await userEvent.click(within(detailPane).getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(within(detailPane).getByText("Refunded duplicate")).toBeInTheDocument());
  });

  it("shows server errors from a conflicting action", async () => {
    signedIn("staff", "staff1");
    mockApi({
      "GET /api/staff/escalations": () => ({ json: [row] }),
      "GET /api/staff/escalations/ESC-1": () => ({ json: detail }),
      "POST /api/staff/escalations/ESC-1/claim": () => ({ status: 409, json: { detail: "escalation already claimed" } }),
    });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(await screen.findByRole("button", { name: /ESC-1/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Claim" }));
    expect(await screen.findByText("escalation already claimed")).toBeInTheDocument();
  });

  it("does not offer resolve on an escalation someone else claimed", async () => {
    signedIn("staff", "staff1");
    mockApi({
      "GET /api/staff/escalations": () => ({ json: [{ ...row, status: "open" }] }),
      "GET /api/staff/escalations/ESC-1": () => ({ json: { ...detail, status: "claimed", claimed_by: "staff2" } }),
    });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(await screen.findByRole("button", { name: /ESC-1/ }));
    expect(await screen.findByText(/only they can resolve it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument();
  });

  it("approves a make-up request and refreshes the queue", async () => {
    signedIn("staff", "staff1");
    let pending = [approval];
    const f = mockApi({
      "GET /api/staff/escalations": () => ({ json: [] }),
      "GET /api/staff/approvals": () => ({ json: pending }),
      "POST /api/staff/approvals/MK-1/decision": () => { pending = []; return { json: { outcome: "approved", message: "Make-up booked." } }; },
    });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(screen.getByRole("tab", { name: /Make-up approvals/ }));
    expect(await screen.findByText("Meera")).toBeInTheDocument();
    expect(screen.getByText("2026-10-20")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/Approved PYD-301 make-up for Meera/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("No requests are waiting.")).toBeInTheDocument());
    const d = f.mock.calls.find((c) => String(c[0]).endsWith("/decision"))!;
    expect(JSON.parse(String(d[1]!.body))).toEqual({ approve: true });  // no approver identity in the body
  });

  it("rejects a request and reports a stale one", async () => {
    signedIn("staff", "staff1");
    mockApi({
      "GET /api/staff/escalations": () => ({ json: [] }),
      "GET /api/staff/approvals": () => ({ json: [approval] }),
      "POST /api/staff/approvals/MK-1/decision": () => ({ status: 409, json: { detail: "this request is no longer waiting for a decision" } }),
    });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(screen.getByRole("tab", { name: /Make-up approvals/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no longer waiting");
  });

  it("signs out when the token is rejected", async () => {
    signedIn("staff", "staff1");
    mockApi({ "GET /api/staff/escalations": () => ({ status: 401, json: { detail: "invalid or expired token" } }) });
    renderWithAuth(<StaffConsole />);
    await waitFor(() => expect(sessionStorage.getItem("sg.session")).toBeNull());
  });
});
