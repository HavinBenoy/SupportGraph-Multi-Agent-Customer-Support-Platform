import { int16ToFloat32, voiceWsUrl } from "./voice";

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
