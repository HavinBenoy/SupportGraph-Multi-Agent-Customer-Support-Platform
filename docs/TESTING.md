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

## Live calls (M4)
- `tests/calls/test_state_service.py`: the exact transition table, every (from, to) pair (allowed ones move, the rest are refused and change nothing), compare-and-set from two stale views (one wins), epoch/timestamp stamping, terminal states final, events written with the move (none for a refused move), event details restricted to safe scalar keys, tenant isolation, list window, startup sweep.
- `tests/calls/test_hub.py`: the hub over a REAL `VoiceSession` with fake caller sockets: lifecycle, listening (hears both sides, call unaffected, multiple/limit/slow listener), takeover (atomic, idempotent, one winner of a race, during speech, during a graph run, queued turns dropped), staff microphone rules (owner, epoch, connection, size, rate), return (same session and context, no replay, only holder/admin, fail-safe on a failed hand-back), end (everything stops, persisted, sockets closed, later work refused), disconnects (staff, caller, grace period, refresh, no microphone), failures (assistant turn raises, dead caller socket, event write fails) and a seeded **fuzz of random operations asserting that no AI frame is sent while staff own the call and no staff frame while the AI does**.
- `tests/calls/test_calls_api.py`: real FastAPI app + sockets: 401/403/404 for every route, tenant isolation (REST and socket), no ticket id / student id leak, REST lifecycle and events, socket auth (missing/bad/student/expired), Origin, expiry, disabled account, the full 11-step sequence (listen, takeover, staff speaks, caller heard, return, assistant answers in the same conversation, end) with a log-leak check, call-to-call audio isolation, voice line without the tables, startup sweep.
- `tests/db/test_postgres_m4.py` (Postgres): migration 0006 up/down with data, constraints, 8 concurrent takeovers (exactly one wins), concurrent end vs takeover.
- `tests/unit/test_m4_embedder_keepalive.py`: the embedder sends `keep_alive` as integer seconds (real `langchain_ollama` against the mock server).
- `frontend`: `live.test.ts` (auth first, epoch-tagged microphone frames, mic released when ownership leaves, teardown, mixer channels), `LiveCalls.test.tsx` (controls follow the server state for 7 situations, duplicate clicks, confirm-before-end, error and refusal display, polling, cleanup on unmount), `VoiceCall.test.tsx` (caller-side takeover messages), `api.test.ts`.
- Not covered by any test: real microphones/speakers, echo, jitter, browser permission prompts, real Ollama. `docs/M4.md` section 12 is the manual check.

## M4.1 + M5 (trial verification, intervention workflow)
Run: `make test-trial` (calls + migration tests + the trial frontend tests), `make test-fast`, `make test-pg`, `make frontend-test`, `make frontend-build`.
- `tests/calls/test_state_service.py`, `test_hub.py`, `test_calls_api.py`: updated for one-way ownership: nothing leads to `active_ai` from `staff_takeover`, no `/return` route (404/405), owner-only end and microphone, owner-disconnect grace -> `failed` (never AI, never another staff), simultaneous takeover (one winner), announcement gates (mic refused while announcing, opens even if the announcement fails, played only after ownership is granted).
- `tests/calls/test_m41.py`: transcript (order, timestamps, system vs AI, staff spans, no invented text, tenant isolation, staff/admin only), feedback (owner-only, after the call only, no duplicates, skip/edit/submit, review flow, no self-review, history, the knowledge base untouched).
- `tests/calls/test_m5_core.py`, `test_m5_api.py`, `test_m5_scenarios.py`: settings (versioned compare-and-set, strict types, policy-version bumps), consent state machine, providers (null, local permissions and path safety, fail-closed), retention (idempotent, retried failures, legal hold, tombstones, counts-only audit), metrics (date filter, empty state, no PII), admin routes, persistence across app restarts, and the end-to-end scenarios A-E with failure cases.
- `tests/db/test_migrations_m4_1_m5.py` (SQLite) and `tests/db/test_postgres_m5.py` (PostgreSQL 16): migrations 0007/0008 up -> down -> up. The SQLite test starts from a schema built at revision 0006 and `stamp`s it, because migrations 0001-0003 use PostgreSQL-only DDL and have never run on SQLite.
- API/scenario tests use a **file-backed SQLite** fixture (`tests/calls/apidb.py`): the shared in-memory SQLite connection cannot serve a WebSocket thread and REST threads at once. The scheduled purge loop is off in tests (`trial_purge_interval_min=0`).
- `frontend`: `LiveCalls.test.tsx` (no Return control in any state, four colours and labels, listening is not taking over, transcript, owner-only control, announcement and owner-disconnect banners, feedback form states, My intervention feedback), `TrialAdmin.test.tsx` (switch with `expected_version`, 409 reload, provider none, metrics empty/data/filter, review and no self-review, retention preview/purge/hold, errors, admin-only), `VoiceCall.test.tsx` (disclosure, consent buttons, `policy_version`, decline, withdrawal, storage failure), `live.test.ts` (announcement gating), `api.test.ts` (new endpoints and bodies).
- Without numpy the whole `tests/calls` directory is skipped by design (the voice tests need it); install numpy to run them.
- Not covered by any test: real microphones/speakers, real recording audio quality and playback, real Ollama, a real browser run.

## M6 (knowledge store, versioning, audit)
Run: `make test-kb` (knowledge + migration tests on SQLite and the Knowledge frontend tests), `make test-kb-pg` (the PostgreSQL 16 tests), then `make test-fast`, `make test-pg`, `make frontend-test`, `make frontend-build`.
- `tests/knowledge/test_store_api.py`: create/read, duplicate id (409), same text under another id (warning only), validation matrix (ids, categories, sizes, front matter, control characters, filename traversal, MIME), listing/filter/search/pagination (wildcards are searched literally), metadata update with `expected_row_version`, tenant isolation on every route, the authorization matrix (anonymous 401, student/staff 403 on every admin route; staff read published content only).
- `tests/knowledge/test_versions.py`: numbering, history, compare, restore-as-new-version (v1 -> v2 -> v3 -> restore v1 = v4, all four intact), identical-content refusal, stale `expected_row_version`, six concurrent writers (distinct consecutive numbers), ORM immutability (every immutable column, delete), no HTTP route can edit or delete a version.
- `tests/knowledge/test_publication_rag.py`: drafts and processed-but-unpublished versions are invisible to the real retrieval function, publish makes a version retrievable with doc/version ids and the cache fingerprint changes, superseding, restore-then-publish, failed embedding keeps the old version live and is recoverable, a publish that fails midway rolls back completely, idempotent processing, embedding-model change blocks publish, unpublish/archive/unarchive (history kept, nothing republished silently), tenant scoping, the public chat citing a store document, concurrent publishes (one live version), disk ingest never overwrites or removes store-managed documents, `import-disk` (idempotent, chunk counts unchanged), stale processing take-over.
- `tests/knowledge/test_feedback_proposals.py`: only approved feedback can start a proposal, a proposal creates a draft that changes nothing customers can see, publishing is blocked until a human approves, rejected proposals can never be published, proposer/reviewer/publisher recorded, the feedback row is byte-for-byte unchanged, one proposal per feedback (also under concurrency), optional separate reviewer, other tenants' feedback is invisible.
- `tests/knowledge/test_audit.py`: every action audited with its actor, no document/feedback/secret text ever stored (sentinel test over the API output and the raw table), unknown keys dropped, append-only (ORM guard and 405), tenant scoped, filters and cursor pagination, failed requests leave no event.
- `tests/knowledge/fencing.py` + `test_processing_fencing.py` (SQLite) and the same scenarios in `tests/db/test_postgres_m6.py` (PostgreSQL 16): processing ownership. Gated fake embedders and an aged claim, no sleeps: old worker finishing/failing after takeover (while the new worker is still embedding, and after it completed), current-worker failure and retry, six concurrent takeovers of one stale claim (one owner), stale worker after publish, archive during processing, retry without duplicates.
- `tests/db/test_migrations_m6.py` (SQLite, from a schema at 0008; includes 0010) and `tests/db/test_postgres_m6.py` (PostgreSQL 16): 0009 up -> down -> up with existing documents, chunks and calls untouched, constraints enforced; on PostgreSQL also real concurrent versions, publishes, processing and proposals, vector staging, and a publish rollback.
- `frontend/src/components/KnowledgeAdmin.test.tsx`: list and status, process/publish buttons appear only in the right lifecycle state and nothing publishes by itself, server refusals shown in words, new version sends the loaded `expected_row_version`, restore creates a new version, archived and published documents offer the right actions only, feedback -> draft -> approve without any publish call.
- Not covered by any test: a real browser session on the Knowledge tab, real `nomic-embed-text` embeddings through the store (tests use the offline hashing embedder; the store calls the same embedder as retrieval, so real vectors take the same path), very large documents near the limits, and PostgreSQL behaviour with more than the six/five writers used in the race tests.


## Part 1 (Knowledge Workspace)
Run: `make test-kw` (backend knowledge + migration tests on SQLite, and the workspace frontend tests), `make test-kw-pg` (migration 0011 and concurrency on PostgreSQL 16), then `make test-fast`, `make test-pg`, `make frontend-test`, `make frontend-build`.

| File | Covers |
|---|---|
| backend/tests/knowledge/test_diffing.py | the diff engine: equal+delete reproduces the original, equal+insert the proposal, unicode, CRLF, 4000-line document |
| backend/tests/knowledge/test_workspace_folders.py | folders, hierarchy, depth, duplicates, rename CAS, moving documents (content/versions/live RAG untouched), listing filters, staff tree and search (no draft leak), published history, RBAC matrix, tenant isolation, concurrent folder creation, audit, disk-import compatibility |
| backend/tests/knowledge/test_change_requests.py | start/resume, structured diff storage, draft persistence, multi-document requests, concurrent edit conflicts (token, parallel saves), submit rules, stale base, cancel, admin review, apply (draft versions only, all-or-nothing), audit without text leakage, isolation, M6 restore/unpublish compatibility |
| backend/tests/db/test_postgres_workspace.py | 0011 up/down/up with data kept, DB constraints, racing folder creators/moves/saves/submits/applies on PostgreSQL |
| frontend/src/components/workspace/KnowledgeWorkspace.test.tsx | browsing, breadcrumbs, read-only reader, search, history/compare, editor (review diff, save, 409, submit, cancel, resume), admin folders/move/review/apply, DiffView escaping |
