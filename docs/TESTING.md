# Test tiers and benchmark commands

Goal: the default loop is seconds, the full gate is minutes, and nothing hides a regression. `make test` and `make test-pg` are unchanged (same counts as before).
Timings below were measured on my 2-core sandbox; yours (4C/8T) should be faster with `-n auto`. They are not measured on your machine.

| Command | What it runs | Sandbox time | Does NOT cover |
|---|---|---|---|
| `make test-smoke` | security/tenant isolation, benchmark kit, FAQ validation + truncation, prompts, gateway hardening, router/triage/escalation-floor units (185 tests) | ~17 s | API, DB-backed flows beyond the FAQ tests, frontend, Postgres, real models |
| `make test-targeted` | only what your changed files need (`git status`), via `backend/scripts/test_select.py`; `DRY=1` previews, `FILES="..."` overrides | seconds to ~1 min | anything outside the rules; unknown backend files fall back to the FULL backend suite |
| `make test-fast` | whole SQLite backend suite, `-x --ff`, parallel (`-n auto`) when `pytest-xdist` is installed | ~48 s on 2 cores (76 s serial) | Postgres/pgvector, frontend, real models |
| `make test` / `make test-pg` | unchanged: full backend on SQLite / on real Postgres (+ pgvector) | ~76 s / ~85 s | frontend, real models |
| `make test-full` | `test-fast` + `test-pg` + frontend tests + production build | minutes | browser e2e (`make e2e`), real models |
| `make bench-quick` | real model, 8 tickets per split, no cold start, own folder `evals/bench-quick` | ~1-2 min for 1.7b | decisions: 8 tickets cannot separate models |
| `make bench-full` | real models, both splits, all tickets, cold start | ~6 min (1.7b); much longer for slow models | |
| `make bench-review DIR=bench-x` | writes `REVIEW.md`: failures the strict scorer cannot judge on meaning, for a human to read. Read-only, changes no score | seconds | |
| `make bench-experiment` | one controlled variant (generation settings only) in `evals/bench-<TAG>` | see MODEL_SELECTION.md | |

## Which tier when
- Editing code or prompts: `make test-targeted` (add `DRY=1` to see the plan first). For FAQ/triage prompt changes it also prints a reminder to run `make bench-quick`.
- Before commit: `make test-fast`. Before handing over / merging: `make test-full`.
- Database models or migrations: the selector adds `make test-pg`.
- Anything touching the model gateway or sampling: `make bench-quick`, then `make bench-full` only when you intend to compare models.

## Benchmark speed without hiding regressions
- `--no-cold-start` and `--suites` separate quality runs from latency runs. Cold start is measured in `bench-full` only.
- `keep_alive` is already 30 minutes, so the model is not reloaded between tickets.
- Model outputs are **never cached**: a cache would hide exactly the generation changes the benchmark exists to detect. Code-level tests use the fake/mock
  models (no model at all), which is why they are fast.
- No parallel model calls: one CPU-bound model at a time, otherwise latencies are meaningless.
- Quick and experiment runs use separate folders and the report's comparability warning (Ollama version, digests, settings, code hashes, hardware, dataset,
  prompts) fires if files from different conditions are put together.

## Adding `pytest-xdist`
`pip install -e "backend[dev]"` (it is now in the `dev` extra) or `pip install pytest-xdist`. Without it `make test-fast` simply runs serially.


## Real local stack (M1b)
- `tests/graph/test_local_stack_mock.py`: real langchain-ollama clients against a mock Ollama server (runs in `make test-fast`). Proves plumbing and safety, not model quality.
- `SG_TEST_OLLAMA=1 pytest tests/graph/test_real_ollama.py`: opt-in, real Ollama, safety assertions only.
- `make e2e-local`: 12 synthetic messages + retrieval Hit@k + memory/latency on your machine (`evals/e2e/REPORT.md`). Not part of any tier.

## Voice line (M2)
- `tests/voice/` runs in `make test-fast`: endpointer, spoken-text rules, session logic, real adapters against stand-ins, smoke logic, WebSocket conversations through the real graph with fake STT/TTS. `test_real_vad.py` uses the real Silero model and is skipped unless onnxruntime and faster-whisper are installed (`make voice-setup`); speech for it comes from espeak-ng when available.
- `frontend`: `npm test` (VoiceCall, voice helpers).
- `make voice-smoke`: real Piper/Silero/Whisper round trip on your machine (not part of any tier).

## Conversation control (M2.1)
- `tests/unit/test_intents.py`, `tests/unit/test_escalation_floor.py` (new human-request cases and look-alikes): pure code, fast.
- `tests/graph/test_m21_conversation.py`: the full support graph with a triager that RAISES if called, proving pleasantries and requests for a person need no model; plus unchanged FAQ, abstention, escalation, sign-in and approval behaviour.
- `tests/voice/test_voice_ws.py` (M2.1 block): the same words by voice and by text give the same replies; a goodbye ends the call after the closing is spoken.
- `make e2e-local` now has 22 messages (10 new, code-handled). Its make-up case accepts "already pending" only after confirming the pending request in the database (re-runs on the same database).

## Voice turn management (M2.2)
- `tests/voice/test_turns.py`: aggregation (pause inside a sentence, many frames, completed utterance, max length, silence, two questions, grace), one graph run at a time, stale replies, hand-offs never stale, sequential playback, simultaneous responses, new turn during TTS, barge-in, stale chunks after cancel, pacing, disconnect/cleanup, events carry no words.
- `tests/graph/test_m22_context.py`: follow-up, correction, unrelated question, no leakage, injection/escalation/RBAC unchanged, through the full graph.
- `tests/voice/test_voice_ws.py` (M2.2 block) and `tests/unit/test_intents.py` (M2.2 block): the same through the real WebSocket route; the real-call phrases.
- `frontend`: `voice.test.ts` (fake AudioContext: no overlap, stale turns dropped), `VoiceCall.test.tsx` (barge-in only on `interrupted`, merged transcript line, playback ack).
- Not covered by any test: real microphone/speaker timing. See docs/M2_2.md section 9.

## Latency (M3)
- `tests/graph/test_m3_stages.py`: recorder (attribution, `other`, reconciliation, cold verdict, Ollama counter parsing and callback), the FULL graph's stage report with no double counting and no question/answer text in it, the REAL langchain-ollama client against the mock server (counters, `num_thread`/`num_ctx`/triage cap reach the requests).
- `tests/rag/test_m3_answer_cache.py`: what may be stored (only validated cited answers), who may be served (anonymous only), keys, TTL/LRU, tampering, invalidation by document and passage changes, QA/finalize still run on a hit, abstentions/injection/escalation/greetings never touch it, injected knowledge graph and failures fall back.
- `tests/unit/test_m3_triage_cache.py`: failures never cached, expiry/bounds, compact prompt keeps every rule, routing/auth/floor/injection after the cache.
- `tests/unit/test_m3_warmup.py`: preload options equal the real call's, sequential steps, once per process, failure tolerance, non-blocking, voice warm-up waits for the LLM warm-up.
- `tests/voice/test_m3_latency_metrics.py`, `tests/voice/test_m3_ws.py`: the four latencies and their order, the `metrics` message before `audio_end`, browser acks (late/foreign/duplicate ignored), holding-phrase audio cache (fresh answers, cut phrase never stored), the socket protocol and `warm_detail`.
- `tests/rag/test_m3_context_trim.py`, `tests/bench/test_latency_bench.py`: opt-in prompt trimming never makes an answer ungrounded; benchmark statistics, cold/warm grouping, comparison and CLI.
- `frontend`: `voice.test.ts` (first-audible announce once per response), `VoiceCall.test.tsx` (the `started` ack carries the filler flag).
- Not covered by any test: real Ollama timings, the `cold` verdict against real load times, real browser audio-clock latency, CPU/RAM. `make bench-voice` and docs/M3.md section 10.
