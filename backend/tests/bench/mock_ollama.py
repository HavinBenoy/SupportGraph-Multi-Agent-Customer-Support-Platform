"""A tiny stand-in for the Ollama HTTP API, so the benchmark plumbing is tested through the REAL langchain-ollama client.
It answers chat with a canned cited reply (or abstains for questions containing 'salary'), reports Ollama-style counters,
and returns deterministic 768-wide vectors. Not a model: it proves the plumbing, not the quality."""

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _vec(text: str) -> list[float]:
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        h = hashlib.sha256(w.encode()).digest()
        out.append((int.from_bytes(h[:2], "big") % 768, 1.0))
    v = [0.0] * 768
    for i, x in out:
        v[i] += x
    if not any(v):
        v[0] = 1.0
    return v


class _H(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json({"models": [{"name": "qwen3:4b", "digest": "abc123def4567890", "size": 2500000000, "details": {"family": "qwen3"}},
                                          {"name": "nomic-embed-text:latest", "digest": "fedcba0987654321", "size": 274000000}]})
        if self.path == "/api/version":
            return self._json({"version": "0.0.mock"})
        self._json({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        _H.calls.append((self.path, body))
        if self.path == "/api/generate":
            return self._json({"done": True})
        if self.path == "/api/embed":
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return self._json({"model": body["model"], "embeddings": [_vec(t) for t in inputs]})
        if self.path == "/api/chat":
            prompt = " ".join(m.get("content", "") for m in body["messages"])
            ids = re.findall(r'<source id="([^"]+)"', prompt)
            question = (re.findall(r"<question>\n(.*?)\n</question>", prompt, re.S) or [""])[-1]
            done_reason = "stop"
            if "ramble" in question.lower():  # never stops by itself: hits the token limit like a non-terminating model
                limit = (body.get("options") or {}).get("num_predict", 50)
                text = " ".join(["blah"] * limit)
                done_reason = "length"
            elif "salary" in question.lower() or not ids:
                text = "INSUFFICIENT_CONTEXT"
            else:
                text = f"Here is the answer [{ids[0]}]."
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for word in text.split(" "):
                self.wfile.write((json.dumps({"model": body["model"], "message": {"role": "assistant", "content": word + " "}, "done": False}) + "\n").encode())
            self.wfile.write((json.dumps({"model": body["model"], "message": {"role": "assistant", "content": ""}, "done": True,
                                          "done_reason": done_reason, "total_duration": 2_000_000_000, "load_duration": 500_000_000,
                                          "prompt_eval_count": 120, "prompt_eval_duration": 400_000_000,
                                          "eval_count": len(text.split()) if done_reason == "length" else 20, "eval_duration": 1_000_000_000}) + "\n").encode())
            return
        self._json({}, 404)


class MockOllama:
    def __enter__(self):
        _H.calls = []
        self.server = HTTPServer(("127.0.0.1", 0), _H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.calls = _H.calls
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
