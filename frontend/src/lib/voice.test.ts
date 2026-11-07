import { createPlayer, int16ToFloat32, parseAudioFrame, voiceWsUrl } from "./voice";

describe("voice helpers", () => {
  it("converts PCM16 to float samples in [-1, 1)", () => {
    const f = int16ToFloat32(new Int16Array([0, 16384, -32768, 32767]).buffer);
    expect(Array.from(f).map((x) => Math.round(x * 100) / 100)).toEqual([0, 0.5, -1, 1]);
  });
  it("ignores a trailing odd byte instead of throwing", () => {
    expect(int16ToFloat32(new Uint8Array([1, 0, 9]).buffer)).toHaveLength(1);
  });
  it("derives the socket URL from the page (ws for http, wss for https) and honours an override", () => {
    expect(voiceWsUrl({ protocol: "http:", hostname: "localhost" }, "")).toBe("ws://localhost:8000/api/voice/ws");
    expect(voiceWsUrl({ protocol: "https:", hostname: "support.example.com" }, "")).toBe("wss://support.example.com:8000/api/voice/ws");
    expect(voiceWsUrl({ protocol: "http:", hostname: "x" }, "wss://api.example.com/api/voice/ws")).toBe("wss://api.example.com/api/voice/ws");
  });
});

/** A fake Web Audio context that records what is scheduled, so overlap can be asserted without a browser. */
function fakeAudio() {
  const sources: { start: number; end: number; stopped: boolean; onended: (() => void) | null }[] = [];
  const ctx = {
    currentTime: 0,
    destination: {},
    createBuffer: (_c: number, len: number, rate: number) => ({ duration: len / rate, copyToChannel: () => undefined }),
    createBufferSource: () => {
      const s = { buffer: null as { duration: number } | null, onended: null as (() => void) | null, connect: () => undefined, stopped: false, start0: 0, end0: 0,
        start(at: number) { rec.start = at; rec.end = at + (s.buffer?.duration ?? 0); }, stop() { rec.stopped = true; } };
      const rec = { start: 0, end: 0, stopped: false, get onended() { return s.onended; }, set onended(v) { s.onended = v; } };
      sources.push(rec);
      return Object.assign(s, { get onended() { return rec.onended; }, set onended(v: (() => void) | null) { rec.onended = v; } });
    },
  };
  return { ctx: ctx as unknown as AudioContext, sources, clock: (t: number) => { ctx.currentTime = t; } };
}
const pcm = (seconds: number, rate = 22050) => new Int16Array(Math.round(seconds * rate)).fill(1000).buffer;

describe("parseAudioFrame", () => {
  it("reads turn id, sequence and the PCM payload", () => {
    const b = new Uint8Array(12); const v = new DataView(b.buffer); v.setUint32(0, 5, true); v.setUint32(4, 2, true); b.set([9, 0, 8, 0], 8);
    const f = parseAudioFrame(b.buffer)!;
    expect(f.turn).toBe(5); expect(f.seq).toBe(2); expect(Array.from(new Uint8Array(f.pcm))).toEqual([9, 0, 8, 0]);
  });
  it("rejects a buffer shorter than the header", () => { expect(parseAudioFrame(new ArrayBuffer(7))).toBeNull(); });
});

describe("player: one response at a time (M2.2)", () => {
  it("chunks of one response play back to back", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(1, 22050); p.push(1, pcm(1)); p.push(1, pcm(1));
    expect(a.sources[1].start).toBeCloseTo(a.sources[0].end, 5);
  });

  it("a second response stops the first one's scheduled audio instead of playing on top of it (the M2.2 root cause)", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(1, 22050); p.push(1, pcm(6)); p.end(1);                 // the server sent a whole 6 s answer at once
    a.clock(2);                                                     // 2 s into it, the next response arrives
    p.begin(2, 22050); p.push(2, pcm(3));
    expect(a.sources[0].stopped).toBe(true);                        // response 1 was cut: nothing of it is still scheduled
    const live = a.sources.filter((s) => !s.stopped);
    expect(live).toHaveLength(1); expect(live[0].start).toBeGreaterThanOrEqual(2);
  });

  it("ignores audio for a response that is not the current one", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(2, 22050); p.push(1, pcm(1)); p.push(3, pcm(1));
    expect(a.sources).toHaveLength(0);
  });

  it("stale chunks after a cancel are dropped, and a cancelled response can never start again", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(1, 22050); p.push(1, pcm(1));
    p.stop(1);
    p.push(1, pcm(1)); p.begin(1, 22050); p.push(1, pcm(1));        // late websocket events for the cancelled response
    expect(a.sources).toHaveLength(1); expect(a.sources[0].stopped).toBe(true); expect(p.playing).toBe(false); expect(p.current).toBeNull();
  });

  it("an older response cannot start after a newer one has", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(5, 22050); p.stop(5); p.begin(6, 22050); p.begin(4, 22050); p.push(4, pcm(1));
    expect(p.current).toBe(6); expect(a.sources).toHaveLength(0);
  });

  it("stopping a response that is not playing does not cut the one that is", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(2, 22050); p.push(2, pcm(1)); p.stop(1);
    expect(a.sources[0].stopped).toBe(false); expect(p.current).toBe(2);
  });

  it("reports when a response has played out naturally, and goes idle", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    const played: number[] = []; let idle = 0; p.onplayed = (t) => played.push(t); p.onidle = () => { idle++; };
    p.begin(1, 22050); p.push(1, pcm(1)); p.end(1);
    expect(played).toEqual([]); expect(p.playing).toBe(true);
    a.sources[0].onended?.();
    expect(played).toEqual([1]); expect(idle).toBe(1); expect(p.playing).toBe(false);
  });

  it("a cut response is not reported as played", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    const played: number[] = []; p.onplayed = (t) => played.push(t);
    p.begin(1, 22050); p.push(1, pcm(1)); p.stop(1); a.sources[0].onended?.();
    expect(played).toEqual([]);
  });
});

describe("player: a holding phrase and the answer of the same turn (M3)", () => {
  it("the answer queues after the holding phrase instead of on top of it, and is not cut", () => {
    const a = fakeAudio(); const p = createPlayer(() => a.ctx);
    p.begin(1, 22050); p.push(1, pcm(2)); p.end(1);                 // "One moment please." (2 s)
    a.clock(0.5);
    p.begin(1, 22050); p.push(1, pcm(3));                            // the real answer, same turn
    expect(a.sources[0].stopped).toBe(false);
    expect(a.sources[1].start).toBeGreaterThanOrEqual(a.sources[0].end - 1e-9);
  });
});

describe("player: first audible sound (M3)", () => {
  it("announces the first sound once per response, when it is really audible, and never for a response that was cut first", () => {
    vi.useFakeTimers();
    try {
      const a = fakeAudio(); const p = createPlayer(() => a.ctx); const started: number[] = [];
      p.onstarted = (t) => started.push(t);
      p.begin(1, 22050); p.push(1, pcm(1)); p.push(1, pcm(1));
      expect(started).toEqual([]);                                  // scheduled, not yet audible (the audio clock starts it 50 ms later)
      vi.advanceTimersByTime(100);
      expect(started).toEqual([1]);                                 // exactly once, however many chunks follow
      p.begin(2, 22050); p.push(2, pcm(1)); p.stop(2);              // cut before it became audible
      vi.advanceTimersByTime(500);
      expect(started).toEqual([1]);
      p.begin(3, 22050); p.push(3, pcm(1)); p.end(3);
      vi.advanceTimersByTime(500);
      expect(started).toEqual([1, 3]);
    } finally { vi.useRealTimers(); }
  });
  it("a holding phrase and the answer of the same turn each announce their own first sound", () => {
    vi.useFakeTimers();
    try {
      const a = fakeAudio(); const p = createPlayer(() => a.ctx); const started: number[] = [];
      p.onstarted = (t) => started.push(t);
      p.begin(4, 22050); p.push(4, pcm(0.5)); vi.advanceTimersByTime(100);
      p.begin(4, 22050); p.push(4, pcm(1)); vi.advanceTimersByTime(1000);   // the answer reopens the same turn
      expect(started).toEqual([4, 4]);
    } finally { vi.useRealTimers(); }
  });
});
