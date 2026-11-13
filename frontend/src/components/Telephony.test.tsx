import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LiveCalls, callerWords } from "./LiveCalls";
import { PhonePin, TelephonyAdmin } from "./Telephony";
import { AdminConsole } from "./AdminConsole";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { LiveCall, TelephonyStatus } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, "", "/"); });

const sent = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);
const STATUS: TelephonyStatus = {
  provider: "none", enabled: false, ready: false, problems: ["The telephone line is switched off (SG_TELEPHONY_PROVIDER=none). Enabling it uses a paid provider: see docs/M8.md."],
  stream_path: null, token_configured: false, sample_rate: 16000, default_tenant: "t1", ip_allowlist: false, max_concurrent: 4, active_phone_calls: 0, paid_service: true,
  approval_note: "A public telephone number is a paid service (number rental, per-minute charges). It stays off until you approve it.",
  pin: { length: 6, max_attempts: 5, lock_minutes: 15, attempts_per_call: 3 },
};
const ACCOUNTS = [
  { username: "admin1", role: "admin", disabled: false, phone_masked: null, phone_source: null, has_pin: false, locked_until: null },
  { username: "asha", role: "student", disabled: false, phone_masked: "+91 ******2345", phone_source: "student record", has_pin: true, locked_until: "2099-01-01T00:00:00Z" },
  { username: "staff1", role: "staff", disabled: false, phone_masked: "+91 ******9999", phone_source: "administrator", has_pin: false, locked_until: null },
];
function admin(extra: Record<string, (b: any) => any> = {}) {
  signedIn("admin", "admin1");
  return mockApi({
    "GET /api/admin/telephony/status": () => ({ json: STATUS }),
    "GET /api/admin/telephony/summary": () => ({ json: { days: 30, phone: { total: 3, verified: 1, claimed_unverified: 1, unknown: 1, ambiguous: 0, taken_over: 1, failed: 0 }, web_calls: 4 } }),
    "GET /api/admin/telephony/accounts": () => ({ json: { items: ACCOUNTS } }),
    "GET /api/admin/telephony/audit": () => ({ json: { items: [{ id: 1, at: "2026-11-12T08:00:00Z", actor: "system", action: "pin_locked", subject: "asha" }] } }),
    "GET /api/me/phone-pin": () => ({ json: { has_pin: false, locked_until: null, updated_at: null, length: 6, phone_line: false } }),
    ...extra,
  });
}

describe("M8 administration", () => {
  it("says the line is off and paid, never shows a token, and summarises phone calls by identity status", async () => {
    admin();
    renderWithAuth(<TelephonyAdmin />);
    const st = await screen.findByRole("region", { name: "Line status" });
    expect(await within(st).findByText("Off")).toBeInTheDocument();
    expect(st).toHaveTextContent("paid service");
    expect(st).toHaveTextContent("switched off");
    const calls = await screen.findByRole("region", { name: "Phone calls" });
    expect(await within(calls).findByText("Verified callers")).toBeInTheDocument();
    expect(calls).toHaveTextContent("4 browser voice calls");
    expect(await screen.findByText(/phone verification locked/)).toBeInTheDocument();
  });

  it("sets a staff number, never offers one for a student, and resets a locked PIN only after confirming", async () => {
    const f = admin({
      "PUT /api/admin/telephony/phones/admin1": () => ({ json: { username: "admin1", phone_masked: "+91 ******2345" } }),
      "DELETE /api/admin/telephony/pins/asha": () => ({ json: { reset: true } }),
    });
    renderWithAuth(<TelephonyAdmin />);
    const table = await screen.findByRole("table");
    const asha = within(table).getByRole("row", { name: /asha/ });
    expect(within(asha).queryByRole("button", { name: /number/ })).toBeNull();                       // a student's number is their own record
    expect(asha).toHaveTextContent("locked until");
    await userEvent.click(within(within(table).getByRole("row", { name: /admin1/ })).getByRole("button", { name: "Set number" }));
    await userEvent.type(screen.getByLabelText("Telephone number for admin1"), "+91 98450 12345");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(f, "PUT", "/api/admin/telephony/phones/admin1")).toHaveLength(1));
    expect(JSON.parse(String((sent(f, "PUT", "/api/admin/telephony/phones/admin1")[0][1] as RequestInit).body))).toEqual({ phone: "+91 98450 12345" });
    expect(await screen.findByText("Number saved for admin1.")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("row", { name: /asha/ })).getByRole("button", { name: "Reset PIN" }));
    expect(sent(f, "DELETE", "/api/admin/telephony/pins/asha")).toHaveLength(0);                    // not yet: a confirmation first
    await userEvent.click(within(screen.getByRole("row", { name: /asha/ })).getByRole("button", { name: "Confirm reset" }));
    await waitFor(() => expect(sent(f, "DELETE", "/api/admin/telephony/pins/asha")).toHaveLength(1));
    expect(await screen.findByText(/Phone PIN reset for asha/)).toBeInTheDocument();
  });

  it("is a tab of the administration area", async () => {
    admin({ "GET /api/admin/analytics": () => ({ json: {} }) });
    window.history.replaceState(null, "", "/admin?tab=telephony");
    renderWithAuth(<AdminConsole />);
    expect(screen.getByRole("tab", { name: "Telephone line" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("region", { name: "Line status" })).toBeInTheDocument();
  });
});

describe("M8 own phone PIN", () => {
  it("needs the current password and exactly the configured number of digits, and shows the server's refusal", async () => {
    signedIn("student", "asha");
    const f = mockApi({
      "GET /api/me/phone-pin": () => ({ json: { has_pin: false, locked_until: null, updated_at: null, length: 6, phone_line: true } }),
      "PUT /api/me/phone-pin": (b) => (b.current_password === "right-password" ? { json: { has_pin: true, locked_until: null, updated_at: null } } : { status: 403, json: { detail: "the password is not correct" } }),
    });
    renderWithAuth(<PhonePin />);
    const save = await screen.findByRole("button", { name: "Set PIN" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Current password"), "wrong");
    await userEvent.type(screen.getByLabelText("New 6-digit PIN"), "48a29x13");
    expect(screen.getByLabelText("New 6-digit PIN")).toHaveValue("482913");                         // digits only
    await userEvent.click(save);
    expect(await screen.findByRole("alert")).toHaveTextContent("password is not correct");
    await userEvent.clear(screen.getByLabelText("Current password"));
    await userEvent.type(screen.getByLabelText("Current password"), "right-password");
    await userEvent.type(screen.getByLabelText("New 6-digit PIN"), "482913");
    await userEvent.click(screen.getByRole("button", { name: "Set PIN" }));
    expect(await screen.findByText("Phone PIN saved.")).toBeInTheDocument();
    expect(sent(f, "PUT", "/api/me/phone-pin")).toHaveLength(2);
    expect(screen.getByLabelText("New 6-digit PIN")).toHaveValue("");                               // the PIN is never kept on screen
  });
});

describe("M8 in Live Calls", () => {
  const base: LiveCall = {
    id: "c_p1", short_id: "p1p1p1p1", status: "active_ai", category: "ai_handling", owner: "ai", owner_staff: null, epoch: 1, ticket_ref: "a-1234", caller: "anonymous",
    created_at: "2026-11-12T10:00:00Z", connected_at: null, takeover_at: null, ended_at: null, end_reason: null, duration_s: 4, intervened: false, intervention_by: null, trial_mode: false,
    live: true, listeners: 0, listening_staff: [], mic_open: false, announcing: false, is_mine: false, listening: false, can_takeover: true, can_end: true, can_listen: true,
    channel: "phone", caller_status: "claimed", caller_role: "student", caller_masked: "+91 ******2345",
  };
  it("words a matching number as a claim, not as proof", () => {
    expect(callerWords(base)).toBe("Phone +91 ******2345 · student number matches an account — NOT verified");
    expect(callerWords({ ...base, caller_status: "verified", caller: "student" })).toBe("Phone +91 ******2345 · student verified with phone PIN");
    expect(callerWords({ ...base, caller_status: "unknown", caller_role: null, caller_masked: null })).toBe("Phone (number withheld) · number not registered");
    expect(callerWords({ ...base, channel: "web" })).toBe("Visitor");
  });
  it("marks phone calls in the list and shows the channel and identity in the detail", async () => {
    signedIn("staff", "staff1");
    mockApi({ "GET /api/staff/calls": () => ({ json: [base] }) });
    renderWithAuth(<LiveCalls />);
    const row = await screen.findByRole("button", { name: /Call p1p1p1p1/ });
    expect(row).toHaveTextContent("Phone · not verified");
    await userEvent.click(row);
    expect(await screen.findByText("Telephone")).toBeInTheDocument();
    expect(screen.getByText(/NOT verified · ref a-1234/)).toBeInTheDocument();
  });
});
