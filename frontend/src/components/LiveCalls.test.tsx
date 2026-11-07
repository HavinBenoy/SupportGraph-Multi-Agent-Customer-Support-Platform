import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LiveCalls, type LiveCallsDeps } from "./LiveCalls";
import { StaffConsole } from "./StaffConsole";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { LiveClient, LiveHandlers } from "@/lib/live";
import type { LiveCall } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

const call = (o: Partial<LiveCall> = {}): LiveCall => ({
  id: "c_aaaa1111bbbb2222", short_id: "aaaa1111", status: "active_ai", owner: "ai", owner_staff: null, epoch: 1, ticket_ref: "s-1234abcd", caller: "student",
  created_at: "2026-11-07T10:00:00Z", connected_at: "2026-11-07T10:00:01Z", takeover_at: null, returned_at: null, ended_at: null, end_reason: null,
  duration_s: 125, live: true, listeners: 0, listening_staff: [], mic_open: false, is_mine: false, listening: false, ...o,
});
const ID = "c_aaaa1111bbbb2222";

/** A fake audio client that records what the component asks of it, and lets the test play the server's part. */
function fake() {
  let h!: LiveHandlers;
  const log: string[] = [];
  const c = {
    state: "idle", speaking: false, epoch: 0,
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
  const set = (c: Partial<LiveCall>) => { state.calls = state.calls.map((x) => (x.id === ID ? { ...x, ...c } : x)); return { json: state.calls.find((x) => x.id === ID) }; };
  const f = mockApi({
    "GET /api/staff/calls": () => ({ json: state.calls }),
    [`POST /api/staff/calls/${ID}/listen`]: () => ({ json: { ...state.calls[0], ws_path: "x" } }),
    [`POST /api/staff/calls/${ID}/takeover`]: () => set({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true, epoch: 2 }),
    [`POST /api/staff/calls/${ID}/return`]: () => set({ status: "active_ai", owner: "ai", owner_staff: null, is_mine: false, epoch: 4 }),
    [`POST /api/staff/calls/${ID}/end`]: () => set({ status: "ended", live: false, owner: "none", owner_staff: null, is_mine: false, ended_at: "2026-11-07T10:05:00Z", end_reason: "ended_by_staff" }),
    ...extra,
  });
  return { state, f, set };
}
const posts = (f: ReturnType<typeof mockApi>) => f.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "POST").map((c) => String(c[0]).replace("/api/staff/calls/", ""));
const select = async () => { await userEvent.click(await screen.findByRole("button", { name: /Call aaaa1111/ })); return screen.getByRole("region", { name: "Call detail" }); };
const btn = (pane: HTMLElement, name: string | RegExp) => within(pane).getByRole("button", { name });
const enabled = (pane: HTMLElement) => ["Listen", "Stop listening", "Take over", "Return to AI", "End call"].filter((n) => !(btn(pane, n) as HTMLButtonElement).disabled);

describe("Live calls: the list", () => {
  it("shows short id, status, owner, caller reference, start time, duration and who is listening", async () => {
    signedIn("staff", "staff1");
    backend([call({ listeners: 2, listening_staff: ["staff2", "admin1"] }), call({ id: "c_zzzz", short_id: "zzzz9999", status: "staff_takeover", owner: "staff", owner_staff: "staff2", caller: "anonymous", ticket_ref: "a-77aa", duration_s: 5 })]);
    renderWithAuth(<LiveCalls />);
    const a = await screen.findByRole("button", { name: /Call aaaa1111, Assistant on call/ });
    expect(within(a).getByText("2 listening")).toBeInTheDocument();
    expect(a.textContent).toMatch(/Assistant · student s-1234abcd · started .* · 2m 0\ds/);
    const b = screen.getByRole("button", { name: /Call zzzz9999, Staff on call/ });
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
    await screen.findByRole("button", { name: /Assistant on call/ });
    b.state.calls = [call({ status: "staff_takeover", owner: "staff", owner_staff: "staff2", epoch: 2 })];
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(await screen.findByRole("button", { name: /Staff on call/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Staff on call/ }).textContent).toMatch(/Staff: staff2/);
  });
});

describe("Live calls: which controls the server's state allows", () => {
  const cases: [string, Partial<LiveCall>, string, string[]][] = [
    ["AI owns a live call", {}, "staff1", ["Listen", "Take over", "End call"]],
    ["I hold the call", { status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true }, "staff1", ["Listen", "Return to AI", "End call"]],
    ["another staff member holds it (I am staff)", { status: "staff_takeover", owner: "staff", owner_staff: "staff2" }, "staff1", ["Listen", "End call"]],
    ["another staff member holds it (I am admin)", { status: "staff_takeover", owner: "staff", owner_staff: "staff2" }, "admin1", ["Listen", "Return to AI", "End call"]],
    ["it is returning to the AI", { status: "returning_to_ai", owner: "none" }, "staff1", ["Listen", "End call"]],
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
    expect(within(pane).queryByRole("log")).not.toBeInTheDocument();
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

describe("Live calls: take over, return, end", () => {
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
    expect(enabled(pane)).toEqual(["Stop listening", "Return to AI", "End call"]);
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

  it("return to AI: the microphone stops BEFORE the server is asked, and the new state comes from the server", async () => {
    signedIn("staff", "staff1");
    const order: string[] = [];
    const fk = fake();
    fk.c.stopSpeaking.mockImplementation(() => { order.push("stopSpeaking"); });
    const b = backend([call({ status: "staff_takeover", owner: "staff", owner_staff: "staff1", is_mine: true, epoch: 2 })],
      { [`POST /api/staff/calls/${ID}/return`]: () => { order.push("POST return"); return b.set({ status: "active_ai", owner: "ai", owner_staff: null, is_mine: false, epoch: 4 }); } });
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Listen"));                      // reconnecting while holding the call (also reopens the microphone)
    await waitFor(() => expect(fk.c.connect).toHaveBeenCalled());
    await fk.state("joined");
    expect(fk.c.speak).toHaveBeenCalledTimes(1);
    order.length = 0;
    await userEvent.click(btn(pane, "Return to AI"));
    expect(await within(pane).findByText(/Call aaaa1111 is back with the assistant/)).toBeInTheDocument();
    expect(order).toEqual(["stopSpeaking", "POST return"]);
    expect(within(pane).getByText("Assistant", { selector: "dd" })).toBeInTheDocument();
    expect(enabled(pane)).toEqual(["Stop listening", "Take over", "End call"]);     // handing the call back does not stop you listening
  });

  it("a takeover that loses the race shows the server's message, refreshes, and offers no takeover", async () => {
    signedIn("staff", "staff1");
    const b = backend([call()], { [`POST /api/staff/calls/${ID}/takeover`]: () => { b.state.calls = [call({ status: "staff_takeover", owner: "staff", owner_staff: "staff2", epoch: 2 })]; return { status: 409, json: { detail: "another staff member has already taken over this call" } }; } });
    const fk = fake();
    renderWithAuth(<LiveCalls deps={fk.deps} />);
    const pane = await select();
    await userEvent.click(btn(pane, "Take over"));
    expect(await within(pane).findByText("another staff member has already taken over this call")).toBeInTheDocument();
    await waitFor(() => expect(enabled(pane)).toEqual(["Listen", "End call"]));
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
