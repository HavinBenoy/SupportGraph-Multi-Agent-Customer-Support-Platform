# M4.1 + M5 - Safer human takeover and Trial Verification Mode (one change set)

Apply (from the project root): `unzip -o ../supportgraph-m4.1-m5-complete.zip`, then `make db-migrate` (migrations 0007 and 0008, additive). No new dependency, no model/embedding/RAG change, `.env` untouched, nothing deleted. Full write-up: **docs/M5.md** (and the updated docs/M4.md). The M4 section further down is kept as history.

## 1. M4.1 changes
- **Return to AI removed completely** (button, frontend action `returnCall`, `POST .../return`, hub method, state-machine path). A human-owned call stays human-owned until it ends, fails or the caller disconnects; an owner who stays away past the grace period ends the call as `failed` (`staff_unreachable`). `returning_to_ai` remains only as a legacy value so M4 rows stay valid.
- **Exclusive control** by the intervention owner (`calls.intervention_owner`, persisted, re-checked in the database for end, microphone and feedback). No silent transfer. An admin may take over an AI-owned call, never a human-owned one.
- **Hand-over announcement** ("I want to make sure you receive accurate information. One of our support specialists is joining us to clarify this. I'll hand over the conversation now."): only after ownership is granted, a system entry (never an AI message), never overlaps audio (the microphone waits; it opens even if the announcement fails, and the failure is audited).
- **Full chronological transcript** with timestamps (customer, AI, human staff spans, system), built from the existing conversation history and call events; tenant-isolated, staff/admin only; staff speech is not transcribed and nothing is invented.
- **Four status colours with words**: green AI handling, blue Human handling, grey Ended normally, red Ended after intervention (plus an amber Failed for a call that failed with no human involved). Listening is not taking over.
- **Post-call "Call intervention feedback"**: owner only, after the call; category, "What did the AI say or do that required your intervention?", optional correct information; no duplicates, submit/edit/skip; never written to the knowledge base.

## 2. M5 architecture
Versioned per-tenant trial settings + audit; per-call `TrialCall` (disclosure, consent, recorder); `RecordingProvider` abstraction (`none` default, `local` disk); idempotent retention job; counts-only metrics; admin Trial verification page. Diagram and file map: docs/M5.md B1.

## 3. Database migrations
- **0007 interventions**: `calls.intervention_owner` (nullable); tables `intervention_feedback`, `feedback_reviews`.
- **0008 trial_mode**: `calls.trial_mode` (default false); tables `trial_settings`, `trial_events`, `call_consents`, `call_recordings`.
Both purely additive; downgrades remove exactly what they add; tested up -> down -> up on PostgreSQL 16 and SQLite. Recording audio is never in the database.

## 4. API changes
- Removed: `POST /api/staff/calls/{id}/return`.
- Staff/admin: `GET .../calls/{id}/transcript`, `GET|POST|PUT .../calls/{id}/feedback`, `POST .../feedback/skip`, `GET /api/staff/interventions`, `GET /api/staff/trial` (read-only flag).
- Admin: `GET|PUT /api/admin/trial` (PUT needs `expected_version`), `GET .../events|metrics|feedback|retention|recordings`, `POST .../feedback/{call_id}/review`, `POST .../retention/purge` (dry-run), `POST .../recordings/{id}/hold`, `GET .../recordings/{id}/audio/{track}` (audited).
- Sockets: caller gets `trial_notice` / `consent` / `recording` and sends `consent{decision,policy_version}`; staff get `announcement` and `owner_disconnected`.

## 5. Frontend changes
Live Calls: no Return control, status badges with text, transcript panel, owner-only controls and banners, announcement/owner-disconnect messages, feedback form, "My intervention feedback" tab. Voice page: trial disclosure, explicit consent buttons (Allow recording / No thanks / Stop recording), recording indicator, storage-failure message. Admin console: Trial verification (switch, settings, metrics with date filter, feedback review, retention and recordings with legal hold). A role gate keeps it admin-only.

## 6. Call ownership and transcript changes
See 1. Ownership moves one way: `created -> active_ai -> staff_takeover -> ended|failed`. The transcript merges the LangGraph checkpoint thread with call events; limitations are returned in the response `notes` and shown on the page.

## 7. Intervention feedback workflow
Takeover creates an `awaiting` record for the owner; after the call ends the owner submits or skips (compare-and-set, one record per call), may edit until reviewed. Admin review: `pending_review -> reviewed -> approved_for_update | rejected` (also directly from pending, and `approved_for_update -> rejected`); no self-review; history in `feedback_reviews`. "Approved for knowledge update" is only a label for a later manual step: **nothing is inserted into the knowledge base or index.**

## 8. Consent and privacy controls
Trial disclosure on every trial call (text and voice). Recording is offered only if trial mode, "recording offered" and a healthy provider all hold; consent is a separate explicit button press tied to the policy version shown (changing notice, period or withdrawal rule makes a new version and refuses old answers); decline/ignore never changes the call; withdrawal stops recording at once and the admin-chosen policy deletes or keeps what was recorded. No claim of legal compliance is made.

## 9. Retention and purge
Recordings (default 30 d), consent records (365 d), reviewed/skipped feedback (365 d). Idempotent; legal holds respected; failures kept, counted and retried every run; tombstones remain; audit holds counts only; calls, tickets and knowledge documents are never deleted. Runs in-process every `SG_TRIAL_PURGE_INTERVAL_MIN` (60; 0 = off), from the admin page (with preview) and by `make purge-trial TENANT=... [PREVIEW=1]`.

## 10. Trial metrics
Date-filtered (UTC, default 30 days), trial calls only, counts and rates only (no names, ids, text or audio), explicit empty state, `n/a` for undefined rates, truncation flag. Calls, listening vs takeover, intervention rate, endings, escalations, durations, consent funnel, recordings, feedback reasons and review status.

## 11. Security measures
Admin-only routes and tenant from the token on every query; strict request models (`extra=forbid`, strict bool/int); versioned settings and compare-and-set feedback; consent accepted only from the caller's own connection and current policy version; database owner re-check for every human control; recording fails closed; files 0700/0600 with traversal checks; storage references never in responses or logs; audited, `no-store` audio download; counts-only audit details.

## 12. Tests actually executed (in this sandbox; fakes and synthetic audio only)
- Backend, SQLite, whole suite (`pytest -n 4`): **1586 passed, 41 skipped**.
- Backend with PostgreSQL 16 (`SG_TEST_DATABASE_URL=...`, whole suite, run serially): **1625 passed, 2 skipped**; includes `tests/db/test_postgres_m5.py` and the 0007/0008 up -> down -> up test.
- Frontend: `vitest run` **190 passed** in 9 files; `tsc --noEmit` clean; production `next build` succeeded (routes /, /admin, /agent, /login, /voice).
- `scripts/purge_trial_data.py` smoke-run on a scratch SQLite database (dry-run, exit 0).
- Scenarios A-E (trial off; trial on without recording; consent granted and recorded; declined/withdrawn; failures, retention and purge) in `tests/calls/test_m5_scenarios.py`.

## 13. Skipped tests and reasons
- SQLite run: 39 PostgreSQL-only tests (need `SG_TEST_DATABASE_URL`) and 2 real-Ollama tests (need `SG_TEST_OLLAMA=1`).
- PostgreSQL run: the same 2 real-Ollama tests.
- With numpy hidden the whole `tests/calls` directory is skipped by design (voice tests need numpy); not a failure.
- Not run at all: real microphone/speaker, real recording audio, real Ollama, real browser sessions (nothing in the sandbox can do these).

## 14. Migration instructions
`make db-migrate` (applies 0007, 0008). Do not reset or drop the database. To roll back: `cd backend && ../.venv/bin/alembic downgrade 0006` (removes only the M4.1/M5 additions; trial settings, feedback and recording metadata are lost, so export them first if you need them). Take a backup first (`pg_dump`) if you have data you care about.

## 15. Local environment configuration
Nothing is required: defaults keep trial mode off and recording unavailable. Optional keys (in `.env.example`): `SG_CALLS_HANDOVER_ANNOUNCEMENT`, `SG_CALLS_ANNOUNCEMENT_TIMEOUT_S`, `SG_TRIAL_RECORDING_PROVIDER` (`none`|`local`), `SG_TRIAL_RECORDINGS_DIR`, `SG_TRIAL_RECORDING_MAX_MB`, `SG_TRIAL_PURGE_INTERVAL_MIN`, `SG_TRIAL_METRICS_MAX_ROWS`. `data/recordings/` is added to `.gitignore`.

## 16. Exact local verification commands
```
make db-migrate
make test-fast                 # SQLite backend suite
make test-pg                   # needs make db-up; runs the PostgreSQL tests incl. migration 0007/0008
make test-trial                # M4.1 + M5 backend and frontend tests only
make frontend-test && make frontend-build
cd frontend && npx tsc --noEmit
make purge-trial TENANT=qspiders_sample PREVIEW=1
```
Then the browser checks in docs/M5.md part F.

## 17. Known limitations
Verified only with fake STT/TTS/VAD and synthetic audio. Recording supports the local-disk provider only, with no encryption at rest, no off-machine storage and no in-app playback. One API process (live state and the retention loop are in-process). Staff speech is not transcribed. Transcript completeness depends on the checkpoint history. The announcement is not mirrored to listeners. An admin cannot take over or end a human-owned call. Migrations 0001-0003 remain PostgreSQL-only, so the SQLite migration test starts from a schema at 0006.

## 18. Features requiring real-world validation
Real microphone/speaker behaviour of the announcement and the staff microphone gate (echo, timing); actual recorded audio (quality, size, three-track sync, playback); the consent and withdrawal experience with real callers; retention timing against a real clock and disk; what recording notice and retention periods your organisation and its lawyers need (this software makes no compliance claim); real Ollama latency during a trial call; browser behaviour on your machine.

---

# M4 - Call sessions, staff Live Calls, listening and human takeover (complete, one change set)

Apply (from the project root): `unzip -o ../supportgraph-m4-complete.zip`, then `make db-migrate` (migration 0006, additive). No new dependency, no model/embedding/voice-engine change, `.env` untouched, nothing deleted. Full write-up (state machine, protocols, failure table, manual test): **docs/M4.md**.

## 1. What was implemented
- **Call sessions:** every voice call is a durable `calls` row (unique id, tenant, ticket/caller association, status, audio owner, ownership epoch, timestamps for creation, connection, latest takeover, latest return, end) with an audit trail in `call_events`.
- **Backend lifecycle:** states `created, active_ai, staff_takeover, returning_to_ai, ended, failed`; every move is validated and applied as an atomic compare-and-set; idempotent repeats; one staff member per call; ended/failed calls can never resume.
- **Staff API:** list, get, listen, take over, return, end, events; staff/admin only; tenant-isolated; no ticket ids, no student data for plain staff.
- **Real-time audio:** a staff WebSocket per call (auth in the first message, Origin check, expiry and account re-check). Listening (caller + assistant + other staff audio, short transcript lines), takeover (assistant silenced at once; staff microphone to the caller; caller audio to staff only), return to AI (same session, same conversation), end (everything stopped, state persisted, sockets closed).
- **Ownership multiplexer:** audio is accepted only from the current owner; every ownership change has an epoch; stale or replayed audio is dropped.
- **Live Calls UI** inside the existing staff console: list with short id, status, start time, running duration, owner, caller reference, who is listening; Listen / Stop listening / Take over / Return to AI / End call driven by server state.
- **Events:** `created, connected, listen_started, listen_stopped, takeover_requested, takeover_succeeded, takeover_failed, returned_to_ai, ended, failed, participant_disconnected`, each with call id, tenant id, actor, timestamp and safe metadata.
- **M3.1 fix:** the nomic embedder is now loaded with `keep_alive` (as seconds), so Ollama stops unloading it after 5 idle minutes (your `ollama ps` showed only `qwen3:1.7b`). Note: the first version of this fix (a `"30m"` string) would have crashed the embedder, because `OllamaEmbeddings` takes an integer; the new test with the real `langchain_ollama` caught it before shipping.

## 2. Architecture and design decisions (reasons in docs/M4.md section 2)
Listening is not a state (only ownership is); `returning_to_ai` is a real state so nobody owns the audio during a hand-back; the database compare-and-set is the referee and a per-call lock orders operations in the process; ownership epochs reject stale audio; a grace period returns an abandoned takeover to the assistant; live state is in the one API process (leftover live rows are marked failed at startup); the voice line keeps working if the tables are missing; the assistant's time limit does not count staff time; event details accept only safe scalar keys; the API shows a ticket *reference*, never the ticket id.

## 3. Call lifecycle and ownership state machine
`created -> active_ai <-> staff_takeover (via returning_to_ai)`; any live state -> `ended` or `failed`. Table with owners and epochs: docs/M4.md section 3.

## 4. How listening works
REST `listen` validates; the staff socket authenticates and joins the call's listener set; each listener has its own bounded outbox; leaving never touches the call. docs/M4.md section 4.

## 5. How takeover works
Database compare-and-set, then ONE synchronous switch (owner, epoch, assistant suspended: speech cut, queues dropped, in-flight reply discarded), then the staff microphone (owner + connection + epoch + size + rate checked per message). docs/M4.md section 5.

## 6. How return-to-AI works
Holder or admin only; `staff_takeover -> returning_to_ai` (nobody owns audio), staff routing stopped, `returning_to_ai -> active_ai`, assistant resumed in the same session; nothing replayed; a failed hand-back fails the call safely. docs/M4.md section 6.

## 7. Failures and disconnects
Table of 17 situations (takeover mid-speech, mid-graph, two staff at once, staff tab closed/refresh/token expiry, no microphone, caller disconnect, handler cancelled, return write fails, restart ...): docs/M4.md section 7. During this work a real bug of this class was found and fixed: a cancelled socket handler could leave a dead staff connection attached (now removal and call-end persistence run as tasks that outlive the handler).

## 8. Security and privacy
Staff/admin only; tenant from the token; 404/"not found" across tenants; WebSocket auth in the first message + Origin + role + tenant-scoped lookup + expiry/account re-check + limits; epoch and connection checks on every microphone message; no audio or transcript stored or logged (tested); `ticket_ref` not ticket id; `student_id` admin-only; no call recording, no voice-based identification. docs/M4.md section 8.

## 9. Database changes
Migration `0006_calls`: new tables `calls` and `call_events` only (CHECKs, composite keys with `tenant_id`, indexes). Downgrade drops exactly those two. No earlier migration edited, no data touched. `make db-migrate`. If you forget, the voice line still works and the log tells you.

## 10. Dependencies and environment
No new dependency. Optional `.env` keys (all defaults, in `.env.example`): `SG_CALLS_TAKEOVER_GRACE_S`, `SG_CALLS_MAX_LISTENERS`, `SG_CALLS_LISTENER_QUEUE`, `SG_CALLS_RECHECK_S`, `SG_CALLS_KEEP_ENDED_MINUTES` (and `SG_CALLS_STAFF_FRAME_MAX`). New Make target: `make test-calls`.

## 11. Tests actually executed (sandbox: fake STT/TTS/VAD, synthetic audio, fake browser audio layer)
All run in the sandbox on the final code (Python 3.13, Postgres 16, vitest, tsc):
- Backend, SQLite, full suite (`pytest -n 4`): **1494 passed, 34 skipped, 0 failed** (the skips are Postgres-only tests when no `SG_TEST_DATABASE_URL`).
- Backend, M4 tests alone (`tests/calls`, `tests/voice`): 260 passed, repeated 3 times in a row with 0 failures (after fixing one flaky test, see section 7).
- Backend, Postgres 16 (`SG_TEST_DATABASE_URL=...`, `tests/db` + `tests/calls/test_calls_api.py`): **49 passed** (includes the 4 migration/CAS tests in `tests/db/test_postgres_m4.py`).
- Backend with numpy hidden (simulating a machine without `make voice-setup`): 1234 passed, 34 skipped; the voice/call tests are skipped cleanly, nothing else breaks.
- Frontend: vitest **109 passed** (8 files, incl. 16 in `live.test.ts`, 28 in `LiveCalls.test.tsx`, 4 new in `VoiceCall.test.tsx`); `tsc --noEmit` clean.
- New test counts: state/service 47, hub 37 (incl. a seeded fuzz of "the caller never hears AI and staff at once"), API/WebSocket 17 (incl. the 11-step end-to-end sequence with a log-leak check), Postgres 4, embedder keep-alive 1 file.
Not run: `next build`.

## 12. Tests NOT executable in the sandbox
Real microphone capture and speaker playback (echo, jitter, browser permission prompts, autoplay rules); Whisper/Piper/Silero/Ollama behaviour under a takeover; the 150 ms staff jitter buffer on a real network; a real browser driving the Live Calls tab (the component is tested with a fake audio client); `make test-pg` on your Postgres 16 (the sandbox Postgres 16 was used).

## 13. Exact local commands
```bash
cd ~/supportgraph            # your project root
unzip -o ../supportgraph-m4-complete.zip
make db-migrate              # 0006_calls
make test-calls              # fast, SQLite, no models
make test-fast               # whole backend suite
make test-pg                 # Postgres (needs make db-up)
make frontend-test           # vitest
cd frontend && npx tsc --noEmit
make api                     # terminal 1
make frontend-dev            # terminal 2
```

## 14. Manual browser test
docs/M4.md section 12 (13 steps: caller window + staff window, listen, take over mid-answer, speak both ways, mute, return and follow-up, staff tab closed -> back to the assistant after ~15 s, second staff refused, end, caller hang-up, events via curl).

## 15. Known limitations
Real audio is unverified here; one API process; no adaptive jitter buffer; the human part of a call is not transcribed or recorded, so the assistant does not know what staff said after a return; a reconnecting caller gets a new call record on the same ticket; staff transcript lines cover assistant-handled speech only; no M5 dashboard yet. Details: docs/M4.md section 13.

---

# M3 - Evidence-based voice latency (complete, one change set)

Apply (from the project root): `unzip -o ../supportgraph-m3-complete.zip`. No migration, no dependency, no model/embedding change, `.env` untouched. Every behaviour change has a regression test; everything that could cost accuracy is OFF by default. Full write-up, metric reference, configuration, caching rules, troubleshooting and the verification checklist: **docs/M3.md**.

## Findings (from your two real calls; details in docs/M3.md sections 1-2)
- The graph is 91-93% of the delay; STT is 5-9%; `endpoint_to_reply - turn_ms` equals the STT time, so nothing is hidden between the caller stopping and the graph starting. Node times explain all but 22-142 ms of `turn_ms` (graph/checkpointer overhead), now reported as `unaccounted_ms`.
- QA ~0 ms is correct: citation validation runs inside the knowledge node (now visible as `stage.validate`); the QA node is deterministic checks only.
- Cold start is NOT provably one thing from node timings. Found in the code: the old warm-up preloaded Ollama with no options while real calls send `num_ctx`, which makes Ollama start a new runner (a full reload). Fixed. Other candidates (model not loaded yet, embedding load, prompt-cache effects, lazy init) are now separable with the new Ollama counters. **Your repeat question benefited from llama.cpp's prompt cache, so 6.0 s is the best case, not the typical warm case.**

## Implemented
1. **Instrumentation:** per-run stage recorder (`triage_llm`, `rewrite`, `cache_check`, `embed`, `search`, `context`, `llm`, `llm_retry`, `validate`), `other` per node, `unaccounted_ms`, Ollama load/prompt-eval/decode counters per call, `cold` verdict with reasons. Numbers only, no text.
2. **Four voice latencies:** `endpoint_to_graph_done_ms`, `endpoint_to_first_audio_ms` (any audio), `endpoint_to_answer_audio_ms` (never the holding phrase), `endpoint_to_playback_done_ms`, plus the browser's first-audible ack (`endpoint_to_first_audible_ms`, `endpoint_to_answer_audible_ms`). New `metrics` message before each answer's `audio_end`.
3. **Model lifecycle:** complete, sequential, non-blocking, failure-tolerant warm-up (Ollama models with the real runner options, triage prime, embedding + DB prime, then voice models), status in `/api/voice/status` -> `warm_detail`.
4. **Triage cache** (failures never cached; routing/auth/floor/injection run after it) and an opt-in compact prompt / output cap.
5. **Validated-answer cache** for repeated anonymous FAQ questions (validated, cited, re-validated on every hit, invalidated by document fingerprint, QA and finalize still run).
6. **Holding-phrase audio cache** (fixed phrases only).
7. Opt-in `SG_RAG_CONTEXT_BAND`, `SG_RAG_MAX_CONTEXT_CHARS`, `SG_LLM_NUM_THREAD`.
8. **`make bench-voice`:** scenarios (cold first, warm repeat/varied, unsupported, human, greeting, long utterance, context sizes), stats (mean/median/P90/P95 with sample-size rules), cold vs warm groups, CPU/memory, before/after compare.
9. Evals (`app/evals/run.py`, `scripts/eval_routing.py`) run with the caches off.

## Deliberately not implemented (reasons in docs/M3.md section 4)
Parallel embedding with triage; keyword shortcuts for triage; streaming unvalidated tokens to TTS; concurrency or `OLLAMA_NUM_PARALLEL` changes in code; model/embedding/Whisper changes; caching for signed-in students; pre-rendering answers; lowering `num_ctx` by default.

## Files
New: `app/observability/stages.py`, `app/llm/warmup.py`, `app/rag/answer_cache.py`, `app/bench/latency.py`, `scripts/bench_voice_latency.py`, `docs/M3.md`, tests `tests/graph/test_m3_stages.py`, `tests/rag/test_m3_answer_cache.py`, `tests/rag/test_m3_context_trim.py`, `tests/unit/test_m3_triage_cache.py`, `tests/unit/test_m3_warmup.py`, `tests/voice/test_m3_latency_metrics.py`, `tests/voice/test_m3_ws.py`, `tests/bench/test_latency_bench.py`.
Changed: `app/config.py`, `app/graph/support.py`, `app/graph/subgraphs/knowledge.py`, `app/agents/triage.py`, `app/rag/faq.py`, `app/llm/gateway.py`, `app/llm/preflight.py`, `app/api/main.py`, `app/api/routes/voice.py`, `app/voice/session.py`, `app/evals/run.py`, `scripts/eval_routing.py`, `frontend/src/lib/voice.ts`, `frontend/src/components/VoiceCall.tsx` (+ their tests), `Makefile`, `.env.example`, `docs/ROADMAP.md`, `docs/TESTING.md`, `docs/M2.md`.

## Results (sandbox, fake models; NOT speed measurements)
SQLite 1390 passed / 30 skipped; PostgreSQL 1418 passed / 2 skipped (real-Ollama tests); frontend 60 passed, `tsc` clean; non-voice suites pass with numpy hidden (1019 / 2). `make bench-voice` ran end to end against the sandbox database (labelled FAKE). Cannot be verified here: real Ollama timings, the cold verdict against real load times, real browser audio-clock latency, CPU/RAM.

## Run locally
```
unzip -o ../supportgraph-m3-complete.zip
make test-fast && git diff --check
make test-pg
cd frontend && npm test && npx tsc --noEmit && cd ..
make e2e-local
make bench-voice ARGS="--no-cache --n 10 --label before"
make bench-voice ARGS="--cold"
```
Then restart the API, wait for `/api/voice/status` -> warm "ready", make a call, ask the fee question twice and a different question, and send the `metrics` blocks (docs/M3.md section 10).

---
# M3.0.1 - leak fix and per-node timings (from your first real timing)

Apply: `unzip -o ../supportgraph-m2-2-3.zip` (M2.2.2/M3.0 already applied). No migration, dependency, model or default-setting change.

## Reading your real numbers
`stt_ms 577, turn_ms 6125, endpoint_to_reply_ms 6703` for "what is the fee of the full-stack course" (2.75 s of audio, one segment, no merge):
- Speech recognition 0.6 s (about 0.2x real time) and the end-of-speech wait are NOT the problem. **The graph is 91% of the delay.**
- Inside the graph there are two model calls on your CPU: triage (about 1.4 s in your e2e runs) and the grounded FAQ answer (the rest, about 4-5 s: prompt processing of ~500 tokens plus ~25 tokens of output). Retrieval and the code steps take milliseconds. I cannot split this precisely from one block, so this release makes the split visible instead of guessing.
- The holding phrase should already have played at 1.8 s while waiting (the `audio_end` message of that turn shows `endpoint_to_filler_ms`; please tell me if you did not hear it).

## Fixes
1. **Leaked tasks ("Task was destroyed but it is pending ... Event.wait()" in your API log).** My paced-playback drain wait created two helper tasks per response that were never awaited. All waits now go through one helper that cancels and awaits its tasks; regression test checks no stray tasks remain after several paced responses. Harmless to the call but noisy and a slow leak, so it is fixed now.
2. **Per-node timings.** Every graph run records milliseconds per node (`triage`, `route`, `knowledge`, `qa`, `finalize`, ...; names and numbers only). They arrive in the voice `reply.timing.nodes`, in the `graph_done` event (`grep voice_event` in the API log) and as `node_ms` in the graph result. With one real turn we will know exactly how the 6.1 s splits.

## Next (M3.1, decided after your next timing block)
Candidates, each testable with the new breakdown and none changing models or weakening grounding: (a) smaller context window / fewer retrieved chunks for voice (`SG_LLM_NUM_CTX=2048`, `SG_RAG_TOP_K=3` are existing settings you can try now: change one, ask the same question three times, compare `nodes`), (b) a shorter FAQ prompt, (c) a validated-answer cache for repeated anonymous FAQ questions, (d) answering only after validation stays; text is not streamed unvalidated.
Please send: the `reply.timing` (with `nodes`) and the `audio_end.timing` for two questions (one warm, one repeated).

## Results (sandbox)
SQLite 1304 passed / 30 skipped; PostgreSQL 1332 passed / 2 skipped; frontend unchanged (57, `tsc` clean). Not testable here: real model timings.

## Run locally
```
unzip -o ../supportgraph-m2-2-3.zip
make test-fast && git diff --check && make test-pg
```
Then `make api`, make a call, and confirm the "Task was destroyed" lines are gone.

---
# M2.2.2 + M3.0 - Close M2, first safe latency slice

Apply: `unzip -o ../supportgraph-m2-2-2.zip` (M2.2.1 already applied). No migration, dependency or model change; `.env`, databases, volumes untouched.

## Analysis of your third real call (decision: close M2, start M3)
Good news, verified by the transcript: your speech now prints for every call, one answer per turn, no overlapping voices, "connect me to the support team" escalates, "Let's end the call" ends it, the model abstains instead of guessing (CEO salary, trainers: nothing in the sample docs).
The misses are all of two kinds, and neither is turn management:
| Heard | Meant | Kind |
|---|---|---|
| "am I old of them?" | am I audible | speech recognition (base.en); nothing to match |
| "please set the call", "let's send the conversation" | cut / end | recognition garbled the verb |
| "let's end this session", "cut the call and call them directly", "you may end the call. It's fine." | goodbye | phrase gaps (fixed) |
| "I'm not able to give a reliable answer to that here" | - | the router's honest fallback for unclassified text; correct behaviour |
**Decision:** more per-sentence patching has diminishing returns, so M2.2.2 replaces it with a general deterministic rule and M2 closes.

## M2.2.2 - call-ending rule (all in the shared graph)
A sentence made ONLY of call-ending vocabulary (please/let's/you can/the/this/call/chat/session/conversation/line + end/cut/stop/close/finish/drop/kill/disconnect...) is a goodbye. Recognition-garbled verbs (`set`, `send`, `get`, `cat`...) count only when the same utterance also says goodbye/thanks/"that's it". Any other word (fee, course, how, recording, tomorrow, center, link, ...) breaks the rule, so "stop the call recording", "send me the call link", "how do I end the call", "end the call and tell me the fee", "please set the call" alone are NOT goodbyes. Also: "am I audible / whether I'm audible or not / are we clear" are answered like "can you hear me" with no knowledge-base lookup.

## M3.0 - latency, the safe part (grounding untouched)
Where the time goes (docs/M2.md): end-of-speech wait + Whisper + the text path (4-8 s with the 1.7B model) + first sentence of speech. The answer text cannot be streamed to the caller before it has been validated (citations, grounding, abstention), so M3.0 does not stream unvalidated tokens. What it does:
1. **Measured baseline.** `timing.endpoint_to_first_audio_ms` (caller stopped talking -> first sound), `endpoint_to_reply_ms`, `endpoint_to_filler_ms`, `tts_first_ms`, `turn_ms`, `stt_ms` in every `reply`/`audio_end`, plus a `latency` event. Use these before and after any later change.
2. **Holding phrase.** If the answer is not ready after `SG_VOICE_FILLER_AFTER_MS` (default 1800; 0 = off) one fixed phrase ("One moment please." / "Let me check that for you." / "Just a moment.") is spoken, in the same turn, through the same one-at-a-time playback (it can be interrupted, is never spoken for a stale turn, is never spoken for instant replies such as greetings or goodbyes, and no model or caller text ever goes into it). The browser keeps "Thinking..." during it and queues the answer after it.
3. **Warm-up.** Opening the voice page (`GET /api/voice/status`) loads Whisper, Piper and Silero in a background thread and runs each once, so the first real question no longer pays model-loading time. Status now reports `warm`.
4. `SG_VOICE_STT_BEAM` (default 1, unchanged) lets you try 2-3 for better recognition of unusual words at some CPU cost; not changed by default and not claimed to help: you can test it on your own voice.
Not done (needs your measurements first): lowering the silence window, a faster model, speculative graph runs. Recognition model choice (base.en vs small.en) is a measurement for M3.1 with your numbers.

## Results (sandbox, fakes only)
SQLite 1301 passed / 30 skipped; PostgreSQL 1329 passed / 2 skipped; voice + intents with numpy hidden 186 passed; frontend 57 passed, `tsc` clean. New tests: call-ending groups and look-alikes (`test_intents.py`), three goodbyes over the real WebSocket, holding phrase (in order, none when quick/off/stale, cut by barge-in), latency fields, warm-up no-op, player re-open of the same turn, "Thinking" kept during the phrase.
Your M2.2.1 local run was fully green (SQLite 1270/31, PostgreSQL 1298/3, frontend 55, e2e 22/22).

## Run locally
```
unzip -o ../supportgraph-m2-2-2.zip
make test-fast && git diff --check && make test-pg
cd frontend && npm test && npx tsc --noEmit && cd .. && make e2e-local
```
Real call: open the voice page, wait ~10 s (warm-up), ask the fee question and read `timing` (browser dev tools > WebSocket > `reply`, or `grep voice_event` in the API log for `latency`). Expect a spoken "One moment please." if the answer takes over ~1.8 s. Please send me one `reply`/`audio_end` timing block and I will plan M3.1 from real numbers.

## Not verified
Real microphone/speaker timing, recognition quality, and real model warm-up time (no models in the sandbox).

---
# M2.2.1 - Final M2.2 fixes from your second real call (closes M2)

Apply: `unzip -o ../supportgraph-m2-2-1.zip` over the project (M2.2 already applied). No migration, dependency or model change; `.env`/databases untouched.

## What your transcript showed, and the cause of each
| Symptom | Cause | Fix |
|---|---|---|
| After the first call, your side of the conversation was not printed (you could not tell whether it heard you) | **My M2.2 bug.** Transcript lines were keyed by turn id, and turn ids restart at 1 on every call. Call 2's "turn 1" matched call 1's "turn 1" and rewrote that old line in place, so new speech never appeared (and the chat looked scrambled: replies without "You" lines, or the wrong sentence next to a reply) | line keys now include the call number; test: a second call never overwrites the first call's transcript |
| "Okay, thank you so much. Let me end this. Please cut the call." was answered with the fee | "Let me end this" was not a recognised goodbye, so the whole utterance went to the knowledge flow (and the old context rule treated "this" as a reference to the earlier fee question) | `let me / let's / I want to / we can ... end this / it / here / the call` are goodbyes (whole utterance only); a bare "this"/"it" no longer borrows the previous course, only a pronoun together with something being asked ("Does it have a certificate?") does |
| "Please cut the gore." -> "I couldn't find that" | speech recognition heard "call" as "gore" | after a command verb + "the", `gore/coal/caul/cull` count as the call word (whole utterance only: "cut the core", "what is the cold fee" are not goodbyes); the Whisper prompt now contains "Please end the call. Cut the call. Hang up. Goodbye." to bias recognition |
| "Do you get me and not understanding ...?" -> escalated to "management" | no rule for "do you understand me"; the model classified the complaint as a person-needed case | "do you get/understand/follow me", "are you getting/understanding me", "can you understand what I'm saying" are handled like "can you hear me" (no escalation). The long garbled sentence itself is still the model's call |
| Several "I couldn't find that in our help articles" replies in call 2 | consequence of the above: unrecognised or misheard command-like speech reaches the knowledge flow, which abstains correctly (no guessing) | covered by the fixes above; abstention itself is unchanged |
Note: because of the first bug, the pairing of "You" lines and replies in your pasted chat is not reliable; the replies themselves were produced correctly.

## Results (sandbox)
SQLite 1271 passed / 30 skipped; PostgreSQL 1299 passed / 2 skipped; frontend 55 passed, `tsc` clean. New tests: `test_intents.py` (+2 groups), `test_m22_context.py` (+2), `test_voice_ws.py` (+2 goodbyes over the real socket), `VoiceCall.test.tsx` (second call). Your local results for M2.2 (SQLite 1244/31, PostgreSQL 1272/3, frontend 54, e2e 22/22 with 0 hard failures) were already green.

## Run locally
```
unzip -o ../supportgraph-m2-2-1.zip
make test-fast && git diff --check && make test-pg
cd frontend && npm test && npx tsc --noEmit && cd .. && make e2e-local
```
Then one real call: end it, start a second call, and confirm your lines appear in the transcript; say "Let me end this, please cut the call".

## Still not covered
Real microphone/speaker timing and recognition quality (see docs/M2_2.md). If recognition mishears a command in some other way, say what you saw and I will add it, but phrase lists cannot cover every mishearing: STT quality is the limit.

---
# M2.2 - Voice turn management, utterance aggregation, sequential response playback

Apply: `unzip -o ../supportgraph-m2-2.zip` over the project (M2.1.1 already applied). No migration, no schema change, no new dependency, no model change. `.env`, databases and volumes untouched (new settings are optional, defaults below; `.env.example` lists them commented out).
Full write-up, root cause, protocol, observability and manual test steps: **`docs/M2_2.md`**.

## 1. Root cause (summary)
Two independent causes (plus four contributing defects); VAD was only one.
1. **Browser player overlap.** `createPlayer.start()` reset the clock and never stopped audio still scheduled from the previous response; the server streams far faster than real time, so a whole answer is already scheduled when the next one starts: two answers sound together. Reproduced with a fake AudioContext (R1 0.05-6.05 s, R2 2.05-5.05 s).
2. **Segments became independent turns.** Every Silero utterance (after 700 ms silence) was its own STT -> graph -> TTS turn. Reproduced: a 0.8 s pause gave 2 graph runs, 2 replies, 2 `audio_start`.
3. `speaking` ended when bytes were sent, not when they were heard. 4. The browser stopped playback on every raw `speech_start` (noise, echo). 5. A single shared cancel event, cleared per `_speak`. 6. No staleness check. 7. The knowledge branch saw only the last message, so follow-ups lost the course.

## 2. What changed
| Area | Change |
|---|---|
| `backend/app/voice/session.py` (rewritten) | `Turn` aggregation of segments with adaptive grace; one runner + queue + graph lock; unique turn ids; per-response cancel token; one `_speak` at a time (lock); paced audio and drain wait; browser `playback` ack; stale-reply logic (hand-offs/approvals never stale; ack/thanks do not supersede); barge-in via `interrupted{turn_id}`; framed audio; structured events and counters; safe `close()` |
| `backend/app/voice/turns.py` (new) | completeness (complete/short/incomplete), grace, `Turn` |
| `backend/app/voice/stt.py` | `FakeStt` accepts a function of the audio (tests) |
| `backend/app/api/routes/voice.py` | handles `playback`; passes `intent`/`context`; protocol docs |
| `backend/app/config.py` | `voice_silence_ms` 700 -> 600; new `voice_grace_complete_ms` 250, `voice_grace_short_ms` 700, `voice_grace_incomplete_ms` 1600, `voice_max_turn_s` 30, `voice_max_pending_turns` 3, `voice_pace_audio` true, `voice_pace_lead_ms` 1500 |
| `backend/app/conversation/context.py` (new) | deterministic follow-up / correction resolution (<= 5 added words, from the caller's own earlier question) |
| `backend/app/graph/{support,state}.py`, `nodes/common.py` | `resolved`, `context_kind` (reset per run); triage classifies and knowledge retrieves with the resolved text; injection, floor, RBAC, QA use the original text |
| `backend/app/conversation/intents.py`, `nodes/replies.py` | `affirm` intent; comma-separated pleasantries; "I think that's it"; "proceed to / feel free to ..."; trailing "it's fine/file"; "thanks for having me"; correction topic tails and "why did you mention that" |
| `frontend/src/lib/voice.ts`, `components/VoiceCall.tsx` | turn-aware player (`begin/push/end/stop(turn)`, late and older turns dropped, never two responses), `parseAudioFrame`, stop only on `interrupted{turn_id}`, playback ack, one transcript line per turn (updated in place), stale replies marked |
| `backend/tests/conftest.py` | `SG_VOICE_PACE_AUDIO=false` for tests (pacing tests switch it on) |
| docs | `docs/M2_2.md` (new), ROADMAP, TESTING, `docs/M2.md` protocol, `.env.example` |

## 3. Bugs found by the new tests while building (fixed)
- A sealed (maximum length) turn's timer was cancelled by the next turn: the turn was lost. Turns now keep their own settle task and commit in order.
- A hand-off (`escalated`) reply was marked not stale but `_speak` still skipped it when a newer question existed. `must` outcomes are now always spoken.

## 4. Tests added / sandbox results (actual runs in the sandbox; fakes only)
- `tests/voice/test_turns.py` (43): aggregation, grace, max length, silence, two questions, one graph run at a time, stale/hand-off/light follow-up, sequential playback, simultaneous responses, new turn during TTS, stale chunks after cancel, cancel cannot leak, barge-in by voice, cough ignored, no talking over the caller, pacing + ack + cut, disconnect during playback, timer cleanup, close during graph, events without words, bounded queue.
- `tests/graph/test_m22_context.py` (27): resolver and full graph (follow-up, correction, unrelated, no leakage, injection/escalation/RBAC unchanged).
- `tests/voice/test_voice_ws.py` +7 (real WebSocket route and graph), `tests/unit/test_intents.py` (+ M2.2 phrases and look-alike guards; the three M2.1 tests that listed yes/yeah/yes please as non-intents were updated: they are now `affirm`, a bare "no" is still not an intent), `tests/voice/test_session.py` / `test_voice_ws.py` updated for the partial+final `transcript`.
- Frontend: `voice.test.ts` (fake AudioContext) and `VoiceCall.test.tsx`.
- Results: **SQLite 1245 passed / 30 skipped; PostgreSQL 1273 passed / 2 skipped; no-numpy simulation of the voice/intent/context suites 173 passed; frontend vitest 54 passed, `tsc --noEmit` clean.**

## 5. Run locally
```
unzip -o ../supportgraph-m2-2.zip
make test-fast && git diff --check
make test-pg
cd frontend && npm test && npx tsc --noEmit && cd ..
make e2e-local            # unchanged scenarios; expect 22 messages, 0 hard failures
make api   # and make frontend-dev, open /voice; follow docs/M2_2.md section 9
```

## 6. Remaining limitations (honest)
- **No real microphone, speaker, echo or browser audio timing was tested.** Real Silero/Whisper/Piper were not run here (downloads blocked). The grace periods are tuned by reasoning, not by your voice; if sentences still split, raise `SG_VOICE_GRACE_INCOMPLETE_MS`.
- Completeness is heuristic (trailing words, openers, punctuation Whisper produces). A long pause after a finished-looking sentence ("What is the fee.") will be answered, and the next part becomes a follow-up; that is the intended trade-off against waiting too long.
- A complete sentence waits at most ~250 ms (less, since STT time counts); an unfinished one up to 1.6 s. This is not the M3 latency work.
- Context resolution only handles a named course/batch from the last three usable messages; anything else goes through unchanged.
- Observations deliberately not touched (model behaviour): the ungrounded "check your account settings" reply to "How do you know whether I'm a student?", and abstention on "Are you sure ... somebody is getting some discounts".
