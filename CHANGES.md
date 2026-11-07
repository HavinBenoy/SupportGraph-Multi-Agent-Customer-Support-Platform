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
