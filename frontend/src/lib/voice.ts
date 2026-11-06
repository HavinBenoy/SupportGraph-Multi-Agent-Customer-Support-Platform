/** Browser side of the voice line: the WebSocket client, microphone capture (AudioWorklet) and speech playback (Web Audio).
 *  The server does the hearing (VAD + speech recognition) and the speaking (Piper); the browser only captures, plays and shows.
 *  Protocol: see backend/app/api/routes/voice.py. Nothing here decides what a caller may see: the server applies the chat's own rules. */

export type ServerMsg =
  | { type: "ready"; ticket_id: string; signed_in: boolean; tenant_name: string; sample_rate: number }
  | { type: "speech_start" | "transcribing" | "thinking" | "interrupted" }
  | { type: "transcript"; text: string; final?: boolean; ignored?: boolean }
  | { type: "reply"; reply: string; status: string; citations: { id: string; heading: string }[]; escalation_id: string | null; ticket_id: string; end_call?: boolean; timing?: Record<string, number> }
  | { type: "audio_start"; sample_rate: number }
  | { type: "audio_end"; interrupted?: boolean; timing?: Record<string, number> }
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

export interface Player {
  start(sampleRate: number): void;
  push(pcm16: ArrayBuffer): void;
  stop(): void;          // drop everything queued (barge-in / interruption)
  end(): void;           // the server finished sending
  readonly playing: boolean;
  onidle?: () => void;
}

/** Plays PCM16 chunks back to back with Web Audio. Chunks are scheduled on the audio clock so there are no gaps between sentences. */
export function createPlayer(ctxFactory: () => AudioContext = () => new AudioContext()): Player {
  let ctx: AudioContext | null = null;
  let rate = 22050, nextAt = 0, active = new Set<AudioBufferSourceNode>(), finished = true;
  const p: Player = {
    get playing() { return active.size > 0; },
    start(sampleRate) { ctx ??= ctxFactory(); rate = sampleRate; nextAt = ctx.currentTime + 0.05; finished = false; },
    push(pcm) {
      if (!ctx) return;
      const f32 = int16ToFloat32(pcm);
      const buf = ctx.createBuffer(1, f32.length, rate);
      buf.copyToChannel(f32 as unknown as Float32Array<ArrayBuffer>, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf; src.connect(ctx.destination);
      const at = Math.max(nextAt, ctx.currentTime);
      src.start(at); nextAt = at + buf.duration;
      active.add(src);
      src.onended = () => { active.delete(src); if (active.size === 0 && finished) p.onidle?.(); };
    },
    stop() { for (const s of active) { try { s.onended = null; s.stop(); } catch { /* already stopped */ } } active = new Set(); finished = true; p.onidle?.(); },
    end() { finished = true; if (active.size === 0) p.onidle?.(); },
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
