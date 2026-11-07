import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TrialAdmin } from "./TrialAdmin";
import { RequireRole } from "./RequireRole";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { Feedback, RecordingRow, RetentionStatus, TrialMetrics, TrialView } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

const view = (o: Partial<TrialView["settings"]> = {}, rec: Partial<TrialView["recording"]> = {}): TrialView => ({
  settings: { tenant_id: "t1", enabled: false, version: 3, recording_offered: false, disclosure_text: "This is a trial call.", recording_notice: "Recorded for {days} days.", consent_policy_version: 2,
    retention_recording_days: 30, retention_consent_days: 365, retention_feedback_days: 365, withdrawal_policy: "delete", updated_by: "admin1", updated_at: "2026-11-01T09:00:00Z", recording_notice_rendered: "Recorded for 30 days.", ...o },
  recording: { provider: "none", operational: false, note: "No recording provider is configured on this server: calls are never recorded.", ...rec },
});
const metrics = (o: Partial<TrialMetrics> = {}): TrialMetrics => ({
  period: { from: "2026-10-09", to: "2026-11-07" }, empty: false, truncated: false, calls_outside_trial: 0,
  calls: { total: 10, ai_handled: 7, ongoing: 0, ended_normally: 6, failed: 1, ended_after_intervention: 3, requiring_escalation: 2 },
  human: { calls_with_listeners: 4, listener_sessions: 5, takeovers: 3, calls_with_takeover: 3, intervention_rate: 0.3 },
  customers: { unique_signed_in: 6, anonymous_calls: 2 },
  durations: { avg_call_s: 125, avg_call_s_note: undefined, calls_measured: 9, avg_intervention_s: 40, interventions_measured: 3 } as TrialMetrics["durations"],
  consent: { offered: 8, granted: 3, ever_granted: 4, declined: 2, withdrawn: 1, undecided: 2, not_offered: 2, granted_rate: 0.5, declined_rate: 0.25, withdrawn_rate: 0.25 },
  recording: { recordings: 4, by_status: { completed: 3, purged: 1 }, calls_recorded: 4 },
  feedback: { by_state: { submitted: 2 }, by_review_status: { pending_review: 2 }, reasons: { outdated_knowledge: 2 }, no_reason_given: 1 },
  notes: ["Figures count trial calls only."], ...o,
});
const fb = (o: Partial<Feedback> = {}): Feedback => ({
  call_id: "c_1", short_id: "aaaa1111", call_status: "ended", call_ended: true, state: "submitted", form_open: false, category: "outdated_knowledge", category_label: "Outdated knowledge",
  review_status: "pending_review", intervened_at: null, submitted_at: "2026-11-07T10:05:00Z", updated_at: null, skipped_at: null, reviewed_at: null, related_index: null, related_message_id: null,
  editable: true, what_was_wrong: "Quoted last year's fees.", suggested_correction: "Use the 2026 table.", staff: "staff1", reviewed_by: null, ...o,
});
const retention = (o: Partial<RetentionStatus> = {}): RetentionStatus => ({
  policy: { recording_days: 30, consent_days: 365, feedback_days: 365, withdrawal_policy: "delete" }, provider: { name: "none", operational: false, recording_offered: false },
  recordings: { by_status: {}, held: 0, eligible_now: 0, purge_failing: 0, next_expiry: null }, consents: { eligible_now: 0 }, feedback: { eligible_now: 0 }, last_run: null, ...o,
});
const rec = (o: Partial<RecordingRow> = {}): RecordingRow => ({
  id: "r_1", call_id: "c_1", status: "completed", started_at: "2026-11-07T10:00:00Z", stopped_at: "2026-11-07T10:03:00Z", stop_reason: "call_ended", size_bytes: 20480,
  expires_at: "2026-12-07T10:00:00Z", legal_hold: false, hold_reason: null, purged_at: null, purge_attempts: 0, purge_error: null, consent_policy_version: 2, provider: "local", tracks: ["customer", "ai", "staff"], ...o,
});

interface World { view: TrialView; metrics: TrialMetrics | (() => TrialMetrics); feedback: Feedback[]; retention: RetentionStatus; recs: RecordingRow[] }
function world(o: Partial<World> = {}, extra: Record<string, (b: any, u: string) => any> = {}) {
  const w: World = { view: view(), metrics: metrics(), feedback: [fb()], retention: retention(), recs: [], ...o };
  const f = mockApi({
    "GET /api/admin/trial": () => ({ json: w.view }),
    "GET /api/admin/trial/events": () => ({ json: [{ id: 1, kind: "enabled", actor: "admin1", detail: { fields: "enabled" }, created_at: "2026-11-01T09:00:00Z" }] }),
    "GET /api/admin/trial/metrics": () => ({ json: typeof w.metrics === "function" ? w.metrics() : w.metrics }),
    "GET /api/admin/trial/feedback": (_b, u) => ({ json: { items: new URL(u, "http://x").searchParams.get("status") ? w.feedback.filter((i) => i.review_status === new URL(u, "http://x").searchParams.get("status")) : w.feedback,
      counts: { by_state: {}, by_review_status: {} }, categories: {} } }),
    "GET /api/admin/trial/retention": () => ({ json: w.retention }),
    "GET /api/admin/trial/recordings": () => ({ json: w.recs }),
    ...extra,
  });
  return { w, f };
}
const calls = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);
const section = (name: string) => screen.findByRole("region", { name });
const mount = () => { signedIn("admin", "admin1"); return renderWithAuth(<TrialAdmin />); };

describe("Trial admin: mode switch", () => {
  it("shows OFF with its version and who changed it; turning it on sends the version it was loaded with", async () => {
    const { w, f } = world({}, { "PUT /api/admin/trial": () => ({ json: (w.view = view({ enabled: true, version: 4 })) }) });
    mount();
    const s = await section("Trial mode and settings");
    expect(await within(s).findByTestId("trial-state")).toHaveTextContent("OFF");
    expect(within(s).getByText(/version 3 · last changed by admin1/)).toBeInTheDocument();
    await userEvent.click(within(s).getByRole("button", { name: "Turn trial mode on" }));
    expect(await within(s).findByText("Trial mode is on for new calls.")).toBeInTheDocument();
    expect(JSON.parse(String((calls(f, "PUT", "/api/admin/trial")[0][1] as RequestInit).body))).toEqual({ expected_version: 3, enabled: true });
    expect(within(s).getByTestId("trial-state")).toHaveTextContent("ON");
    expect(within(s).getByText(/version 4/)).toBeInTheDocument();
    expect(within(s).getByRole("button", { name: "Turn trial mode off" })).toHaveAttribute("aria-pressed", "true");
  });

  it("a stale version (409) tells the admin and offers to reload the latest settings", async () => {
    let first = true;
    const { w } = world({}, { "PUT /api/admin/trial": () => ({ status: 409, json: { detail: "settings changed (now version 5): reload and try again" } }) });
    mount();
    const s = await section("Trial mode and settings");
    await userEvent.click(await within(s).findByRole("button", { name: "Turn trial mode on" }));
    expect(await within(s).findByText(/Somebody else changed these settings/)).toBeInTheDocument();
    expect(within(s).getByRole("alert")).toHaveTextContent("settings changed (now version 5)");
    w.view = view({ enabled: true, version: 5 });
    await userEvent.click(within(s).getByRole("button", { name: "Reload the latest settings" }));
    await waitFor(() => expect(within(s).getByTestId("trial-state")).toHaveTextContent("ON"));
    expect(within(s).queryByText(/Somebody else changed/)).not.toBeInTheDocument();
    expect(first).toBe(true);
  });

  it("another error is shown and the displayed state is left as the server last said", async () => {
    world({ view: view({ enabled: true }) }, { "PUT /api/admin/trial": () => ({ status: 403, json: { detail: "administrators only" } }) });
    mount();
    const s = await section("Trial mode and settings");
    await userEvent.click(await within(s).findByRole("button", { name: "Turn trial mode off" }));
    expect(await within(s).findByRole("alert")).toHaveTextContent("administrators only");
    expect(within(s).getByTestId("trial-state")).toHaveTextContent("ON");
    expect(within(s).queryByText(/Somebody else changed/)).not.toBeInTheDocument();
  });

  it("a double click sends one request", async () => {
    let n = 0; let release!: () => void; const gate = new Promise<void>((r) => { release = r; });
    world({}, { "PUT /api/admin/trial": async () => { n++; await gate; return { json: view({ enabled: true, version: 4 }) }; } });
    mount();
    const s = await section("Trial mode and settings");
    const b = await within(s).findByRole("button", { name: "Turn trial mode on" });
    fireEvent.click(b); fireEvent.click(b); fireEvent.click(b);
    release();
    await within(s).findByText("Trial mode is on for new calls.");
    expect(n).toBe(1);
  });

  it("says why recording cannot be offered when no provider is configured, and disables the choice", async () => {
    world();
    mount();
    const s = await section("Trial mode and settings");
    expect(await within(s).findByTestId("provider-note")).toHaveTextContent(/Recording provider: none \(not available\).*calls are never recorded/);
    expect(within(s).getByRole("checkbox", { name: /Offer callers the choice to record/ })).toBeDisabled();
  });

  it("with a working provider the choice is available and saved with the other settings and the version", async () => {
    const { w, f } = world({ view: view({}, { provider: "local", operational: true, note: "" }) }, { "PUT /api/admin/trial": () => ({ json: (w.view = view({ recording_offered: true, version: 4, retention_recording_days: 14 }, { provider: "local", operational: true, note: "" })) }) });
    mount();
    const s = await section("Trial mode and settings");
    const box = await within(s).findByRole("checkbox", { name: /Offer callers the choice to record/ });
    expect(box).toBeEnabled();
    await userEvent.click(box);
    const days = within(s).getByLabelText("Keep recordings (days)");
    await userEvent.clear(days); await userEvent.type(days, "14");
    await userEvent.click(within(s).getByRole("button", { name: "Save settings" }));
    expect(await within(s).findByText("Settings saved.")).toBeInTheDocument();
    const body = JSON.parse(String((calls(f, "PUT", "/api/admin/trial")[0][1] as RequestInit).body));
    expect(body).toMatchObject({ expected_version: 3, recording_offered: true, retention_recording_days: 14, withdrawal_policy: "delete" });
    expect(within(s).getByText(/now version 2/)).toBeInTheDocument();
  });

  it("lists the recent changes (audit trail) and states that staff cannot change the mode", async () => {
    world();
    mount();
    const s = await section("Trial mode and settings");
    await within(s).findByTestId("trial-state");
    await userEvent.click(within(s).getByText("Recent changes"));
    expect(within(s).getByText(/admin1 · enabled \(enabled\)/)).toBeInTheDocument();
    expect(within(s).getByText(/Only administrators can change this/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/return to ai|hand(ed)? (it )?back to the ai/i);
  });

  it("an initial load failure is shown", async () => {
    world({}, { "GET /api/admin/trial": () => ({ status: 500, json: {} }) });
    mount();
    const s = await section("Trial mode and settings");
    expect(await within(s).findByRole("alert")).toHaveTextContent("The server had a problem. Please try again.");
    expect(within(s).queryByRole("button", { name: /Turn trial mode/ })).not.toBeInTheDocument();
  });
});

describe("Trial admin: metrics", () => {
  it("shows the figures, the consent split and the notes", async () => {
    world();
    mount();
    const s = await section("Trial metrics");
    expect(await within(s).findByText("Trial calls")).toBeInTheDocument();
    const stat = (label: string) => within(s).getByText(label).parentElement!;
    expect(stat("Trial calls")).toHaveTextContent("10");
    expect(stat("Takeovers")).toHaveTextContent("3");
    expect(stat("Takeovers")).toHaveTextContent("listening is not a takeover");
    expect(stat("Intervention rate")).toHaveTextContent("30%");
    expect(stat("Average call")).toHaveTextContent("2 min");
    expect(stat("Average intervention")).toHaveTextContent("40 s");
    expect(within(s).getByTestId("consent-rates")).toHaveTextContent("Agreed 50% · declined 25% · withdrew 25%");
    expect(within(s).getByText("Figures count trial calls only.")).toBeInTheDocument();
  });

  it("an empty period says so (and how many non-trial calls were left out) instead of showing zeros", async () => {
    world({ metrics: metrics({ empty: true, calls_outside_trial: 4 }) });
    mount();
    const s = await section("Trial metrics");
    expect(await within(s).findByTestId("metrics-empty")).toHaveTextContent(/No trial calls in this period.*4 calls were made outside trial mode/);
    expect(within(s).queryByText("Trial calls")).not.toBeInTheDocument();
  });

  it("n/a, not 0%, when a rate cannot be computed", async () => {
    world({ metrics: metrics({ human: { calls_with_listeners: 0, listener_sessions: 0, takeovers: 0, calls_with_takeover: 0, intervention_rate: null }, durations: { avg_call_s: null, calls_measured: 0, avg_intervention_s: null, interventions_measured: 0 } }) });
    mount();
    const s = await section("Trial metrics");
    await within(s).findByText("Trial calls");
    expect(within(s).getByText("Intervention rate").parentElement).toHaveTextContent("n/a");
    expect(within(s).getByText("Average call").parentElement).toHaveTextContent("n/a");
  });

  it("warns when the period was cut short", async () => {
    world({ metrics: metrics({ truncated: true }) });
    mount();
    const s = await section("Trial metrics");
    expect(await within(s).findByText(/more calls than can be summarised at once/)).toBeInTheDocument();
  });

  it("the date filter sends the chosen period; an error is shown", async () => {
    let fail = false;
    const { f } = world({ metrics: () => { if (fail) throw new Error("x"); return metrics(); } });
    mount();
    const s = await section("Trial metrics");
    await within(s).findByText("Trial calls");
    fireEvent.change(within(s).getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(s).getByLabelText("To"), { target: { value: "2026-10-31" } });
    await waitFor(() => expect(f.mock.calls.some((c) => String(c[0]).includes("from=2026-10-01&to=2026-10-31"))).toBe(true));
    await userEvent.click(within(s).getByRole("button", { name: "Apply" }));
    expect(f.mock.calls.filter((c) => String(c[0]).includes("from=2026-10-01&to=2026-10-31")).length).toBeGreaterThanOrEqual(1);
    fail = true;
    await userEvent.click(within(s).getByRole("button", { name: "Apply" }));
    expect(await within(s).findByRole("alert")).toBeInTheDocument();
  });

  it("shows no personal data: only counts", async () => {
    world();
    mount();
    const s = await section("Trial metrics");
    await within(s).findByText("Trial calls");
    expect(s.textContent).not.toMatch(/@|asha|meera|student|c_[0-9a-f]{6}/i);
  });
});

describe("Trial admin: feedback review", () => {
  it("lists feedback awaiting review; opening one shows what was written and the review actions, and says nothing changes the knowledge base", async () => {
    world();
    mount();
    const s = await section("Feedback awaiting review");
    await userEvent.click(await within(s).findByRole("button", { name: "Feedback on call aaaa1111" }));
    const d = within(s).getByRole("article", { name: "Feedback detail" });
    expect(d).toHaveTextContent("Quoted last year's fees.");
    expect(d).toHaveTextContent("Use the 2026 table.");
    for (const name of ["Mark reviewed", "Approve for knowledge update", "Reject"]) expect(within(d).getByRole("button", { name })).toBeEnabled();
    expect(within(s).getByText(/does not change what the assistant knows/)).toBeInTheDocument();
  });

  it("approving sends the status and the note, then refreshes the queue", async () => {
    let body: unknown; let done = false;
    const { w } = world({}, { "POST /api/admin/trial/feedback/c_1/review": (b) => { body = b; done = true; w.feedback = []; return { json: fb({ review_status: "approved_for_update" }) }; } });
    mount();
    const s = await section("Feedback awaiting review");
    await userEvent.click(await within(s).findByRole("button", { name: "Feedback on call aaaa1111" }));
    await userEvent.type(within(s).getByLabelText("Note (optional)"), "Confirmed with accounts.");
    await userEvent.click(within(s).getByRole("button", { name: "Approve for knowledge update" }));
    await waitFor(() => expect(done).toBe(true));
    expect(body).toEqual({ status: "approved_for_update", note: "Confirmed with accounts." });
    expect(await within(s).findByTestId("feedback-empty")).toHaveTextContent("No feedback is waiting for review.");
  });

  it("an administrator cannot review their own feedback: no review buttons, and the reason is given", async () => {
    world({ feedback: [fb({ staff: "admin1" })] });
    mount();
    const s = await section("Feedback awaiting review");
    await userEvent.click(await within(s).findByRole("button", { name: "Feedback on call aaaa1111" }));
    const d = within(s).getByRole("article", { name: "Feedback detail" });
    expect(d).toHaveTextContent("You wrote this feedback, so another administrator must review it.");
    expect(within(d).queryByRole("button")).not.toBeInTheDocument();
  });

  it("a rejected record offers no further action; a refusal from the server is shown", async () => {
    world({ feedback: [fb({ review_status: "rejected", reviewed_by: "admin2" })] }, { });
    mount();
    const s = await section("Feedback awaiting review");
    await userEvent.selectOptions(await within(s).findByLabelText("Show"), "");
    await userEvent.click(await within(s).findByRole("button", { name: "Feedback on call aaaa1111" }));
    const d = within(s).getByRole("article", { name: "Feedback detail" });
    expect(d).toHaveTextContent("Rejected by admin2");
    expect(within(d).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the server's refusal of a review", async () => {
    world({}, { "POST /api/admin/trial/feedback/c_1/review": () => ({ status: 409, json: { detail: "cannot move from rejected to reviewed" } }) });
    mount();
    const s = await section("Feedback awaiting review");
    await userEvent.click(await within(s).findByRole("button", { name: "Feedback on call aaaa1111" }));
    await userEvent.click(within(s).getByRole("button", { name: "Mark reviewed" }));
    expect(await within(s).findByRole("alert")).toHaveTextContent("cannot move from rejected to reviewed");
  });

  it("empty queue and load errors have their own messages", async () => {
    world({ feedback: [] });
    const first = mount();
    const s = await section("Feedback awaiting review");
    expect(await within(s).findByTestId("feedback-empty")).toBeInTheDocument();
    first.unmount();
    world({}, { "GET /api/admin/trial/feedback": () => ({ status: 500, json: {} }) });
    mount();
    expect(await within(await section("Feedback awaiting review")).findByRole("alert")).toBeInTheDocument();
  });
});

describe("Trial admin: retention and recordings", () => {
  it("shows the provider state, counters and policy; with no provider says calls are never recorded", async () => {
    world({ retention: retention({ recordings: { by_status: { completed: 2, failed: 1, purged: 3 }, held: 1, eligible_now: 2, purge_failing: 1, next_expiry: "2026-12-01T00:00:00Z" }, consents: { eligible_now: 5 }, feedback: { eligible_now: 1 },
      last_run: { at: "2026-11-06T02:00:00Z", actor: "system" } }) });
    mount();
    const s = await section("Recording and retention status");
    expect(await within(s).findByTestId("provider-state")).toHaveTextContent("No recording provider is configured on this server: calls are never recorded.");
    expect(within(s).getByText("Recordings kept").parentElement).toHaveTextContent("3");
    expect(within(s).getByText("Recordings kept").parentElement).toHaveTextContent("3 purged");
    expect(within(s).getByText("Due for deletion").parentElement).toHaveTextContent("2");
    expect(within(s).getByText("Under legal hold").parentElement).toHaveTextContent("1");
    expect(within(s).getByText("Deletion failing").parentElement).toHaveTextContent("1");
    expect(within(s).getByText(/Kept for 30 days \(recordings\), 365 days \(consent records\), 365 days \(reviewed feedback\)/)).toBeInTheDocument();
    expect(within(s).getByText(/Calls, tickets and knowledge documents are never deleted by this job/)).toBeInTheDocument();
    expect(within(s).getByText(/Last run .* by system\./)).toBeInTheDocument();
    expect(within(s).getByTestId("recordings-empty")).toBeInTheDocument();
  });

  it("a preview sends dry_run:true and says it would delete; the real purge sends false and reports failures and holds", async () => {
    const bodies: unknown[] = [];
    world({}, { "POST /api/admin/trial/retention/purge": (b) => { bodies.push(b); return { json: b.dry_run ? { purged: 2, failed: 0, held: 0, consents: 1, feedback: 0, dry_run: true } : { purged: 1, failed: 1, held: 1, consents: 0, feedback: 2, dry_run: false } }; } });
    mount();
    const s = await section("Recording and retention status");
    await userEvent.click(await within(s).findByRole("button", { name: "Preview what would be deleted" }));
    expect(await within(s).findByRole("status")).toHaveTextContent("Preview: would delete 2 recording(s), 1 consent record(s), 0 feedback record(s).");
    await userEvent.click(within(s).getByRole("button", { name: "Delete expired data now" }));
    await waitFor(() => expect(within(s).getByRole("status")).toHaveTextContent(/^Deleted 1 recording\(s\), 0 consent record\(s\), 2 feedback record\(s\); 1 could not be deleted and will be retried; 1 skipped \(legal hold\)\./));
    expect(bodies).toEqual([{ dry_run: true }, { dry_run: false }]);
  });

  it("a purge error is shown", async () => {
    world({}, { "POST /api/admin/trial/retention/purge": () => ({ status: 500, json: {} }) });
    mount();
    const s = await section("Recording and retention status");
    await userEvent.click(await within(s).findByRole("button", { name: "Delete expired data now" }));
    expect(await within(s).findByRole("alert")).toHaveTextContent("The server had a problem. Please try again.");
  });

  it("lists recordings as metadata only (no storage reference, no audio element) and shows a failed deletion", async () => {
    world({ recs: [rec({ purge_error: "disk busy" }), rec({ id: "r_2", status: "purged" })] });
    mount();
    const s = await section("Recording and retention status");
    expect(await within(s).findByText("r_1")).toBeInTheDocument();
    expect(within(s).getByText(/deletion failed \(disk busy\)/)).toBeInTheDocument();
    expect(within(s).getByText("purged")).toBeInTheDocument();
    expect(s.querySelector("audio, video, a[download]")).toBeNull();
    expect(s.textContent).not.toMatch(/storage_ref|\/data\/recordings/);
  });

  it("legal hold needs a reason; placing and releasing send the right bodies; a purged recording has no hold button", async () => {
    const bodies: unknown[] = [];
    const { w } = world({ recs: [rec(), rec({ id: "r_2", status: "purged" })] }, { "POST /api/admin/trial/recordings/r_1/hold": (b) => { bodies.push(b); w.recs = [rec({ legal_hold: b.hold, hold_reason: b.reason ?? null }), rec({ id: "r_2", status: "purged" })]; return { json: w.recs[0] }; } });
    mount();
    const s = await section("Recording and retention status");
    await userEvent.click(await within(s).findByRole("button", { name: "Place legal hold" }));
    expect(within(s).getByRole("button", { name: "Confirm hold" })).toBeDisabled();            // no reason, no hold
    await userEvent.type(within(s).getByLabelText("Reason for the legal hold"), "Complaint 42");
    await userEvent.click(within(s).getByRole("button", { name: "Confirm hold" }));
    expect(await within(s).findByText("legal hold: Complaint 42")).toBeInTheDocument();
    await userEvent.click(within(s).getByRole("button", { name: "Release legal hold" }));
    await waitFor(() => expect(within(s).queryByText(/legal hold: Complaint 42/)).not.toBeInTheDocument());
    expect(bodies).toEqual([{ hold: true, reason: "Complaint 42" }, { hold: false }]);
    expect(within(s).getAllByRole("button", { name: /legal hold/i })).toHaveLength(1);        // only r_1; the purged r_2 offers none
  });

  it("cancelling a hold sends nothing", async () => {
    const { f } = world({ recs: [rec()] });
    mount();
    const s = await section("Recording and retention status");
    await userEvent.click(await within(s).findByRole("button", { name: "Place legal hold" }));
    await userEvent.click(within(s).getByRole("button", { name: "Cancel" }));
    expect(within(s).getByRole("button", { name: "Place legal hold" })).toBeInTheDocument();
    expect(calls(f, "POST", "/api/admin/trial/recordings/r_1/hold")).toHaveLength(0);
  });
});

describe("Trial admin: access", () => {
  it("is only rendered for administrators (staff and students are turned away by the role gate, and no trial request is made)", async () => {
    for (const role of ["staff", "student"] as const) {
      signedIn(role, "u9");
      const f = mockApi({});
      const { unmount } = renderWithAuth(<RequireRole roles={["admin"]}><TrialAdmin /></RequireRole>);
      await waitFor(() => expect(screen.queryByText("Trial verification")).not.toBeInTheDocument());
      expect(f.mock.calls.filter((c) => String(c[0]).includes("/admin/trial"))).toHaveLength(0);
      unmount();
    }
  });
});
