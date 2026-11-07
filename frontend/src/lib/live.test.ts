import { createLiveClient, createMixer, liveWsUrl, type LiveDeps, type LiveMsg } from "./live";
import type { Mic } from "./voice";

function rig(opts: { micError?: string; micGate?: Promise<void> } = {}) {
  let h: Parameters<LiveDeps["socket"]>[1] | null = null;
  const sent: (string | ArrayBuffer)[] = [];
  const close = vi.fn(), micStop = vi.fn(), micMute = vi.fn();
  let frame: ((p: ArrayBuffer) => void) | null = null;
  const played: { ch: string; rate: number; bytes: number }[] = [], reset: string[] = [];
  const mixerClose = vi.fn();
  const deps: LiveDeps = {
    socket: (_u, handlers) => { h = handlers; return { send: (d) => sent.push(d), close }; },
    mic: async (onFrame) => { await opts.micGate; if (opts.micError) throw new Error(opts.micError); frame = onFrame; return { stop: micStop, mute: micMute } as Mic; },
    mixer: () => ({ play: (ch, pcm, rate) => played.push({ ch, rate, bytes: pcm.byteLength }), reset: (ch) => reset.push(ch), close: mixerClose }),
  };
  const events = { states: [] as string[], msgs: [] as LiveMsg[], errors: [] as string[], mic: [] as boolean[] };
  const client = createLiveClient({ onState: (s) => events.states.push(s), onMsg: (m) => events.msgs.push(m), onError: (e) => events.errors.push(e), onMic: (m) => events.mic.push(m) }, deps);
  const say = (m: LiveMsg) => h!.onmessage(JSON.stringify(m));
  const json = () => sent.filter((x): x is string => typeof x === "string").map((x) => JSON.parse(x));
  return { client, deps, sent, json, say, bin: (b: ArrayBuffer) => h!.onmessage(b), open: () => h!.onopen(), onclose: () => h!.onclose(false), onerror: () => h!.onerror(), close, micStop, micMute, frame: (p: ArrayBuffer) => frame!(p),
    played, reset, mixerClose, events };
}
const JOINED: LiveMsg = { type: "joined", call_id: "c_1", owner: "ai", epoch: 1, owner_staff: null, listeners: 1, you_own: false, live: true };
const tagged = (tag: number, body: Uint8Array) => { const o = new Uint8Array(1 + body.length); o[0] = tag; o.set(body, 1); return o.buffer; };
const aiFrame = (turn: number, seq: number, pcm = [1, 0, 2, 0]) => { const b = new Uint8Array(8 + pcm.length); const v = new DataView(b.buffer); v.setUint32(0, turn, true); v.setUint32(4, seq, true); b.set(pcm, 8); return tagged(2, b); };

describe("liveWsUrl", () => {
  it("is on the API port with the call id, ws/wss by page protocol, and honours the voice override", () => {
    expect(liveWsUrl("c_1", { protocol: "http:", hostname: "localhost" }, "")).toBe("ws://localhost:8000/api/staff/calls/c_1/stream");
    expect(liveWsUrl("c_1", { protocol: "https:", hostname: "x.org" }, "")).toBe("wss://x.org:8000/api/staff/calls/c_1/stream");
    expect(liveWsUrl("c 1", { protocol: "http:", hostname: "x" }, "wss://api.example.com/api/voice/ws")).toBe("wss://api.example.com/api/staff/calls/c%201/stream");
  });
});

describe("live client: connection", () => {
  it("authenticates with the token as the FIRST message (browsers cannot send headers) and is joined only when the server says so", () => {
    const r = rig();
    r.client.connect("ws://t/stream", "tok");
    expect(r.client.state).toBe("connecting");
    r.open();
    expect(r.json()).toEqual([{ type: "auth", token: "tok" }]);
    r.say(JOINED);
    expect(r.client.state).toBe("joined");
    expect(r.client.epoch).toBe(1);
    expect(r.events.states).toEqual(["connecting", "joined"]);
  });

  it("a fatal server error, call_ended, or a socket close tears everything down", () => {
    for (const end of [(r: ReturnType<typeof rig>) => r.say({ type: "error", detail: "session expired", fatal: true }), (r: ReturnType<typeof rig>) => r.say({ type: "call_ended", reason: "x" }),
                       (r: ReturnType<typeof rig>) => r.onclose(), (r: ReturnType<typeof rig>) => r.onerror()]) {
      const r = rig();
      r.client.connect("u", "t"); r.open(); r.say(JOINED); r.played.length = 0;
      end(r);
      expect(r.client.state).toBe("closed");
      expect(r.close).toHaveBeenCalled();
      expect(r.mixerClose).not.toHaveBeenCalled();     // the mixer is created lazily: nothing was played, so nothing to close
    }
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED); r.bin(tagged(1, new Uint8Array([1, 0])));
    r.say({ type: "call_ended", reason: "ended_by_staff" });
    expect(r.mixerClose).toHaveBeenCalledTimes(1);
  });

  it("disconnect is idempotent and ignores everything the old socket says afterwards", () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    r.client.disconnect(); r.client.disconnect();
    expect(r.close).toHaveBeenCalledTimes(1);
    r.say({ type: "ownership", owner: "staff", epoch: 9, staff: "x" });
    expect(r.client.epoch).toBe(1);
    expect(r.client.state).toBe("closed");
  });

  it("a second connect while connected does nothing (no second socket)", () => {
    const r = rig(); let n = 0;
    const deps = { ...r.deps, socket: ((u, h) => { n++; return r.deps.socket(u, h); }) as LiveDeps["socket"] };
    const c = createLiveClient({}, deps);
    c.connect("u", "t"); c.connect("u", "t");
    expect(n).toBe(1);
  });
});

describe("live client: what the listener hears", () => {
  it("plays caller audio at 16 kHz and AI audio at the AI's rate, only for the response that is current", () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    r.bin(tagged(1, new Uint8Array([1, 0, 2, 0])));
    expect(r.played).toEqual([{ ch: "caller", rate: 16000, bytes: 4 }]);
    r.bin(aiFrame(3, 0));                                           // no ai_audio_start yet: not playing
    r.say({ type: "ai_audio_start", turn_id: 3, sample_rate: 22050 });
    r.bin(aiFrame(3, 0)); r.bin(aiFrame(2, 0));                     // a frame of another (older) response is dropped
    expect(r.played.filter((p) => p.ch === "ai")).toEqual([{ ch: "ai", rate: 22050, bytes: 4 }, ]);
    r.say({ type: "ai_audio_end", turn_id: 3, interrupted: true });
    expect(r.reset.at(-1)).toBe("ai");
    r.bin(tagged(3, new Uint8Array([1, 0])));
    expect(r.played.at(-1)).toEqual({ ch: "staff", rate: 16000, bytes: 2 });
  });

  it("a takeover cuts whatever the assistant had queued", () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    r.say({ type: "ownership", owner: "staff", epoch: 2, staff: "staff1" });
    expect(r.reset).toContain("ai");
  });
});

describe("live client: the staff microphone", () => {
  it("refuses to speak before joining; then opens the mic, announces the epoch, and tags every frame with the CURRENT epoch", async () => {
    const r = rig();
    await r.client.speak();
    expect(r.client.speaking).toBe(false);
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    r.say({ type: "ownership", owner: "staff", epoch: 2, staff: "staff1" });
    await r.client.speak();
    expect(r.client.speaking).toBe(true);
    expect(r.json().at(-1)).toEqual({ type: "mic_open", epoch: 2 });
    const pcm = new Int16Array([7, 8]).buffer;
    r.frame(pcm);
    let out = r.sent.at(-1) as ArrayBuffer;
    expect(new DataView(out).getUint32(0, true)).toBe(2);
    expect(Array.from(new Int16Array(out.slice(4)))).toEqual([7, 8]);
    r.say({ type: "ownership", owner: "staff", epoch: 5, staff: "staff1" });   // a newer epoch of the same takeover (e.g. after reconnection)
    r.frame(pcm);
    out = r.sent.at(-1) as ArrayBuffer;
    expect(new DataView(out).getUint32(0, true)).toBe(5);
  });

  it("the microphone is released the moment the call is no longer ours (no longer owner), and the server is told", async () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    r.say({ type: "ownership", owner: "staff", epoch: 2, staff: "s" });
    await r.client.speak();
    r.say({ type: "ownership", owner: "ai", epoch: 4, staff: null });
    expect(r.micStop).toHaveBeenCalledTimes(1);
    expect(r.client.speaking).toBe(false);
    expect(r.json().at(-1)).toEqual({ type: "mic_close" });
    expect(r.events.mic).toEqual([true, false]);
  });

  it("a rejected microphone is closed and reported", async () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    await r.client.speak();
    r.say({ type: "mic", state: "rejected", reason: "not_owner" });
    expect(r.micStop).toHaveBeenCalled();
    expect(r.events.errors.at(-1)).toMatch(/not_owner/);
  });

  it("a denied microphone permission is reported and nothing is announced to the server", async () => {
    const r = rig({ micError: "Permission denied" });
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    await r.client.speak();
    expect(r.events.errors).toEqual(["Permission denied"]);
    expect(r.json().some((m) => m.type === "mic_open")).toBe(false);
    expect(r.client.speaking).toBe(false);
  });

  it("disconnecting while the permission prompt is open releases the stream as soon as it is granted", async () => {
    let grant!: () => void;
    const r = rig({ micGate: new Promise<void>((res) => { grant = res; }) });
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    const p = r.client.speak();
    r.client.disconnect();
    grant(); await p;
    expect(r.micStop).toHaveBeenCalledTimes(1);
    expect(r.client.speaking).toBe(false);
  });

  it("stopSpeaking and disconnect release the microphone exactly once; mute passes through", async () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say(JOINED);
    await r.client.speak();
    r.client.mute(true);
    expect(r.micMute).toHaveBeenCalledWith(true);
    r.client.stopSpeaking(); r.client.disconnect();
    expect(r.micStop).toHaveBeenCalledTimes(1);
  });
});

describe("mixer", () => {
  function ctx() {
    const stopped: number[] = []; let id = 0;
    const c = { currentTime: 0, destination: {}, close: vi.fn(),
      createBuffer: (_n: number, len: number, rate: number) => ({ duration: len / rate, copyToChannel: () => undefined }),
      createBufferSource: () => { const me = id++; return { buffer: null as unknown, onended: null, connect: () => undefined, start: () => undefined, stop: () => { stopped.push(me); } }; } };
    return { c: c as unknown as AudioContext, stopped, raw: c };
  }
  const pcm = (n: number) => new Int16Array(n).fill(5).buffer;

  it("channels are independent: resetting the AI channel leaves the caller's audio playing", () => {
    const a = ctx(); const m = createMixer(() => a.c);
    m.play("caller", pcm(160), 16000); m.play("ai", pcm(2205), 22050); m.play("ai", pcm(2205), 22050);
    m.reset("ai");
    expect(a.stopped).toEqual([1, 2]);
  });
  it("close stops every source and closes the audio context", () => {
    const a = ctx(); const m = createMixer(() => a.c);
    m.play("caller", pcm(160), 16000); m.play("ai", pcm(160), 16000);
    m.close();
    expect(a.stopped).toHaveLength(2); expect(a.raw.close).toHaveBeenCalled();
  });
  it("ignores empty audio", () => {
    const a = ctx(); const m = createMixer(() => a.c);
    m.play("caller", new ArrayBuffer(0), 16000);
    expect(a.stopped).toEqual([]);
  });
});

describe("live client: the hand-over announcement (M4.1)", () => {
  const owned = () => { const r = rig(); r.client.connect("u", "t"); r.open(); r.say(JOINED); r.say({ type: "ownership", owner: "staff", epoch: 2, staff: "staff1" }); return r; };

  it("speak() while the announcement plays opens nothing, then opens the mic as soon as it finishes", async () => {
    const r = owned();
    r.say({ type: "announcement", state: "started" });
    expect(r.client.announcing).toBe(true);
    await r.client.speak();
    expect(r.client.speaking).toBe(false);
    expect(r.json().some((m) => m.type === "mic_open")).toBe(false);
    r.say({ type: "announcement", state: "finished" });
    await vi.waitFor(() => expect(r.client.speaking).toBe(true));
    expect(r.json().at(-1)).toEqual({ type: "mic_open", epoch: 2 });
    expect(r.client.announcing).toBe(false);
  });

  it("a failed announcement never blocks the staff member: the queued request goes through", async () => {
    const r = owned();
    r.say({ type: "announcement", state: "started" });
    await r.client.speak();
    r.say({ type: "announcement", state: "failed" });
    await vi.waitFor(() => expect(r.client.speaking).toBe(true));
  });

  it("joining while it is already playing is honoured, and a nothing-queued finish opens no microphone", async () => {
    const r = rig();
    r.client.connect("u", "t"); r.open(); r.say({ ...JOINED, announcing: true });
    expect(r.client.announcing).toBe(true);
    r.say({ type: "announcement", state: "finished" });
    expect(r.client.announcing).toBe(false);
    expect(r.client.speaking).toBe(false);
  });

  it("a mic refused with reason 'announcing' is not an error: it is retried when the announcement ends", async () => {
    const r = owned();
    await r.client.speak();                                                   // server says: too early
    r.say({ type: "mic", state: "rejected", reason: "announcing" });
    expect(r.events.errors).toEqual([]);
    expect(r.client.speaking).toBe(false);
    r.say({ type: "announcement", state: "finished" });
    await vi.waitFor(() => expect(r.client.speaking).toBe(true));
  });

  it("any other refusal is reported", async () => {
    const r = owned();
    await r.client.speak();
    r.say({ type: "mic", state: "rejected", reason: "not_owner" });
    expect(r.events.errors).toEqual(["The microphone was refused (not_owner)."]);
    expect(r.client.speaking).toBe(false);
  });

  it("disconnecting forgets a queued request (no microphone opens after a reconnect by surprise)", async () => {
    const r = owned();
    r.say({ type: "announcement", state: "started" });
    await r.client.speak();
    r.client.disconnect();
    r.client.connect("u", "t"); r.open(); r.say(JOINED); r.say({ type: "ownership", owner: "staff", epoch: 3, staff: "staff1" });
    r.say({ type: "announcement", state: "finished" });
    await new Promise((res) => setTimeout(res, 20));
    expect(r.client.speaking).toBe(false);
  });
});
