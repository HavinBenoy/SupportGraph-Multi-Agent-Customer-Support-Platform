import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceCall, type VoiceDeps } from "./VoiceCall";
import { CONFIG, mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { ServerMsg } from "@/lib/voice";

afterEach(() => { vi.unstubAllGlobals(); });

function rig(opts: { status?: unknown; micError?: string } = {}) {
  const sent: (string | ArrayBuffer)[] = [];
  let h: Parameters<VoiceDeps["socket"]>[1] | null = null;
  const micStop = vi.fn(), mute = vi.fn(), pushed: ArrayBuffer[] = [];
  const player = { begin: vi.fn(), push: (_turn: number, b: ArrayBuffer) => pushed.push(b), stop: vi.fn(), end: vi.fn(), playing: false, current: null as number | null,
    onidle: undefined as undefined | (() => void), onplayed: undefined as undefined | ((t: number) => void), onstarted: undefined as undefined | ((t: number) => void) };
  const close = vi.fn();
  let frame: ((p: ArrayBuffer) => void) | null = null;
  const deps: VoiceDeps = {
    socket: (_url, handlers) => { h = handlers; return { send: (d) => sent.push(d), close }; },
    mic: async (onFrame) => { if (opts.micError) throw new Error(opts.micError); frame = onFrame; return { stop: micStop, mute }; },
    player: () => player, wsUrl: () => "ws://test/api/voice/ws",
  };
  const f = mockApi({
    "GET /api/config": () => ({ json: CONFIG }),
    "GET /api/voice/status": () => ({ json: opts.status ?? { enabled: true, ready: true, problems: [] } }),
  });
  const serverSays = (m: ServerMsg) => act(() => h!.onmessage(JSON.stringify(m)));
  return { deps, sent, serverSays, serverAudio: (b: ArrayBuffer) => act(() => h!.onmessage(b)), micStop, mute, player, pushed, close, f, open: () => act(() => h!.onopen()), mic: (p: ArrayBuffer) => frame!(p), onclose: () => act(() => h!.onclose(false)) };
}
const start = async () => userEvent.click(await screen.findByRole("button", { name: "Start call" }));
const READY: ServerMsg = { type: "ready", ticket_id: "a-9.sig", signed_in: false, tenant_name: "Test Institute", sample_rate: 22050 };

describe("VoiceCall", () => {
  it("a guest call: start message, transcript, spoken reply with its source, ticket saved for the text chat", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start();
    await r.open();
    expect(JSON.parse(String(r.sent[0]))).toEqual({ type: "start", tenant: "t1", token: null, ticket_id: null });
    await r.serverSays(READY);
    expect(await screen.findByText(/Listening/)).toBeInTheDocument();
    expect(sessionStorage.getItem("sg.ticket.guest")).toBe("a-9.sig");
    r.mic(new Int16Array([1, 2]).buffer);
    expect(r.sent.at(-1)).toBeInstanceOf(ArrayBuffer);   // microphone frames go straight to the socket

    await r.serverSays({ type: "speech_start" });
    expect(screen.getByText(/Hearing you/)).toBeInTheDocument();
    await r.serverSays({ type: "transcript", turn_id: 1, text: "what is the fee", final: true });
    await r.serverSays({ type: "thinking", turn_id: 1 });
    await r.serverSays({ type: "reply", reply: "The fee is 60% at registration.", status: "answered", citations: [{ id: "fees#2", heading: "Fees" }], escalation_id: null, ticket_id: "a-9.sig", turn_id: 1 });
    await r.serverSays({ type: "audio_start", turn_id: 1, sample_rate: 22050 });
    expect(await screen.findByText("what is the fee")).toBeInTheDocument();
    expect(screen.getByText("The fee is 60% at registration.")).toBeInTheDocument();
    expect(screen.getByText("Fees")).toBeInTheDocument();
    expect(r.player.begin).toHaveBeenCalledWith(1, 22050);
    await r.serverSays({ type: "audio_end", turn_id: 1 });
    expect(r.player.end).toHaveBeenCalledWith(1);
    await waitFor(() => expect(screen.getByText(/Listening/)).toBeInTheDocument());
  });

  it("raw speech_start does NOT stop playback; the server's interrupted{turn_id} does (M2.2 barge-in)", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "audio_start", turn_id: 4, sample_rate: 22050 });
    await r.serverSays({ type: "speech_start" });                 // a cough, a click, the bot's own echo
    expect(r.player.stop).not.toHaveBeenCalled();
    await r.serverSays({ type: "interrupted", turn_id: 4 });      // the server decided it was real
    expect(r.player.stop).toHaveBeenCalledWith(4);
  });

  it("audio frames are parsed (turn, seq, pcm) and routed to the player by turn", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    const frame = new Uint8Array(8 + 4); new DataView(frame.buffer).setUint32(0, 7, true); new DataView(frame.buffer).setUint32(4, 3, true); frame.set([1, 0, 2, 0], 8);
    const push = vi.spyOn(r.player, "push");
    await r.serverAudio(frame.buffer);
    expect(push).toHaveBeenCalledTimes(1);
    expect(push.mock.calls[0][0]).toBe(7);
    expect(new Uint8Array(push.mock.calls[0][1])).toEqual(new Uint8Array([1, 0, 2, 0]));
    await r.serverAudio(new ArrayBuffer(3));                       // shorter than the header: ignored, no crash
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("an interrupted audio_end stops that response instead of letting it drain", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "audio_end", turn_id: 2, interrupted: true });
    expect(r.player.stop).toHaveBeenCalledWith(2);
    expect(r.player.end).not.toHaveBeenCalled();
  });

  it("tells the server when a response has finished playing", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    r.player.onplayed?.(9);
    expect(r.sent.map(String).some((m) => m.includes('"playback"') && m.includes('"turn_id":9') && m.includes('"ended"'))).toBe(true);
  });

  it("a merged utterance is ONE transcript line, updated in place; a stale answer is shown but marked", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "transcript", turn_id: 3, text: "I want to know about the course", final: false, segments: 1 });
    await r.serverSays({ type: "transcript", turn_id: 3, text: "I want to know about the course duration and eligibility", final: false, segments: 2 });
    await r.serverSays({ type: "transcript", turn_id: 3, text: "I want to know about the course duration and eligibility", final: true, segments: 2 });
    expect(screen.queryByText("I want to know about the course")).toBeNull();
    expect(screen.getAllByText("I want to know about the course duration and eligibility")).toHaveLength(1);
    await r.serverSays({ type: "reply", turn_id: 2, stale: true, reply: "An old answer.", status: "answered", citations: [], escalation_id: null, ticket_id: "a-9.sig" });
    expect(screen.getByText("An old answer.")).toBeInTheDocument();
    expect(screen.getByText(/Not spoken/)).toBeInTheDocument();
  });

  it("a second call (turn ids restart at 1) never overwrites the first call's transcript", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "transcript", turn_id: 1, text: "first call question", final: true });
    await userEvent.click(screen.getByRole("button", { name: "End call" }));
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "transcript", turn_id: 1, text: "second call question", final: false });
    await r.serverSays({ type: "transcript", turn_id: 1, text: "second call question", final: true });
    expect(screen.getByText("first call question")).toBeInTheDocument();
    expect(screen.getByText("second call question")).toBeInTheDocument();
  });

  it("a holding phrase does not end the thinking phase; the real answer follows", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "thinking", turn_id: 1 });
    await r.serverSays({ type: "audio_start", turn_id: 1, sample_rate: 22050, filler: true });
    await r.serverSays({ type: "audio_end", turn_id: 1, filler: true });
    expect(screen.getByText(/Thinking/)).toBeInTheDocument();
  });

  it("signed-in student: token goes in the start message and the saved ticket is resumed", async () => {
    signedIn("student", "asha");
    sessionStorage.setItem("sg.ticket.t1.asha", "s-7.sig");
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open();
    expect(JSON.parse(String(r.sent[0]))).toMatchObject({ type: "start", token: "tok-asha", ticket_id: "s-7.sig" });
  });

  it("shows escalation and approval banners", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "reply", reply: "This needs a person.", status: "escalated", citations: [], escalation_id: "ESC-1", ticket_id: "a-9.sig" });
    expect(await screen.findByText(/staff member will follow up/i)).toBeInTheDocument();
    await r.serverSays({ type: "reply", reply: "Submitted.", status: "awaiting_staff", citations: [], escalation_id: null, ticket_id: "a-9.sig" });
    expect(await screen.findByText(/waiting for a staff member/i)).toBeInTheDocument();
  });

  it("explains a microphone problem and stays out of the call", async () => {
    const r = rig({ micError: "Permission denied" });
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start();
    expect(await screen.findByRole("alert")).toHaveTextContent("Permission denied");
    expect(screen.getByRole("button", { name: "Start call" })).toBeEnabled();
  });

  it("blocks the call and lists what is missing when the server's voice models are not ready", async () => {
    const r = rig({ status: { enabled: true, ready: false, problems: ["SG_VOICE_TTS_VOICE is not set (run `make voice-setup`)"] } });
    renderWithAuth(<VoiceCall deps={r.deps} />);
    expect(await screen.findByText(/Voice is not ready/)).toBeInTheDocument();
    expect(screen.getByText(/voice-setup/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Start call" })).toBeDisabled();
  });

  it("fatal server error ends the call and releases the microphone", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "error", detail: "This call has reached its time limit.", fatal: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("time limit");
    expect(r.micStop).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start call" })).toBeInTheDocument();
  });

  it("End call tells the server, closes the socket and stops the microphone; mute is forwarded", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await userEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
    expect(r.mute).toHaveBeenCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: "End call" }));
    expect(r.sent.some((m) => typeof m === "string" && m.includes('"stop"'))).toBe(true);
    expect(r.close).toHaveBeenCalled(); expect(r.micStop).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start call" })).toBeInTheDocument();
  });

  it("typing during a call sends a text message", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await userEvent.type(screen.getByLabelText("Type instead"), "hello{Enter}");
    expect(r.sent.map(String).some((m) => m.includes('"text":"hello"'))).toBe(true);
  });

  it("staff accounts are told the voice line is for students and visitors", async () => {
    signedIn("staff", "staff1");
    renderWithAuth(<VoiceCall deps={rig().deps} />);
    expect(await screen.findByText(/for students and visitors/)).toBeInTheDocument();
  });

  it("a goodbye ends the call after the closing has played, without an error", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start();
    await r.open();
    await r.serverSays(READY);
    await r.serverSays({ type: "reply", reply: "Thanks for getting in touch. Take care, goodbye!", status: "answered", citations: [], escalation_id: null, ticket_id: "a-9.sig", end_call: true });
    await r.serverSays({ type: "audio_start", turn_id: 1, sample_rate: 22050 });
    await r.serverSays({ type: "audio_end", turn_id: 1 });
    r.player.playing = true;                              // the closing is still being played in the browser
    await r.serverSays({ type: "call_end", reason: "goodbye" });
    expect(screen.queryByRole("button", { name: "Start call" })).toBeNull();   // waits for the queued speech instead of cutting it off
    await act(async () => { r.player.onidle?.(); });      // playback finished
    await waitFor(() => expect(screen.getByRole("button", { name: "Start call" })).toBeInTheDocument());
    expect(r.micStop).toHaveBeenCalled();
    expect(screen.getByText(/Call ended/)).toBeInTheDocument();
    await r.onclose();                                    // the server's close arrives afterwards: no error banner
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("VoiceCall: first audible sound ack (M3)", () => {
  it("tells the server when playback became audible and whether it was a holding phrase", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start();
    await r.open();
    await r.serverSays(READY);
    await r.serverSays({ type: "audio_start", turn_id: 5, sample_rate: 22050, filler: true });
    act(() => r.player.onstarted?.(5));
    await r.serverSays({ type: "audio_end", turn_id: 5, filler: true });
    await r.serverSays({ type: "audio_start", turn_id: 5, sample_rate: 22050 });
    act(() => r.player.onstarted?.(5));
    const acks = r.sent.map(String).filter((m) => m.includes('"started"')).map((m) => JSON.parse(m));
    expect(acks).toEqual([{ type: "playback", turn_id: 5, state: "started", filler: true }, { type: "playback", turn_id: 5, state: "started", filler: false }]);
  });
});

describe("VoiceCall: staff takeover (M4)", () => {
  async function inCall() {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start();
    await r.open();
    await r.serverSays(READY);
    await screen.findByText(/Listening/);
    return r;
  }

  it("tells the caller a staff member joined, pauses typing, and stops the assistant's audio that was playing", async () => {
    const r = await inCall();
    await r.serverSays({ type: "interrupted", turn_id: 3, reason: "takeover" });
    expect(r.player.stop).toHaveBeenCalledWith(3);
    await r.serverSays({ type: "ownership", owner: "staff", epoch: 2 });
    expect(await screen.findByText(/A staff member has joined your call\. The assistant will not speak again on this call\./)).toBeInTheDocument();
    expect(screen.getByText(/You are speaking with a staff member/)).toBeInTheDocument();
    expect(screen.getByLabelText("Type instead")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "End call" })).toBeEnabled();      // the caller can always hang up
  });

  it("plays the staff member's voice as a normal response, with a bigger start-up buffer than the assistant's", async () => {
    const r = await inCall();
    await r.serverSays({ type: "ownership", owner: "staff", epoch: 2 });
    await r.serverSays({ type: "audio_start", turn_id: 5, sample_rate: 16000, staff: true });
    expect(r.player.begin).toHaveBeenCalledWith(5, 16000, 150);
    await r.serverSays({ type: "audio_start", turn_id: 6, sample_rate: 22050 });
    expect(r.player.begin).toHaveBeenLastCalledWith(6, 22050);                    // the assistant's calls keep their original two-argument form
    await r.serverSays({ type: "audio_end", turn_id: 5, staff: true });
    expect(r.player.end).toHaveBeenCalledWith(5);
  });

  it("there is no hand-back: once a staff member has the call, typing stays off and the assistant never announces it is back", async () => {
    const r = await inCall();
    await r.serverSays({ type: "ownership", owner: "staff", epoch: 2 });
    await r.serverSays({ type: "ownership", owner: "ai", epoch: 4 } as ServerMsg);          // not something the server sends any more; the screen must not act on it
    expect(screen.queryByText("The assistant is back on the call.")).not.toBeInTheDocument();
    expect(screen.getByText(/You are speaking with a staff member/)).toBeInTheDocument();
    expect(screen.getByLabelText("Type instead")).toBeDisabled();
  });

  it("the hand-over announcement is played like any spoken response and is not shown as an assistant message", async () => {
    const r = await inCall();
    await r.serverSays({ type: "ownership", owner: "staff", epoch: 2 });
    await r.serverSays({ type: "audio_start", turn_id: 9, sample_rate: 22050, notice: "handover" });
    expect(r.player.begin).toHaveBeenLastCalledWith(9, 22050);
    await r.serverSays({ type: "audio_end", turn_id: 9, notice: "handover" });
    expect(r.player.end).toHaveBeenCalledWith(9);
    expect(screen.queryByText(/I want to make sure you receive accurate information/)).not.toBeInTheDocument();      // spoken, never a chat line from the assistant
  });

  it("a call ended by staff says so and releases the microphone and socket", async () => {
    const r = await inCall();
    await r.serverSays({ type: "call_end", reason: "ended_by_staff" });
    expect(await screen.findByText(/The call was ended by a staff member/)).toBeInTheDocument();
    expect(r.micStop).toHaveBeenCalled();
    expect(r.close).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "End call" })).not.toBeInTheDocument();
  });
});


describe("VoiceCall: trial notice and recording consent (M5)", () => {
  const OFFER = { type: "trial_notice", disclosure: "You're speaking with our AI support assistant. During this trial, a member of our support team may monitor the conversation and step in whenever additional assistance is needed.",
                  recording: { offered: true, policy_version: 3, notice: "If you agree, we will record the audio of this call. Recording is optional.", retention_days: 30 } } as const;
  const NO_OFFER = { type: "trial_notice", disclosure: OFFER.disclosure, recording: { offered: false } } as const;
  const sentJson = (r: ReturnType<typeof rig>) => r.sent.filter((x): x is string => typeof x === "string").map((x) => JSON.parse(x));
  async function inTrial(notice: ServerMsg = OFFER) {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays(notice);
    return r;
  }

  it("a normal call shows no trial notice and no recording choice", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await screen.findByText(/Listening/);
    expect(screen.queryByRole("region", { name: "Trial call notice" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /recording/i })).not.toBeInTheDocument();
  });

  it("shows the disclosure and the consent choice, and sends NOTHING until the caller presses a button", async () => {
    const r = await inTrial();
    const box = await screen.findByRole("region", { name: "Trial call notice" });
    expect(box).toHaveTextContent("You're speaking with our AI support assistant");
    expect(box).toHaveTextContent("may monitor the conversation");
    expect(box).toHaveTextContent("Recording is optional");
    expect(box).toHaveTextContent("This call is not being recorded");
    expect(sentJson(r).some((m) => m.type === "consent")).toBe(false);                 // viewing, speaking and waiting never grant consent
    await r.serverSays({ type: "transcript", turn_id: 1, text: "yes you can record me", final: true });      // even saying "yes" is not consent
    expect(sentJson(r).some((m) => m.type === "consent")).toBe(false);
    expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument();
  });

  it("Allow recording sends an explicit decision with the policy version it showed; recording is only shown as on when the server says so", async () => {
    const r = await inTrial();
    await userEvent.click(await screen.findByRole("button", { name: "Allow recording" }));
    expect(sentJson(r).filter((m) => m.type === "consent")).toEqual([{ type: "consent", decision: "granted", policy_version: 3 }]);
    expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument();       // nothing is claimed before the server confirms
    await r.serverSays({ type: "recording", state: "on" });
    await r.serverSays({ type: "consent", status: "granted", recording: true });
    expect(await screen.findByLabelText("Recording indicator")).toHaveTextContent("Recording");
    expect(screen.getByText("Recording is on.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow recording" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "No thanks" })).not.toBeInTheDocument();
  });

  it("No thanks declines, the call carries on, and the caller can still change their mind", async () => {
    const r = await inTrial();
    await userEvent.click(await screen.findByRole("button", { name: "No thanks" }));
    expect(sentJson(r).filter((m) => m.type === "consent")).toEqual([{ type: "consent", decision: "declined", policy_version: 3 }]);
    await r.serverSays({ type: "consent", status: "declined", recording: false });
    expect(await screen.findByText(/You said no\. The call works exactly the same\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End call" })).toBeEnabled();
    expect(screen.getByLabelText("Type instead")).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Allow recording after all" }));
    expect(sentJson(r).filter((m) => m.type === "consent").at(-1)).toEqual({ type: "consent", decision: "granted", policy_version: 3 });
  });

  it("Stop recording withdraws consent; the indicator goes away when the server confirms it stopped", async () => {
    const r = await inTrial();
    await userEvent.click(await screen.findByRole("button", { name: "Allow recording" }));
    await r.serverSays({ type: "recording", state: "on" });
    await r.serverSays({ type: "consent", status: "granted", recording: true });
    await userEvent.click(await screen.findByRole("button", { name: "Stop recording" }));
    expect(sentJson(r).filter((m) => m.type === "consent").at(-1)).toEqual({ type: "consent", decision: "withdrawn", policy_version: 3 });
    await r.serverSays({ type: "recording", state: "off", reason: "consent_withdrawn" });
    await r.serverSays({ type: "consent", status: "withdrawn", recording: false });
    await waitFor(() => expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument());
    expect(screen.getByText(/You stopped the recording/)).toBeInTheDocument();
  });

  it("a refusal or a storage failure is said plainly: not recording, call unaffected", async () => {
    const r = await inTrial();
    await userEvent.click(await screen.findByRole("button", { name: "Allow recording" }));
    await r.serverSays({ type: "recording", state: "off", reason: "storage_unavailable" });
    await r.serverSays({ type: "consent", status: "granted", recording: false });
    expect(await screen.findByText(/this call is not being recorded\. The call itself is unaffected/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument();
    await r.serverSays({ type: "consent", error: "the recording terms changed; please read them again" });
    expect(await screen.findByText("the recording terms changed; please read them again")).toBeInTheDocument();
  });

  it("an administrator switching trial mode off while recording clears the indicator", async () => {
    const r = await inTrial();
    await r.serverSays({ type: "recording", state: "on" });
    expect(await screen.findByLabelText("Recording indicator")).toBeInTheDocument();
    await r.serverSays({ type: "recording", state: "off", reason: "trial_disabled" });
    await waitFor(() => expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument());
    expect(screen.getByText(/Recording was switched off/)).toBeInTheDocument();
  });

  it("when recording is not offered only the monitoring disclosure is shown: no recording buttons, no consent can be sent", async () => {
    const r = await inTrial(NO_OFFER);
    const box = await screen.findByRole("region", { name: "Trial call notice" });
    expect(box).toHaveTextContent("may monitor the conversation");
    expect(screen.queryByRole("button", { name: /recording/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "No thanks" })).not.toBeInTheDocument();
    expect(sentJson(r).some((m) => m.type === "consent")).toBe(false);
  });

  it("the notice and recording state are cleared when the call ends, so the next call starts with no consent", async () => {
    const r = await inTrial();
    await userEvent.click(await screen.findByRole("button", { name: "Allow recording" }));
    await r.serverSays({ type: "recording", state: "on" });
    await userEvent.click(screen.getByRole("button", { name: "End call" }));
    expect(screen.queryByRole("region", { name: "Trial call notice" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Recording indicator")).not.toBeInTheDocument();
  });
});
