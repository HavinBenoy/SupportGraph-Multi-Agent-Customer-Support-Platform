import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LiveCalls, type LiveCallsDeps } from "./LiveCalls";
import { StaffConsole } from "./StaffConsole";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { LiveClient, LiveHandlers } from "@/lib/live";
import type { LiveCall } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

/** What the SERVER derives from a call's state (the same rules as backend/app/api/routes/calls.py): the buttons follow these, never the other way round. */
const derive = (c: LiveCall): LiveCall => {
  const intervened = c.intervened || c.owner === "staff" || c.takeover_at !== null;
  const category = c.status === "ended" || c.status === "failed" ? (intervened ? "ended_after_intervention" : c.status === "failed" ? "failed" : "ended_normally") : c.owner === "staff" ? "human_handling" : "ai_handling";
  return { ...c, intervened, category, can_takeover: c.live && c.status === "active_ai" && c.owner === "ai", can_end: c.live && (c.owner !== "staff" || c.is_mine), can_listen: c.live };
};
const call = (o: Partial<LiveCall> = {}): LiveCall => derive({
  id: "c_aaaa1111bbbb2222", short_id: "aaaa1111", status: "active_ai", category: "ai_handling", owner: "ai", owner_staff: null, epoch: 1, ticket_ref: "s-1234abcd", caller: "student",
  created_at: "2026-11-07T10:00:00Z", connected_at: "2026-11-07T10:00:01Z", takeover_at: null, ended_at: null, end_reason: null,
  duration_s: 125, intervened: false, intervention_by: null, trial_mode: false, live: true, listeners: 0, listening_staff: [], mic_open: false, announcing: false, is_mine: false, listening: false,
  can_takeover: false, can_end: false, can_listen: false, ...o,
});
const ID = "c_aaaa1111bbbb2222";

/** A fake audio client that records what the component asks of it, and lets the test play the server's part. */
function fake() {
  let h!: LiveHandlers;
  const log: string[] = [];
  const c = {
    state: "idle", speaking: false, epoch: 0, announcing: false,
    connect: vi.fn((url: string, token: string) => { log.push(`connect ${url} ${token}`); }),
    speak: vi.fn(async () => { log.push("speak"); }),
    stopSpeaking: vi.fn(() => { log.push("stopSpeaking"); }),
    mute: vi.fn(), disconnect: vi.fn(() => { log.push("disconnect"); }),
  };
  const deps: LiveCallsDeps = { createClient: (handlers) => { h = handlers; return c as unknown as LiveClient; }, wsUrl: (id) => `ws://test/${id}/stream` };
  return { deps, c, log, h: () => h, state: (s: "connecting" | "joined" | "closed") => act(() => h.onState!(s)), msg: (m: Parameters<NonNullable<LiveHandlers["onMsg"]>>[0]) => act(() => h.onMsg!(m)) };
}

function backend(initial: LiveCall[], extra: Record<string, (b: any) => any> = {}) {
  const state = { calls: initial };
  const set = (c: Partial<LiveCall>) => { state.calls = state.calls.map((x) => (x.id === ID ? derive({ ...x, ...c }) : x)); return { json: state.calls.find((x) => x.id === ID) }; };
  const f = mockApi({
    "GET /api/staff/calls": () => ({ json: state.calls }),
    [`POST /api/staff/calls/${ID}/listen`]: () => ({ json: { ...state.calls[0], ws_path: "x" } }),
    [`POST /api/staff/calls/${ID}/takeover`]: () => set({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true, epoch: 2 }),
    [`GET /api/staff/calls/${ID}/transcript`]: () => ({ json: TRANSCRIPT }),
    [`POST /api/staff/calls/${ID}/end`]: () => set({ status: "ended", live: false, owner: "none", owner_staff: null, is_mine: false, ended_at: "2026-11-07T10:05:00Z", end_reason: "ended_by_staff" }),
    ...extra,
  });
  return { state, f, set };
}
const TRANSCRIPT = {
  call_id: ID, complete: true, notes: ["What the staff member said is not transcribed: the transcript shows when they spoke, not their words."],
  entries: [
    { speaker: "customer", text: "Can I pay in instalments?", at: "2026-11-07T10:00:05Z", index: 0 },
    { speaker: "ai", text: "Fees are due in full at enrolment.", at: "2026-11-07T10:00:07Z", index: 1 },
    { speaker: "system", code: "takeover", text: "staff1 took over the call. The assistant is silent from here on.", at: "2026-11-07T10:00:20Z" },
    { speaker: "system", code: "announcement", text: "I want to make sure you receive accurate information. One of our support specialists is joining us to clarify this. I'll hand over the conversation now.", at: "2026-11-07T10:00:21Z" },
    { speaker: "staff", kind: "speech", text: null, at: "2026-11-07T10:00:30Z", until: "2026-11-07T10:00:40Z", duration_s: 10, staff: "staff1", note: "Spoke to the caller (staff audio is not transcribed)" },
    { speaker: "system", code: "ended", text: "The call ended.", at: "2026-11-07T10:01:00Z" },
  ],
};
const posts = (f: ReturnType<typeof mockApi>) => f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "POST").map((c) => String(c[0]).replace("/api/staff/calls/", ""));
const select = async () => { await userEvent.click(await screen.findByRole("button", { name: /Call aaaa1111/ })); return screen.getByRole("region", { name: "Call detail" }); };
const btn = (pane: HTMLElement, name: string | RegExp) => within(pane).getByRole("button", { name });
const enabled = (pane: HTMLElement) => ["Listen", "Stop listening", "Take over", "End call"].filter((n) => !(btn(pane, n) as HTMLButtonElement).disabled);

describe("Live calls: the list", () => {
  it("shows short id, status, owner, caller reference, start time, duration and who is listening", async () => {
    signedIn("staff", "staff1");
    backend([call({ listeners: 2, listening_staff: ["staff2", "admin1"] }), call({ id: "c_zzzz", short_id: "zzzz9999", status: "staff_takeover", owner: "staff", owner_staff: "staff2", caller: "anonymous", ticket_ref: "a-77aa", duration_s: 5 })]);
    renderWithAuth(<LiveCalls />);
    const a = await screen.findByRole("button", { name: /Call aaaa1111, AI handling/ });
    expect(within(a).getByText("2 listening")).toBeInTheDocument();
    expect(a.textContent).toMatch(/Assistant · student s-1234abcd · started .* · 2m 0\ds/);
    const b = screen.getByRole("button", { name: /Call zzzz9999, Human handling/ });
    expect(b.textContent).toMatch(/Staff: staff2 · visitor a-77aa/);
  });

  it("shows loading, then an empty message; and keeps the list when a refresh fails, saying updates are paused", async () => {
    signedIn("staff", "staff1");
    let fail = false;
    mockApi({ "GET /api/staff/calls": () => (fail ? { status: 500, json: {} } : { json: [call()] }) });
    renderWithAuth(<LiveCalls />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await screen.findByRole("button", { name: /Call aaaa1111/ });
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Live updates paused: The server had a problem/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Call aaaa1111/ })).toBeInTheDocument();
  });

  it("says so when there are no calls", async () => {
    signedIn("staff", "staff1");
    mockApi({ "GET /api/staff/calls": () => ({ json: [] }) });
    renderWithAuth(<LiveCalls />);
    expect(await screen.findByText("No calls right now.")).toBeInTheDocument();
  });

  it("refreshes by itself every few seconds and shows another staff member's takeover without a click", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderWithAuth(<LiveCalls />);
    await screen.findByRole("button", { name: /AI handling/ });
    b.state.calls = [call({ status: "staff_takeover", owner: "staff", owner_staff: "staff2", epoch: 2 })];
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(await screen.findByRole("button", { name: /Human handling/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Human handling/ }).textContent).toMatch(/Staff: staff2/);
  });
});

describe("Live calls: which controls the server's state allows", () => {
  const cases: [string, Partial<LiveCall>, string, string[]][] = [
    ["AI owns a live call", {}, "staff1", ["Listen", "Take over", "End call"]],
    ["I hold the call", { status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true }, "staff1", ["Listen", "End call"]],
    ["another staff member holds it (I am staff): I can only listen", { status: "staff_takeover", owner: "staff", owner_staff: "staff2" }, "staff1", ["Listen"]],
    ["another staff member holds it (I am admin): an admin has no extra control", { status: "staff_takeover", owner: "staff", owner_staff: "staff2" }, "admin1", ["Listen"]],
    ["an M4 row left in returning_to_ai can only be listened to and ended", { status: "returning_to_ai", owner: "none" }, "staff1", ["Listen", "End call"]],
    ["the call has ended", { status: "ended", live: false, owner: "none", end_reason: "caller_disconnected", ended_at: "2026-11-07T10:03:00Z" }, "staff1", []],
    ["the call failed", { status: "failed", live: false, owner: "none", end_reason: "error", ended_at: "2026-11-07T10:03:00Z" }, "staff1", []],
  ];
  it.each(cases)("%s", async (_n, over, who, expected) => {
    signedIn(who === "admin1" ? "admin" : "staff", who);
    backend([call(over)]);
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    expect(enabled(pane)).toEqual(expected);
  });

  it("nothing is offered until a call is selected, and an ended call says how it ended", async () => {
    signedIn("staff", "staff1");
    backend([call({ status: "ended", live: false, owner: "none", end_reason: "caller_disconnected", ended_at: "2026-11-07T10:03:00Z" })]);
    renderWithAuth(<LiveCalls />);
    expect(await screen.findByText("Select a call to listen, take over or end it.")).toBeInTheDocument();
    const pane = await select();
    expect(within(pane).getByText(/caller hung up/)).toBeInTheDocument();
  });
});

describe("Live calls: listening", () => {
  it("validates with the server, then opens the audio connection with the token; shows the transcript; Stop listening releases it", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(posts(b.f)).toEqual([`${ID}/listen`]));
    expect(fk.log).toEqual([`connect ws://test/${ID}/stream tok-staff1`]);
    expect(within(pane).getByText("Connecting audio…")).toBeInTheDocument();
    await fk.state("joined");
    expect(enabled(pane)).toEqual(["Stop listening", "Take over", "End call"]);
    expect(within(pane).getAllByText("You are listening").length).toBeGreaterThan(0);
    await fk.msg({ type: "transcript", speaker: "caller", text: "When is my class?" });
    await fk.msg({ type: "transcript", speaker: "ai", text: "Your class is at ten." });
    const log = within(pane).getByRole("log", { name: "Call transcript" });
    expect(within(log).getByText("When is my class?")).toBeInTheDocument();
    expect(within(log).getByText("Your class is at ten.")).toBeInTheDocument();
    await userEvent.click(btn(pane, "Stop listening"));
    await waitFor(() => expect(fk.c.disconnect).toHaveBeenCalled());
    expect(enabled(pane)).toEqual(["Listen", "Take over", "End call"]);
  });

  it("a server refusal (the call has ended) is shown and no audio connection is opened", async () => {
    signedIn("staff", "staff1");
    const fk = fake();
    backend([call()], { [`POST /api/staff/calls/${ID}/listen`]: () => ({ status: 409, json: { detail: "this call has ended" } }) });
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    expect(await within(pane).findByText("this call has ended")).toBeInTheDocument();
    expect(fk.c.connect).not.toHaveBeenCalled();
  });

  it("an audio socket that drops clears the connection and the control to listen is available again", async () => {
    signedIn("staff", "staff1");
    backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    await fk.state("closed");
    expect(enabled(pane)).toEqual(["Listen", "Take over", "End call"]);
    expect(within(pane).queryByRole("log", { name: "Call transcript" })).not.toBeInTheDocument();
  });

  it("an error from the audio client (for example the token expired) is shown", async () => {
    signedIn("staff", "staff1");
    backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    act(() => fk.h().onError!("session expired"));
    expect(await within(pane).findByText("session expired")).toBeInTheDocument();
  });
});

describe("Live calls: take over and end (a taken-over call never goes back to the assistant)", () => {
  it("take over: asks the server, connects and opens the microphone after the audio connection is joined, then reflects the new owner", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Take over"));
    await waitFor(() => expect(posts(b.f)).toEqual([`${ID}/takeover`]));
    expect(await within(pane).findByText(/You have taken over call aaaa1111/)).toBeInTheDocument();
    expect(fk.c.speak).not.toHaveBeenCalled();                      // not before the audio connection is joined
    await fk.state("joined");
    expect(fk.c.speak).toHaveBeenCalledTimes(1);
    expect(within(pane).getByText("Staff: staff1 (you)", { selector: "dd" })).toBeInTheDocument();
    expect(enabled(pane)).toEqual(["Stop listening", "End call"]);
    expect(within(pane).getByText(/Your microphone is off/)).toBeInTheDocument();   // until the client reports the mic is open
    act(() => fk.h().onMic!(true));
    expect(await within(pane).findByText(/The caller can hear you/)).toBeInTheDocument();
    await userEvent.click(btn(pane, "Mute microphone"));
    expect(fk.c.mute).toHaveBeenCalledWith(true);
    expect(await within(pane).findByText(/Your microphone is muted/)).toBeInTheDocument();
  });

  it("when already listening, take over opens the microphone straight away on the same connection", async () => {
    signedIn("staff", "staff1");
    backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    await userEvent.click(btn(pane, "Take over"));
    await waitFor(() => expect(fk.c.speak).toHaveBeenCalledTimes(1));
    expect(fk.c.connect).toHaveBeenCalledTimes(1);                   // no second connection
  });

  it("a takeover that loses the race shows the server's message, refreshes, and offers no takeover", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()], { [`POST /api/staff/calls/${ID}/takeover`]: () => { b.state.calls = [call({ status: "staff_takeover", owner: "staff", owner_staff: "staff2", epoch: 2 })]; return { status: 409, json: { detail: "another staff member has already taken over this call" } }; } });
    const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Take over"));
    expect(await within(pane).findByText("another staff member has already taken over this call")).toBeInTheDocument();
    await waitFor(() => expect(enabled(pane)).toEqual(["Listen"]));
    expect(within(pane).getByText(/staff2 has taken over this call/)).toBeInTheDocument();
    expect(fk.c.connect).not.toHaveBeenCalled();
  });

  it("a double click sends ONE request and every control is disabled while it is in flight", async () => {
    signedIn("staff", "staff1");
    let release!: () => void; const gate = new Promise<void>((r) => { release = r; });
    const b = backend([call()], { [`POST /api/staff/calls/${ID}/takeover`]: async () => { await gate; return b.set({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true, epoch: 2 }); } });
    const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    const take = btn(pane, "Take over");
    fireEvent.click(take); fireEvent.click(take); fireEvent.click(take);
    await waitFor(() => expect(enabled(pane)).toEqual([]));          // all disabled while busy
    release();
    await waitFor(() => expect(posts(b.f)).toEqual([`${ID}/takeover`]));
    await within(pane).findByText(/You have taken over/);
  });

  it("ending a call asks for confirmation first; Cancel does nothing; confirming ends it and closes the audio connection", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    await userEvent.click(btn(pane, "End call"));
    expect(posts(b.f)).toEqual([`${ID}/listen`]);
    await userEvent.click(btn(pane, "Cancel"));
    expect(posts(b.f)).toEqual([`${ID}/listen`]);
    await userEvent.click(btn(pane, "End call"));
    await userEvent.click(btn(pane, "Confirm end call"));
    expect(await within(pane).findByText(/Call aaaa1111 was ended/)).toBeInTheDocument();
    expect(posts(b.f)).toEqual([`${ID}/listen`, `${ID}/end`]);
    expect(fk.c.disconnect).toHaveBeenCalled();
    expect(enabled(pane)).toEqual([]);
    expect(within(pane).getByText(/ended by staff/)).toBeInTheDocument();
  });

  it("when the server says the call ended (audio socket message), the screen says so and refreshes", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    b.set({ status: "ended", live: false, owner: "none", ended_at: "2026-11-07T10:04:00Z", end_reason: "caller_disconnected" });
    await fk.msg({ type: "call_ended", reason: "caller_disconnected" });
    expect(await within(pane).findByText("This call has ended.")).toBeInTheDocument();
    await waitFor(() => expect(enabled(pane)).toEqual([]));
  });

  it("an ownership message on the audio socket refreshes the list at once", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    const before = b.f.mock.calls.filter((c) => String(c[0]).endsWith("/staff/calls")).length;
    b.set({ status: "staff_takeover", owner: "staff", owner_staff: "staff2", epoch: 2 });
    await fk.msg({ type: "ownership", owner: "staff", epoch: 2, staff: "staff2" });
    await waitFor(() => expect(b.f.mock.calls.filter((c) => String(c[0]).endsWith("/staff/calls")).length).toBeGreaterThan(before));
    expect(await within(pane).findByText(/staff2 has taken over this call/)).toBeInTheDocument();
  });
});

describe("Live calls: cleanup and integration", () => {
  it("leaving the page releases the audio connection (socket, microphone, audio context)", async () => {
    signedIn("staff", "staff1");
    backend([call()]); const fk = fake();
    const { unmount } = renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    unmount();
    expect(fk.c.disconnect).toHaveBeenCalled();
  });

  it("stops polling after unmount", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { unmount } = renderWithAuth(<LiveCalls />);
    await screen.findByRole("button", { name: /Call aaaa1111/ });
    unmount();
    const n = b.f.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(b.f.mock.calls.length).toBe(n);
  });

  it("is a tab of the staff console", async () => {
    signedIn("staff", "staff1");
    mockApi({ "GET /api/staff/escalations": () => ({ json: [] }), "GET /api/staff/calls": () => ({ json: [call()] }) });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(await screen.findByRole("tab", { name: "Live calls" }));
    expect(await screen.findByRole("button", { name: /Call aaaa1111/ })).toBeInTheDocument();
  });

  it("an admin sees the student id of a call; a staff member does not get it from the API", async () => {
    signedIn("admin", "admin1");
    backend([call({ student_id: "stu_asha" })]);
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    expect(within(pane).getByText(/stu_asha/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------------------------------------------
// M4.1
// ---------------------------------------------------------------------------------------------------------------------------------
const ENDED_AFTER: Partial<LiveCall> = { status: "ended", live: false, owner: "none", takeover_at: "2026-11-07T10:00:20Z", intervention_by: "staff1", end_reason: "ended_by_staff", ended_at: "2026-11-07T10:01:00Z" };
const FB_AWAITING = { call_id: ID, short_id: "aaaa1111", call_status: "ended", call_ended: true, state: "awaiting", form_open: true, category: null, category_label: null, review_status: null,
  intervened_at: "2026-11-07T10:00:20Z", submitted_at: null, updated_at: null, skipped_at: null, reviewed_at: null, related_index: null, related_message_id: null, editable: false,
  categories: { incorrect_information: "Incorrect factual information", outdated_knowledge: "Outdated knowledge", misleading_response: "Misleading response", policy_compliance: "Policy or compliance issue",
    customer_dissatisfaction: "Customer dissatisfaction", missing_knowledge: "Missing knowledge", other: "Other" } };

describe("Live calls (M4.1): Return to AI is gone", () => {
  const states: Partial<LiveCall>[] = [
    {}, { status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true }, { status: "staff_takeover", owner: "staff", owner_staff: "staff2" }, { status: "returning_to_ai", owner: "none" }, ENDED_AFTER,
  ];
  it.each(states.map((s, i) => [i, s] as const))("no control, label or text offers it (state %i)", async (_i, over) => {
    signedIn("admin", "admin1");
    backend([call(over)]);
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    expect(within(pane).queryByRole("button", { name: /return/i })).not.toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(/return to ai|back to the assistant|hand(ed)? (it )?back/i);
  });

  it("the page never calls a return endpoint, whichever buttons are used", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Take over"));
    await within(pane).findByText(/You have taken over/);
    await userEvent.click(btn(pane, "End call")); await userEvent.click(btn(pane, "Confirm end call"));
    await within(pane).findByText(/was ended/);
    expect(b.f.mock.calls.map((c) => String(c[0])).filter((u) => /return/i.test(u))).toEqual([]);
  });
});

describe("Live calls (M4.1): status colours with words", () => {
  const rows: [string, Partial<LiveCall>, string][] = [
    ["ai_handling", {}, "AI handling"],
    ["human_handling", { status: "staff_takeover", owner: "staff", owner_staff: "staff2" }, "Human handling"],
    ["ended_normally", { status: "ended", live: false, owner: "none", end_reason: "caller_disconnected", ended_at: "2026-11-07T10:03:00Z" }, "Ended normally"],
    ["ended_after_intervention", ENDED_AFTER, "Ended after intervention"],
  ];
  it.each(rows)("%s is shown with its label in the list and the detail", async (category, over, label) => {
    signedIn("staff", "staff1");
    backend([call(over)]);
    renderWithAuth(<LiveCalls />);
    const item = await screen.findByRole("button", { name: new RegExp(`Call aaaa1111, ${label}`) });
    const badge = within(item).getByText(label);
    expect(badge).toHaveAttribute("data-category", category);
    expect(badge.className).toContain(`status-${category}`);
    const pane = await select();
    expect(within(pane).getByText(label).className).toContain(`status-${category}`);
  });

  it("the four categories use four different classes (so four different colours)", () => {
    expect(new Set(rows.map(([c]) => `status-${c}`)).size).toBe(4);
  });

  it("listening is not taking over: a call staff listen to is still AI handling", async () => {
    signedIn("staff", "staff1");
    backend([call({ listeners: 2, listening_staff: ["staff2", "admin1"], listening: false })]);
    renderWithAuth(<LiveCalls />);
    const item = await screen.findByRole("button", { name: /Call aaaa1111/ });
    expect(within(item).getByText("AI handling")).toBeInTheDocument();
    expect(within(item).getByText("2 listening")).toBeInTheDocument();
    expect(within(item).queryByText("Human handling")).not.toBeInTheDocument();
  });

  it("a call that failed without a human is its own state, not mistaken for an intervention", async () => {
    signedIn("staff", "staff1");
    backend([call({ status: "failed", live: false, owner: "none", end_reason: "error", ended_at: "2026-11-07T10:03:00Z" })]);
    renderWithAuth(<LiveCalls />);
    expect(await screen.findByRole("button", { name: /Call aaaa1111, Failed/ })).toBeInTheDocument();
  });
});

describe("Live calls (M4.1): the complete transcript", () => {
  it("shows customer, AI, human staff and system entries in order, with times, and never invents staff words", async () => {
    signedIn("staff", "staff1");
    backend([call(ENDED_AFTER)]);
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    const t = await within(pane).findByRole("region", { name: "Full call transcript" });
    const items = await within(t).findAllByRole("listitem");
    expect(items.map((i) => i.className.match(/t-(\w+)/)![1])).toEqual(["customer", "ai", "system", "system", "staff", "system"]);
    expect(items[0]).toHaveTextContent("Customer"); expect(items[0]).toHaveTextContent("Can I pay in instalments?");
    expect(items[1]).toHaveTextContent("AI assistant"); expect(items[1]).toHaveTextContent("Fees are due in full at enrolment.");
    expect(items[2]).toHaveTextContent("System"); expect(items[2]).toHaveTextContent("staff1 took over the call");
    expect(items[3]).toHaveTextContent("I want to make sure you receive accurate information. One of our support specialists is joining us to clarify this. I'll hand over the conversation now.");
    expect(items[4]).toHaveTextContent("Human staff (staff1)"); expect(items[4]).toHaveTextContent("Spoke to the caller (staff audio is not transcribed) · 10s");
    expect(items.every((i) => i.querySelector("time")!.textContent !== "")).toBe(true);
    expect(within(t).getByText(/not transcribed: the transcript shows when they spoke, not their words/)).toBeInTheDocument();
    expect(items[3]).not.toHaveTextContent("AI assistant");                       // the announcement is a system line, never an AI line
  });

  it("refresh reloads it; an error is shown without hiding the call; an empty call says so", async () => {
    signedIn("staff", "staff1");
    let mode: "ok" | "fail" | "empty" = "ok"; let n = 0;
    backend([call()], { [`GET /api/staff/calls/${ID}/transcript`]: () => { n++; return mode === "fail" ? { status: 500, json: {} } : { json: mode === "empty" ? { call_id: ID, entries: [], notes: [], complete: true } : TRANSCRIPT }; } });
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    await within(pane).findByText("Can I pay in instalments?");
    mode = "fail";
    await userEvent.click(within(pane).getByRole("button", { name: "Refresh transcript" }));
    expect(await within(pane).findByText(/Could not load the transcript: The server had a problem/)).toBeInTheDocument();
    expect(within(pane).getByRole("button", { name: "Take over" })).toBeInTheDocument();
    mode = "empty";
    await userEvent.click(within(pane).getByRole("button", { name: "Refresh transcript" }));
    expect(await within(pane).findByText("Nothing has been said on this call yet.")).toBeInTheDocument();
    expect(n).toBeGreaterThanOrEqual(3);
  });

  it("is loaded for an ended call too, and a refusal (for example another tenant) is shown as an error", async () => {
    signedIn("staff", "staff1");
    backend([call(ENDED_AFTER)], { [`GET /api/staff/calls/${ID}/transcript`]: () => ({ status: 404, json: { detail: "call not found" } }) });
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    expect(await within(pane).findByText(/Could not load the transcript: call not found/)).toBeInTheDocument();
  });

  it("keeps refreshing while the call is live", async () => {
    signedIn("staff", "staff1");
    let n = 0;
    backend([call()], { [`GET /api/staff/calls/${ID}/transcript`]: () => { n++; return { json: TRANSCRIPT }; } });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderWithAuth(<LiveCalls />);
    await select();
    await waitFor(() => expect(n).toBeGreaterThanOrEqual(1));
    const before = n;
    await act(async () => { await vi.advanceTimersByTimeAsync(5200); });
    expect(n).toBeGreaterThan(before);
  });
});

describe("Live calls (M4.1): owner-only control", () => {
  it("another staff member and an admin are told only the owner can speak or end, and have no buttons for it", async () => {
    for (const [role, who] of [["staff", "staff2"], ["admin", "admin1"]] as const) {
      signedIn(role, who);
      backend([call({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", takeover_at: "2026-11-07T10:00:20Z" })]);
      const { unmount } = renderWithAuth(<LiveCalls />);
      const pane = await select();
      expect(within(pane).getByText(/staff1 has taken over this call\. You can listen, but only staff1 can speak on it or end it\./)).toBeInTheDocument();
      expect((btn(pane, "End call") as HTMLButtonElement).disabled).toBe(true);
      expect((btn(pane, "Take over") as HTMLButtonElement).disabled).toBe(true);
      expect(within(pane).queryByRole("button", { name: /microphone/i })).not.toBeInTheDocument();
      expect(within(pane).queryByRole("form", { name: "Call intervention feedback" })).not.toBeInTheDocument();
      unmount();
    }
  });

  it("a server refusal of a stale End click (another staff member took over meanwhile) is shown, and the list refreshes", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()], { [`POST /api/staff/calls/${ID}/end`]: () => { b.state.calls = [call({ status: "staff_takeover", owner: "staff", owner_staff: "staff2" })]; return { status: 403, json: { detail: "staff2 took over this call and is the only one who can end it" } }; } });
    renderWithAuth(<LiveCalls />);
    const pane = await select();
    await userEvent.click(btn(pane, "End call")); await userEvent.click(btn(pane, "Confirm end call"));
    expect(await within(pane).findByText("staff2 took over this call and is the only one who can end it")).toBeInTheDocument();
    await waitFor(() => expect((btn(pane, "End call") as HTMLButtonElement).disabled).toBe(true));
  });

  it("when the owner's connection drops, the others are told the call ends rather than returning to the assistant", async () => {
    signedIn("staff", "staff1");
    backend([call({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true })]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    await fk.msg({ type: "owner_disconnected", grace_s: 30 });
    expect(await within(pane).findByText(/lost their connection\. The call ends if they do not come back shortly; it does not return to the assistant/)).toBeInTheDocument();
  });
});

describe("Live calls (M4.1): the hand-over announcement", () => {
  it("shows that it is playing, keeps Enable microphone off until it finishes, and says so if it failed", async () => {
    signedIn("staff", "staff1");
    backend([call({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true })]); const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    await fk.msg({ type: "announcement", state: "started" });
    expect(await within(pane).findByText(/The hand-over announcement is playing to the caller: you can speak when it finishes/)).toBeInTheDocument();
    expect((btn(pane, "Enable microphone") as HTMLButtonElement).disabled).toBe(true);
    await fk.msg({ type: "announcement", state: "finished" });
    await waitFor(() => expect((btn(pane, "Enable microphone") as HTMLButtonElement).disabled).toBe(false));
    expect(within(pane).queryByText(/announcement is playing/)).not.toBeInTheDocument();
    await fk.msg({ type: "announcement", state: "failed" });
    expect(await within(pane).findByText(/The hand-over announcement could not be played to the caller\. You can still speak to them\./)).toBeInTheDocument();
    expect((btn(pane, "Enable microphone") as HTMLButtonElement).disabled).toBe(false);          // a failure never blocks the staff member
  });
});

describe("Live calls (M4.1): intervention feedback after the call", () => {
  const ended = (extra: Partial<LiveCall> = {}) => call({ ...ENDED_AFTER, is_mine: false, feedback: { state: "awaiting", form_open: true, review_status: null }, ...extra });
  const submitted = { ...FB_AWAITING, state: "submitted", form_open: false, review_status: "pending_review", category: "outdated_knowledge", category_label: "Outdated knowledge",
    what_was_wrong: "It quoted last year's fee table.", suggested_correction: "Use the 2026 table.", submitted_at: "2026-11-07T10:05:00Z", editable: true };

  it("is shown to the intervention owner after the call ended, with the exact title, prompt and category list", async () => {
    signedIn("staff", "staff1");
    backend([ended()], { [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: FB_AWAITING }) });
    renderWithAuth(<LiveCalls />);
    await select();
    const form = await screen.findByRole("form", { name: "Call intervention feedback" });
    expect(within(form).getByRole("heading", { name: "Call intervention feedback" })).toBeInTheDocument();
    expect(within(form).getByLabelText("What did the AI say or do that required your intervention?")).toBeRequired();
    expect(within(form).getByLabelText("What is the correct information? (optional)")).not.toBeRequired();
    const cats = within(within(form).getByLabelText("Category")).getAllByRole("option").map((o) => o.textContent);
    expect(cats).toEqual(["Choose a category…", "Incorrect factual information", "Outdated knowledge", "Misleading response", "Policy or compliance issue", "Customer dissatisfaction", "Missing knowledge", "Other"]);
    expect(within(form).getByRole("button", { name: "Submit feedback" })).toBeDisabled();       // nothing to submit yet
    expect(within(form).getByRole("button", { name: "Skip" })).toBeEnabled();
    expect(within(form).getByText(/proposed correction/)).toBeInTheDocument();
  });

  it("is not shown while the call is live, to anyone else, or for a call nobody took over", async () => {
    signedIn("staff", "staff1");
    backend([call({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true, feedback: { state: "awaiting", form_open: false, review_status: null } })]);
    const { unmount } = renderWithAuth(<LiveCalls />);
    await select();
    expect(screen.queryByRole("form", { name: "Call intervention feedback" })).not.toBeInTheDocument();
    unmount();
    backend([call(ENDED_AFTER)]);                                                                  // an ended intervention seen by someone else: the server sends no `feedback`
    const second = renderWithAuth(<LiveCalls />);
    await select();
    expect(screen.queryByRole("form", { name: "Call intervention feedback" })).not.toBeInTheDocument();
    expect(screen.queryByText("Call intervention feedback")).not.toBeInTheDocument();
    second.unmount();
    backend([call({ status: "ended", live: false, owner: "none", ended_at: "2026-11-07T10:03:00Z", end_reason: "caller_disconnected" })]);
    renderWithAuth(<LiveCalls />);
    await select();
    expect(screen.queryByText("Call intervention feedback")).not.toBeInTheDocument();
  });

  it("submits the category, what went wrong and the optional correction; then shows the submitted state with its review status", async () => {
    signedIn("staff", "staff1");
    let body: unknown; let saved = false;
    const b = backend([ended()], {
      [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: saved ? submitted : FB_AWAITING }),
      [`POST /api/staff/calls/${ID}/feedback`]: (x) => { body = x; saved = true; return { status: 201, json: submitted }; },
    });
    renderWithAuth(<LiveCalls />);
    await select();
    const form = await screen.findByRole("form", { name: "Call intervention feedback" });
    await userEvent.selectOptions(within(form).getByLabelText("Category"), "outdated_knowledge");
    await userEvent.type(within(form).getByLabelText("What did the AI say or do that required your intervention?"), "It quoted last year's fee table.");
    await userEvent.type(within(form).getByLabelText("What is the correct information? (optional)"), "Use the 2026 table.");
    await userEvent.click(within(form).getByRole("button", { name: "Submit feedback" }));
    expect(await screen.findByText(/Feedback submitted\./)).toBeInTheDocument();
    expect(body).toEqual({ category: "outdated_knowledge", what_was_wrong: "It quoted last year's fee table.", suggested_correction: "Use the 2026 table." });
    expect(screen.getByText("Pending review")).toBeInTheDocument();
    expect(screen.getByText(/does not change the assistant’s knowledge until an administrator approves it/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Call intervention feedback" })).not.toBeInTheDocument();      // no second submission form
    expect(b.f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "POST" && String(c[0]).endsWith("/feedback"))).toHaveLength(1);
  });

  it("a double click on Submit sends one request", async () => {
    signedIn("staff", "staff1");
    let n = 0; let release!: () => void; const gate = new Promise<void>((r) => { release = r; });
    backend([ended()], { [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: FB_AWAITING }), [`POST /api/staff/calls/${ID}/feedback`]: async () => { n++; await gate; return { status: 201, json: submitted }; } });
    renderWithAuth(<LiveCalls />);
    await select();
    const form = await screen.findByRole("form", { name: "Call intervention feedback" });
    await userEvent.selectOptions(within(form).getByLabelText("Category"), "other");
    await userEvent.type(within(form).getByLabelText("What did the AI say or do that required your intervention?"), "It guessed the answer.");
    const go = within(form).getByRole("button", { name: "Submit feedback" });
    fireEvent.click(go); fireEvent.click(go); fireEvent.click(go);
    release();
    await screen.findByText(/Feedback submitted\./);
    expect(n).toBe(1);
  });

  it("a server refusal (not the owner / already submitted) is shown and the form stays", async () => {
    signedIn("staff", "staff1");
    backend([ended()], { [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: FB_AWAITING }),
      [`POST /api/staff/calls/${ID}/feedback`]: () => ({ status: 403, json: { detail: "only the staff member who took over this call can write its feedback" } }) });
    renderWithAuth(<LiveCalls />);
    await select();
    const form = await screen.findByRole("form", { name: "Call intervention feedback" });
    await userEvent.selectOptions(within(form).getByLabelText("Category"), "other");
    await userEvent.type(within(form).getByLabelText("What did the AI say or do that required your intervention?"), "It guessed the answer.");
    await userEvent.click(within(form).getByRole("button", { name: "Submit feedback" }));
    expect(await within(form).findByText("only the staff member who took over this call can write its feedback")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Call intervention feedback" })).toBeInTheDocument();
  });

  it("Skip records that no feedback was given; the owner can still add feedback afterwards", async () => {
    signedIn("staff", "staff1");
    let skipped = 0;
    backend([ended()], { [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: FB_AWAITING }),
      [`POST /api/staff/calls/${ID}/feedback/skip`]: () => { skipped++; return { json: { ...FB_AWAITING, state: "skipped", form_open: true } }; } });
    renderWithAuth(<LiveCalls />);
    await select();
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    expect(await screen.findByText(/Feedback was skipped\. You can still add it now\./)).toBeInTheDocument();
    expect(skipped).toBe(1);
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit feedback" })).toBeInTheDocument();
  });

  it("a submitted record can be edited until it is reviewed; after review it is read-only", async () => {
    signedIn("staff", "staff1");
    let current: unknown = submitted; let put: unknown;
    backend([ended({ feedback: { state: "submitted", form_open: false, review_status: "pending_review" } })], {
      [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: current }),
      [`PUT /api/staff/calls/${ID}/feedback`]: (x) => { put = x; current = { ...submitted, category: "missing_knowledge", category_label: "Missing knowledge" }; return { json: current }; },
    });
    renderWithAuth(<LiveCalls />);
    await select();
    await userEvent.click(await screen.findByRole("button", { name: "Edit feedback" }));
    const form = await screen.findByRole("form", { name: "Call intervention feedback" });
    expect(within(form).getByLabelText("What did the AI say or do that required your intervention?")).toHaveValue("It quoted last year's fee table.");
    await userEvent.selectOptions(within(form).getByLabelText("Category"), "missing_knowledge");
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Missing knowledge")).toBeInTheDocument();
    expect(put).toMatchObject({ category: "missing_knowledge", what_was_wrong: "It quoted last year's fee table." });
  });

  it("after review the owner sees the status and cannot edit", async () => {
    signedIn("staff", "staff1");
    backend([ended({ feedback: { state: "submitted", form_open: false, review_status: "approved_for_update" } })], {
      [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: { ...submitted, review_status: "approved_for_update", editable: false, review_note: "Confirmed with accounts." } }) });
    renderWithAuth(<LiveCalls />);
    await select();
    expect(await screen.findByText("Approved for knowledge update")).toBeInTheDocument();
    expect(screen.getByText("Confirmed with accounts.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit feedback" })).not.toBeInTheDocument();
    expect(screen.getByText(/reviewed and can no longer be edited/)).toBeInTheDocument();
  });

  it("lets the author point at the AI message concerned, from the call's own transcript", async () => {
    signedIn("staff", "staff1");
    let body: any;
    backend([ended()], { [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: FB_AWAITING }), [`POST /api/staff/calls/${ID}/feedback`]: (x) => { body = x; return { status: 201, json: submitted }; } });
    renderWithAuth(<LiveCalls />);
    await select();
    const form = await screen.findByRole("form", { name: "Call intervention feedback" });
    await userEvent.selectOptions(await within(form).findByLabelText("Which AI message was it? (optional)"), "1");
    await userEvent.selectOptions(within(form).getByLabelText("Category"), "incorrect_information");
    await userEvent.type(within(form).getByLabelText("What did the AI say or do that required your intervention?"), "Wrong fee rule.");
    await userEvent.click(within(form).getByRole("button", { name: "Submit feedback" }));
    await waitFor(() => expect(body).toMatchObject({ related_index: 1 }));
  });
});

describe("Staff console (M4.1): my intervention feedback", () => {
  it("lists only my interventions with their status and opens the form for one that still needs feedback", async () => {
    signedIn("staff", "staff1");
    mockApi({
      "GET /api/staff/escalations": () => ({ json: [] }),
      "GET /api/staff/interventions": () => ({ json: { categories: FB_AWAITING.categories, items: [
        { ...FB_AWAITING }, { ...FB_AWAITING, call_id: "c_2", short_id: "bbbb2222", state: "submitted", form_open: false, review_status: "rejected", category_label: "Other", what_was_wrong: "x" } ] } }),
      [`GET /api/staff/calls/${ID}/feedback`]: () => ({ json: FB_AWAITING }),
    });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(await screen.findByRole("tab", { name: "My intervention feedback" }));
    expect(await screen.findByRole("button", { name: "Intervention on call aaaa1111: Feedback needed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Intervention on call bbbb2222: Rejected" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /call aaaa1111/ }));
    expect(await screen.findByRole("form", { name: "Call intervention feedback" })).toBeInTheDocument();
  });

  it("says so when there are none, and shows an error when the list cannot load", async () => {
    signedIn("staff", "staff1");
    let fail = false;
    mockApi({ "GET /api/staff/escalations": () => ({ json: [] }), "GET /api/staff/interventions": () => (fail ? { status: 500, json: {} } : { json: { items: [], categories: {} } }) });
    renderWithAuth(<StaffConsole />);
    await userEvent.click(await screen.findByRole("tab", { name: "My intervention feedback" }));
    expect(await screen.findByText("You have not taken over any call yet.")).toBeInTheDocument();
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("The server had a problem. Please try again.")).toBeInTheDocument();
  });

  it("the staff console has no trial administration", async () => {
    signedIn("staff", "staff1");
    mockApi({ "GET /api/staff/escalations": () => ({ json: [] }) });
    renderWithAuth(<StaffConsole />);
    await screen.findByRole("tab", { name: "Live calls" });
    expect(screen.queryByRole("tab", { name: /trial/i })).not.toBeInTheDocument();
  });
});
