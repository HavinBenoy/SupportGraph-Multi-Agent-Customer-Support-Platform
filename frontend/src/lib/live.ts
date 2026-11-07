/** Browser side of staff live calls (M4): the staff audio WebSocket, playback of what is said on the call, and the staff microphone.
 *  The server decides who may listen or speak (authentication, tenant, ownership epoch); this file only moves audio and reports state.
 *  Protocol: see backend/app/api/routes/calls.py. Nothing is recorded or stored here: audio is played and dropped. */
import { browserMic, browserSocket, int16ToFloat32, parseAudioFrame, type Mic, type MicFactory, type Socket, type SocketFactory } from "./voice";

export type LiveMsg =
  | { type: "joined"; call_id: string; owner: "ai" | "staff" | "none"; epoch: number; owner_staff: string | null; listeners: number; you_own: boolean; live: boolean }
  | { type: "ownership"; owner: "ai" | "staff" | "none"; epoch: number; staff: string | null }
  | { type: "mic"; state: "open" | "closed" | "rejected"; reason?: string; epoch?: number }
  | { type: "transcript"; speaker: "caller" | "ai"; text: string }
  | { type: "ai_audio_start"; turn_id?: number; sample_rate: number }
  | { type: "ai_audio_end"; turn_id?: number; interrupted?: boolean }
  | { type: "call_ended"; reason: string }
  | { type: "pong" }
  | { type: "error"; detail: string; fatal?: boolean };

export type LiveState = "idle" | "connecting" | "joined" | "closed";
const TAG_CALLER = 1, TAG_AI = 2, TAG_STAFF = 3;
const CALLER_RATE = 16000;

export function liveWsUrl(callId: string, loc: Pick<Location, "protocol" | "hostname"> = window.location, override = process.env.NEXT_PUBLIC_VOICE_WS_URL): string {
  const base = override ? override.replace(/\/api\/voice\/ws$/, "") : `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.hostname}:8000`;
  return `${base}/api/staff/calls/${encodeURIComponent(callId)}/stream`;
}

/** Plays PCM16 on the audio clock, one queue per channel (caller / AI / other staff), so a listener hears both sides together. `reset` cuts a channel. */
export interface Mixer { play(channel: string, pcm: ArrayBuffer, rate: number): void; reset(channel: string): void; close(): void }
export function createMixer(ctxFactory: () => AudioContext = () => new AudioContext(), leadMs = 120): Mixer {
  let ctx: AudioContext | null = null;
  const next: Record<string, number> = {};
  const live: Record<string, Set<AudioBufferSourceNode>> = {};
  const cut = (ch: string) => { for (const s of live[ch] ?? []) { try { s.onended = null; s.stop(); } catch { /* already stopped */ } } live[ch] = new Set(); next[ch] = 0; };
  return {
    play(ch, pcm, rate) {
      const f32 = int16ToFloat32(pcm);
      if (!f32.length) return;
      ctx ??= ctxFactory();
      const buf = ctx.createBuffer(1, f32.length, rate);
      buf.copyToChannel(f32 as unknown as Float32Array<ArrayBuffer>, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf; src.connect(ctx.destination);
      const at = Math.max(next[ch] ?? 0, ctx.currentTime + ((next[ch] ?? 0) > ctx.currentTime ? 0 : leadMs / 1000));
      src.start(at); next[ch] = at + buf.duration;
      (live[ch] ??= new Set()).add(src);
      src.onended = () => live[ch]?.delete(src);
    },
    reset: cut,
    close() { for (const ch of Object.keys(live)) cut(ch); void ctx?.close(); ctx = null; },
  };
}

export interface LiveHandlers {
  onState?: (s: LiveState) => void;
  onMsg?: (m: LiveMsg) => void;
  onMic?: (speaking: boolean) => void;
  onError?: (detail: string) => void;
}
export interface LiveDeps { socket: SocketFactory; mic: MicFactory; mixer: () => Mixer }
export const realLiveDeps: LiveDeps = { socket: browserSocket, mic: browserMic, mixer: () => createMixer() };

export interface LiveClient {
  connect(url: string, token: string): void;
  speak(): Promise<void>;          // open the microphone (needs a user gesture) and tell the server; refused by the server unless you own the call
  stopSpeaking(): void;
  mute(on: boolean): void;
  disconnect(): void;              // closes the socket, the microphone and the audio context; safe to call repeatedly
  readonly state: LiveState;
  readonly speaking: boolean;
  readonly epoch: number;
}

export function createLiveClient(h: LiveHandlers = {}, deps: LiveDeps = realLiveDeps): LiveClient {
  let sock: Socket | null = null, mic: Mic | null = null, mixer: Mixer | null = null;
  let state: LiveState = "idle", epoch = 0, aiTurn = -1, aiRate = 22050, wantMic = false, gen = 0;
  const set = (s: LiveState) => { if (state !== s) { state = s; h.onState?.(s); } };
  const sendJson = (o: unknown) => { try { sock?.send(JSON.stringify(o)); } catch { /* socket closing */ } };
  const m = () => (mixer ??= deps.mixer());

  function closeMic() {
    const was = mic !== null;
    try { mic?.stop(); } catch { /* ignore */ }
    mic = null;
    if (was) { sendJson({ type: "mic_close" }); h.onMic?.(false); }
  }
  function teardown() {
    gen += 1; wantMic = false;
    closeMic();
    try { sock?.close(); } catch { /* ignore */ }
    sock = null;
    mixer?.close(); mixer = null;
  }

  function onText(raw: string) {
    let msg: LiveMsg;
    try { msg = JSON.parse(raw) as LiveMsg; } catch { return; }
    switch (msg.type) {
      case "joined": epoch = msg.epoch; set("joined"); break;
      case "ownership":
        epoch = msg.epoch;
        if (msg.owner !== "staff" && mic) closeMic();       // the call is no longer ours to speak on: release the microphone at once
        if (msg.owner !== "ai") m().reset("ai");            // staff took over: whatever the AI had queued is cut
        break;
      case "mic":
        if (msg.state === "rejected") { closeMic(); h.onError?.(`The microphone was refused (${msg.reason ?? "unknown"}).`); }
        break;
      case "ai_audio_start": aiTurn = msg.turn_id ?? -1; aiRate = msg.sample_rate; m().reset("ai"); break;
      case "ai_audio_end": if (msg.interrupted) m().reset("ai"); break;
      case "call_ended": teardown(); set("closed"); break;
      case "error": h.onError?.(msg.detail); if (msg.fatal) { teardown(); set("closed"); } break;
    }
    h.onMsg?.(msg);
  }
  function onBinary(buf: ArrayBuffer) {
    if (buf.byteLength < 2) return;
    const tag = new Uint8Array(buf, 0, 1)[0];
    const body = buf.slice(1);
    if (tag === TAG_CALLER) m().play("caller", body, CALLER_RATE);
    else if (tag === TAG_STAFF) m().play("staff", body, CALLER_RATE);
    else if (tag === TAG_AI) { const f = parseAudioFrame(body); if (f && f.turn === aiTurn) m().play("ai", f.pcm, aiRate); }   // frames of a cut response are ignored
  }

  return {
    get state() { return state; }, get speaking() { return mic !== null; }, get epoch() { return epoch; },
    connect(url, token) {
      if (sock) return;
      const my = ++gen;
      set("connecting");
      sock = deps.socket(url, {
        onopen: () => { if (my === gen) sendJson({ type: "auth", token }); },
        onmessage: (d) => { if (my !== gen) return; if (typeof d === "string") onText(d); else onBinary(d); },
        onclose: () => { if (my !== gen) return; teardown(); set("closed"); },
        onerror: () => { if (my !== gen) return; h.onError?.("Cannot reach the audio connection."); teardown(); set("closed"); },
      });
    },
    async speak() {
      if (mic || state !== "joined") return;
      wantMic = true;
      const my = gen;
      let started: Mic;
      try {
        started = await deps.mic((pcm) => {
          if (!sock || my !== gen) return;
          const out = new Uint8Array(4 + pcm.byteLength);
          new DataView(out.buffer).setUint32(0, epoch, true);        // the CURRENT epoch: the server drops frames of an ownership that has ended
          out.set(new Uint8Array(pcm), 4);
          try { sock.send(out.buffer); } catch { /* closing */ }
        });
      } catch (e) {
        wantMic = false;
        h.onError?.(e instanceof Error && e.message ? e.message : "The microphone is not available. Allow microphone access for this site.");
        return;
      }
      if (my !== gen || !wantMic) { started.stop(); return; }      // disconnected or cancelled while the permission prompt was open
      mic = started;
      sendJson({ type: "mic_open", epoch });
      h.onMic?.(true);
    },
    stopSpeaking() { wantMic = false; closeMic(); },
    mute(on) { mic?.mute(on); },
    disconnect() { teardown(); set(state === "idle" ? "idle" : "closed"); },
  };
}
