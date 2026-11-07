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
