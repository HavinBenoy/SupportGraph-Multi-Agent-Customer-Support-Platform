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
  const player = { start: vi.fn(), push: (b: ArrayBuffer) => pushed.push(b), stop: vi.fn(), end: vi.fn(), playing: false, onidle: undefined as undefined | (() => void) };
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
  return { deps, sent, serverSays, micStop, mute, player, pushed, close, f, open: () => act(() => h!.onopen()), mic: (p: ArrayBuffer) => frame!(p), onclose: () => act(() => h!.onclose(false)) };
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
    await r.serverSays({ type: "transcript", text: "what is the fee", final: true });
    await r.serverSays({ type: "thinking" });
    await r.serverSays({ type: "reply", reply: "The fee is 60% at registration.", status: "answered", citations: [{ id: "fees#2", heading: "Fees" }], escalation_id: null, ticket_id: "a-9.sig" });
    await r.serverSays({ type: "audio_start", sample_rate: 22050 });
    expect(await screen.findByText("what is the fee")).toBeInTheDocument();
    expect(screen.getByText("The fee is 60% at registration.")).toBeInTheDocument();
    expect(screen.getByText("Fees")).toBeInTheDocument();
    expect(r.player.start).toHaveBeenCalledWith(22050);
    await r.serverSays({ type: "audio_end" });
    await waitFor(() => expect(screen.getByText(/Listening/)).toBeInTheDocument());
  });

  it("speech from the caller stops the bot's playback immediately (barge-in)", async () => {
    const r = rig();
    renderWithAuth(<VoiceCall deps={r.deps} />);
    await start(); await r.open(); await r.serverSays(READY);
    await r.serverSays({ type: "audio_start", sample_rate: 22050 });
    await r.serverSays({ type: "speech_start" });
    expect(r.player.stop).toHaveBeenCalled();
    await r.serverSays({ type: "interrupted" });
    expect(r.player.stop.mock.calls.length).toBeGreaterThanOrEqual(2);
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
    await r.serverSays({ type: "audio_start", sample_rate: 22050 });
    await r.serverSays({ type: "audio_end" });
    await r.serverSays({ type: "call_end", reason: "goodbye" });
    expect(r.player.end).toHaveBeenCalled();              // waits for the queued speech instead of cutting it off
    await act(async () => { r.player.onidle?.(); });      // playback finished
    await waitFor(() => expect(screen.getByRole("button", { name: "Start call" })).toBeInTheDocument());
    expect(r.micStop).toHaveBeenCalled();
    expect(screen.getByText(/Call ended/)).toBeInTheDocument();
    await r.onclose();                                    // the server's close arrives afterwards: no error banner
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
