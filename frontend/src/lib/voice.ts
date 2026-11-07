/** Browser side of the voice line: the WebSocket client, microphone capture (AudioWorklet) and speech playback (Web Audio).
 *  The server does the hearing (VAD + speech recognition) and the speaking (Piper); the browser only captures, plays and shows.
 *  Protocol: see backend/app/api/routes/voice.py. Nothing here decides what a caller may see: the server applies the chat's own rules. */

export type ServerMsg =
  | { type: "ready"; ticket_id: string; signed_in: boolean; tenant_name: string; sample_rate: number }
  | { type: "speech_start"; turn_id?: number }
  | { type: "transcribing" | "thinking"; turn_id?: number }
  | { type: "interrupted"; turn_id?: number; reason?: string }
  | { type: "transcript"; turn_id?: number; text: string; final?: boolean; ignored?: boolean; segments?: number }
  | { type: "reply"; turn_id?: number; stale?: boolean; reply: string; status: string; citations: { id: string; heading: string }[]; escalation_id: string | null; ticket_id: string; end_call?: boolean; timing?: Timing; context?: string; intent?: string; turn?: { segments: number; audio_ms: number; merged: boolean; kind: string } }
  | { type: "audio_start"; turn_id?: number; sample_rate: number; filler?: boolean; staff?: boolean }
  | { type: "metrics"; turn_id: number; timing: Timing }   // M3: the four latencies of a spoken answer
  | { type: "audio_end"; turn_id?: number; interrupted?: boolean; filler?: boolean; staff?: boolean; timing?: Timing }
  | { type: "ownership"; owner: "staff" | "ai"; epoch: number }   // M4: a staff member took the call / handed it back to the assistant
  | { type: "call_end"; reason: string }   // the caller said goodbye: the closing was spoken, the server ends the call
  | { type: "notice"; detail: string }
  | { type: "error"; detail: string; fatal?: boolean };

export interface StartMsg { tenant?: string; token?: string | null; ticketId?: string | null }

export function voiceWsUrl(loc: Pick<Location, "protocol" | "hostname"> = window.location, override = process.env.NEXT_PUBLIC_VOICE_WS_URL): string {
  if (override) return override;
  // WebSockets cannot go through the Next.js /api proxy reliably, so the page talks to the API port directly (the server checks Origin).
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.hostname}:8000/api/voice/ws`;
}

export function int16ToFloat32(buf: ArrayBuffer): Float32Array {
  const even = buf.byteLength % 2 === 0 ? buf : buf.slice(0, buf.byteLength - 1);
  const i16 = new Int16Array(even);
  const out = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 32768;
  return out;
}

export interface Socket {
  send(data: string | ArrayBuffer): void;
  close(): void;
}
export type SocketFactory = (url: string, handlers: { onopen: () => void; onmessage: (m: string | ArrayBuffer) => void; onclose: (clean: boolean) => void; onerror: () => void }) => Socket;

export const browserSocket: SocketFactory = (url, h) => {
  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  ws.onopen = h.onopen;
  ws.onmessage = (e) => h.onmessage(e.data as string | ArrayBuffer);
  ws.onclose = (e) => h.onclose(e.wasClean);
  ws.onerror = h.onerror;
  return { send: (d) => ws.send(d), close: () => ws.close() };
};

/** Every binary audio message from the server: uint32-LE turn id, uint32-LE sequence number, then PCM16 (M2.2). */
export function parseAudioFrame(buf: ArrayBuffer): { turn: number; seq: number; pcm: ArrayBuffer } | null {
  if (buf.byteLength < 8) return null;
  const v = new DataView(buf);
  return { turn: v.getUint32(0, true), seq: v.getUint32(4, true), pcm: buf.slice(8) };
}

/** Timing numbers of a turn (M3): milliseconds, plus `cold` (boolean), `cold_reasons`, and `stages` / `nodes` (name -> ms). See docs/M3.md. */
export type Timing = Record<string, number | boolean | string[] | Record<string, number>>;

export interface Player {
  begin(turn: number, sampleRate: number, leadMs?: number): void;   // a new response: whatever else was playing is stopped first. leadMs: start-up buffer (a live human voice needs more than a prepared answer)
  push(turn: number, pcm16: ArrayBuffer): void;    // ignored unless `turn` is the response being played
  end(turn: number): void;                         // the server finished sending this response
  stop(turn?: number): void;                       // cancel that response (or the current one); late chunks of it are ignored for good
  readonly playing: boolean;
  readonly current: number | null;
  onidle?: () => void;                             // nothing is playing or queued any more
  onplayed?: (turn: number) => void;               // a response finished playing naturally (not when it was cut)
  onstarted?: (turn: number) => void;              // M3: the first sound of a response is audible now (once per begin())
}

/** Plays ONE response at a time with Web Audio. Chunks are scheduled on the audio clock (no gaps between sentences). Every chunk carries its
 *  turn: a chunk of a response that was stopped, replaced or never started is dropped, and a new response always stops the previous one's
 *  scheduled sources first, so two responses can never sound together. */
export function createPlayer(ctxFactory: () => AudioContext = () => new AudioContext()): Player {
  let ctx: AudioContext | null = null;
  let rate = 22050, nextAt = 0, cur: number | null = null, finished = true, latest = 0, announced = true;
  let active = new Set<AudioBufferSourceNode>();
  const retired = new Set<number>();
  const silence = () => { for (const s of active) { try { s.onended = null; s.stop(); } catch { /* already stopped */ } } active = new Set(); };
  const drained = (turn: number) => { cur = null; p.onplayed?.(turn); p.onidle?.(); };
  const p: Player = {
    get playing() { return active.size > 0 || (cur !== null && !finished); },
    get current() { return cur; },
    begin(turn, sampleRate, leadMs = 50) {
      if (retired.has(turn) || turn < latest) return;          // an old or cancelled response must never (re)start
      const reopen = cur === turn;
      if (cur !== null && cur !== turn) { silence(); retired.add(cur); }
      ctx ??= ctxFactory(); rate = sampleRate; latest = turn; cur = turn; finished = false;
      announced = false;
      nextAt = Math.max(reopen ? nextAt : 0, ctx.currentTime + leadMs / 1000);   // the same turn may speak again (a holding phrase, then the answer): queue after it
    },
    push(turn, pcm) {
      if (!ctx || turn !== cur || finished) return;
      const f32 = int16ToFloat32(pcm);
      if (f32.length === 0) return;
      const buf = ctx.createBuffer(1, f32.length, rate);
      buf.copyToChannel(f32 as unknown as Float32Array<ArrayBuffer>, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf; src.connect(ctx.destination);
      const at = Math.max(nextAt, ctx.currentTime);
      src.start(at); nextAt = at + buf.duration;
      active.add(src);
      if (!announced) {                                        // M3: tell the server when the first sound is really audible (the audio clock, not receipt)
        announced = true;
        const t = turn, wait = Math.max(0, (at - ctx.currentTime) * 1000);
        setTimeout(() => { if (cur === t || active.size > 0) p.onstarted?.(t); }, wait);
      }
      src.onended = () => { active.delete(src); if (active.size === 0 && finished && cur === turn) drained(turn); };
    },
    stop(turn) {
      if (turn !== undefined && turn !== cur) { retired.add(turn); return; }   // a response that is not playing: just make sure it never will
      const was = cur;
      silence();
      if (was !== null) retired.add(was);
      cur = null; finished = true;
      p.onidle?.();
    },
    end(turn) {
      if (turn !== cur) return;
      finished = true;
      if (active.size === 0) drained(turn);
    },
  };
  return p;
}

export interface Mic { stop(): void; mute(on: boolean): void }
export type MicFactory = (onFrame: (pcm: ArrayBuffer) => void) => Promise<Mic>;

export const browserMic: MicFactory = async (onFrame) => {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot use the microphone (it needs https or localhost).");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule("/voice-worklet.js");
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  node.port.onmessage = (e) => onFrame(e.data as ArrayBuffer);
  src.connect(node);  // not connected to the speakers: no feedback
  return {
    stop() { stream.getTracks().forEach((t) => t.stop()); node.disconnect(); src.disconnect(); void ctx.close(); },
    mute(on) { node.port.postMessage({ type: "mute", value: on }); },
  };
};
