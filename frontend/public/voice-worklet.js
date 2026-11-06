// AudioWorklet: turns the microphone stream (any sample rate) into 16 kHz mono PCM16 frames of 640 samples (40 ms) for the server.
// Plain box-filter decimation is enough for speech recognition and VAD. No audio ever leaves the browser except over the voice WebSocket.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;      // `sampleRate` is the AudioContext's rate (44100 / 48000 typically)
    this.pos = 0;                         // fractional read position into the pending input
    this.pending = [];                    // input samples not yet consumed
    this.out = new Int16Array(640);
    this.n = 0;
    this.muted = false;
    this.port.onmessage = (e) => { if (e.data && e.data.type === "mute") this.muted = !!e.data.value; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) this.pending.push(ch[i]);
    while (this.pending.length >= Math.ceil(this.pos + this.ratio) + 1) {
      const start = Math.floor(this.pos), end = Math.floor(this.pos + this.ratio);
      let sum = 0, cnt = 0;
      for (let k = start; k <= end; k++) { sum += this.pending[k]; cnt++; }
      const v = this.muted ? 0 : Math.max(-1, Math.min(1, sum / cnt));
      this.out[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      this.pos += this.ratio;
      if (this.n === this.out.length) { const buf = this.out.slice(0).buffer; this.port.postMessage(buf, [buf]); this.n = 0; }
    }
    const drop = Math.floor(this.pos);
    if (drop > 0) { this.pending.splice(0, drop); this.pos -= drop; }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
