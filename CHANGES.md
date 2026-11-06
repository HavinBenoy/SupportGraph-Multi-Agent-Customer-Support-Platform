# M2 (v2: numpy now optional) - Browser voice line (simulated call) + M1b closure documentation

Apply on top of the M1b chain: `unzip -o ../supportgraph-m2.zip`. No migration, no schema change, `.env`, database and volumes untouched.
Only new dependency: the optional extra `voice` (faster-whisper, piper-tts, onnxruntime) plus `numpy`; install with `make voice-setup`.

## M1b closure (docs only)
`docs/ROADMAP.md` and `docs/M1B.md` mark M1b CLOSED 2026-11-06 with your verified results (SQLite 790/30, PostgreSQL 818/2, e2e 12/12, 0 hard failures).

## What M2 adds
Open `/voice`, press Start call, talk. Browser AudioWorklet (16 kHz PCM16) -> WebSocket `/api/voice/ws` -> Silero VAD endpointing -> faster-whisper -> **the same `run_turn` graph as the text chat** -> speakable reply -> Piper, streamed sentence by sentence. Barge-in, mute, typed fallback, transcript, escalation/approval banners. Full description: `docs/M2.md`.

Reused unchanged: FAQ with citation validation and abstention, triage/router, escalation floor, RBAC and own-records rules, make-up approval pause, QA, analytics, the text chat's rate limiter and signed tickets (a call and the text chat share one ticket).
Small refactor: `principal_from_token` extracted from `optional_principal` in `app/api/deps.py` (same behaviour, now shared).

## Files
Backend: `app/config.py` (voice settings), `app/api/deps.py`, `app/api/main.py`, `app/api/routes/voice.py`, `app/voice/{vad,stt,tts,text,session,smoke}.py`, `scripts/voice_smoke.py`, `pyproject.toml`, `tests/conftest.py`, `tests/voice/*`.
Frontend: `public/voice-worklet.js`, `src/lib/voice.ts(+test)`, `src/components/VoiceCall.tsx(+test)`, `src/components/Shell.tsx` (Voice link), `src/app/voice/page.tsx`, `src/app/globals.css`.
Repo: `Makefile` (`voice-setup`, `voice-smoke`), `.gitignore` (models/, evals/voice/), `.env.example`, `docs/{M2,ROADMAP,M1B,TESTING}.md`.

## Tests actually run (sandbox)
- Backend full suite, SQLite: 853 passed, 30 skipped. PostgreSQL 16: 881 passed, 2 skipped (providers exported as ollama to prove env independence). Offline eval regression gate passes.
- New: 63 voice tests (endpointer, spoken text, session/barge-in/limits, WebSocket conversations through the real graph with fake STT/TTS: FAQ+citation, shared ticket with text, escalation, anonymous refused own records, signed-in student own vs friend's records, make-up approval by staff, auth/origin/ticket attacks, shared rate limit); real Silero model on silence, noise and espeak speech.
- Frontend: 39 vitest tests, `tsc --noEmit` clean, `next build` OK. Real Chromium with a fake microphone through the real worklet and WebSocket into the real graph (scripted STT).
- NOT run (needs your machine): real Whisper transcription, real Piper audio, real speaker/mic behaviour (HF downloads are blocked here).

## Verify locally
```
unzip -o ../supportgraph-m2.zip
make test-fast && git diff --check          # (optionally make test-pg)
make voice-setup                            # downloads models; add the 2 printed lines to YOUR .env
make voice-smoke                            # real Piper -> Silero -> Whisper round trip, writes evals/voice/REPORT.md
make api    # + make frontend-dev, open http://localhost:3000/voice, allow the microphone, ask "What is the fee for the Python course?"
(cd frontend && npm test && npx tsc --noEmit)
```
Please send me `evals/voice/REPORT.md` and any call impressions (latency per turn is shown in the page log) to tune M3.

## v2 fix
Your venv had no numpy (the `numpy` core dependency only installs when you re-run `make venv`/`pip install -e backend[dev]`), so importing `app.api.main` failed. numpy is now imported lazily inside the voice modules: the API and the whole existing suite run without it (verified by blocking numpy: 790 passed / 30 skipped, identical to your M1b baseline); `tests/voice` is skipped when numpy is missing. Voice itself still needs `make voice-setup`.
Files in this v2 ZIP: `backend/app/voice/{stt,vad,smoke}.py`, `backend/tests/voice/conftest.py`, `CHANGES.md`.
