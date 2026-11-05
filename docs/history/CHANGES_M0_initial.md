# CHANGES: Milestone 0 (benchmark kit and gateway hardening)

Scope is exactly what you authorised: the benchmark kit and the gateway improvements. **No real model is wired into the product yet and I have not started Milestone 1.** Default behaviour is unchanged (`SG_LLM_PROVIDER=fake`).

Extract over your project folder. The ZIP excludes `.env`, `infra/langfuse/.env`, `.venv`, `node_modules`, `.next`, caches and DB files, and does not touch them. No migration, nothing resets or deletes data. The previous CHANGES.md is kept at `docs/history/CHANGES_STEP6.md`.

## What I verified, and where

| Check | Result | Where |
|---|---|---|
| Fast suite (SQLite) | **631 passed, 28 skipped** (was 601 + 28; +30 new) | my sandbox |
| Postgres + pgvector suite | **659 passed** (was 629; +30 new) | my sandbox (Postgres 16) |
| Benchmark plumbing end to end (check / llm / embed / report) | works through the **real** `langchain-ollama` client against a mock Ollama server; counters, options (`num_ctx`, `num_predict`, thinking off) asserted | my sandbox |
| Real model speed and quality (qwen3:4b, qwen3:1.7b, nomic-embed-text) | **not measured: I cannot run Ollama here.** This is what you run | **your laptop** |
| STT (faster-whisper) and TTS (Piper) benchmarks | code and plumbing tested with injected engines only; never run against real engines or audio | **your laptop** (optional, voice milestone) |
| Frontend | untouched this milestone; not re-run | |

## What changed

**Gateway hardening** (`backend/app/llm/`, `config.py`)
- New settings: per-role models (`SG_LLM_MODEL_<ROLE>`), timeout, retries, output cap, `num_ctx`, `keep_alive`, thinking off, daily token budget, `SG_ALLOW_EXTERNAL_LLM`. All in `.env.example`.
- `SG_ALLOW_EXTERNAL_LLM` gate: a non-local chat or embedding endpoint is refused at model construction (so the API fails at startup, not on the first student message). Tests cover loopback, private ranges, `ollama`, public IPs and look-alike hostnames (`localhost.evil.com` is refused).
- Startup banner in the API log: provider, model, endpoint host, `local` / `EXTERNAL`, role overrides. Never prints keys.
- `ModelUnavailable`: FAQ answer generation and the action agent now go through `invoke_guarded` (budget check, token counting, any provider error normalised without echoing text). `knowledge` and `action` nodes turn it into the fixed `MODEL_UNAVAILABLE` reply instead of a 500. Tested at graph level.
- Default model in docs and `.env.example` changed from `qwen2.5:7b` to `qwen3:4b` (a documented default until your results decide).

**Benchmark kit** (`backend/app/bench/`, `backend/scripts/bench.py`, `evals/bench/` output)
- `make llm-smoke`: one chat call + one embedding call through the gateway, prints effective config.
- `make bench-check`: Ollama reachable? models pulled? prints the `ollama pull` lines you are missing.
- `make bench-llm`: per model: cold start (unloads first), FAQ answering on your synthetic QSpiders questions with fixed context (citations valid, expected facts present, correct abstention; deterministic checks, no LLM judge), time to first token, total time, tokens/s from Ollama's own counters, plus triage accuracy and unusable-output rate.
- `make bench-embed`: index time, query latency, hit@1 / hit@4 against `expected_docs`, and the score gap between answerable and must-abstain questions, which tells us whether `SG_RAG_MIN_SCORE` can be set by data.
- `make bench-stt` / `make bench-tts`: optional, for the voice milestone. Report only timings and word counts, never transcripts. You supply your own WAV files; they are not stored or uploaded.
- `make bench-report`: merges everything into `evals/bench/SUMMARY.md`. It records CPU model, cores, RAM and load average, but no hostname, username or file paths.
- No database, no student data, no network except the local Ollama URL (the same local-only gate applies).

**Small fixes**: `make eval ARGS="--reference evals/reports/reference_fake_v1.json"` now resolves repo-relative paths (your observation 7); `docs/STEP6.md` now says five remaining `npm audit` findings, matching this file's history.

**Docs**: ADR `docs/decisions/0005-local-first-models.md` (local-first, no silent fallback, external opt-in, Piper GPL-3.0-or-later implications with TTS kept replaceable), `docs/ROADMAP.md`, README model line.

## Run it on your laptop

```bash
cd ~/your-project && unzip -o supportgraph-m0.zip
.venv/bin/pip install -e "backend[dev,ollama,bench]"
make test                          # fast suite
make test-pg                       # needs `make db-up`
ollama pull qwen3:4b && ollama pull qwen3:1.7b && ollama pull nomic-embed-text
make bench-check                   # expect: "Ollama reachable ... missing:" lines only for models not yet pulled
SG_LLM_PROVIDER=ollama SG_EMBEDDING_PROVIDER=ollama make llm-smoke      # expect: chat ok ..., embedding ok ..., 768 dimensions
# close heavy apps, plug in the charger, then:
make bench-llm MODELS="qwen3:4b qwen3:1.7b" ARGS="--heldout"            # slow: expect tens of minutes on CPU; add ARGS="--limit 10" for a quick first pass
make bench-embed
make bench-report                  # prints the summary and writes evals/bench/SUMMARY.md
```
Optional voice numbers (only if you want them now): `pip install faster-whisper piper-tts`, record 5-10 short questions as 16-bit WAV, then `make bench-stt AUDIO=~/wavs MODEL=base.en` and `make bench-tts VOICE=/path/to/voice.onnx`.

**Please send back** `evals/bench/SUMMARY.md` (and the JSON files if you like). Those are what we use to choose the model together. If `bench-llm` shows `unusable output` near 100% for triage, that is itself a finding (the model cannot do structured output reliably), not a bug in the kit.

## Expected output (shape only; real numbers come from your machine)

`make test` ends with `... passed, 28 skipped` (631 passed in my sandbox; yours should match); `make test-pg` with `659 passed`. `bench-report` prints a Markdown table per suite (FAQ, triage, embeddings). In my mock run the tables rendered and the figures (20 tok/s etc.) were the mock's canned counters, **not model performance**.

## Honest limits

- All speed numbers anywhere in the docs or report are estimates until your run. Do not plan latency from them.
- Guard coverage and the no-per-ticket-cap limitation are listed in ADR 0005.
- Carried debt, unchanged: sessionStorage token, in-memory rate limiter, shared Postgres checkpointer connection (planned for M1), 5 `npm audit` findings (breaking upgrades), eval observation 8 unreproduced.

I will not start Milestone 1 until you have shared the results and we have reviewed the model choice.
