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
