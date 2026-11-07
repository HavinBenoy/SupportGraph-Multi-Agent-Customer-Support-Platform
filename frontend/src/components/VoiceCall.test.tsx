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
    onidle: undefined as undefined | (() => void), onplayed: undefined as undefined | ((t: number) => void) };
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
