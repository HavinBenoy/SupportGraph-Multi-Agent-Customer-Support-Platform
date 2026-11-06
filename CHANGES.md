# M2.1 - Conversational polish and call intent handling

Apply: `unzip -o ../supportgraph-m2-1.zip` over the project (M2 and M2 v2 already applied). No migration, no schema change, no dependency, no model change,
`.env`, database and volumes untouched. Details and design: `docs/M2_1.md`.

## What was wrong (from your real call) and what changed
| Observed | Cause | Fix |
|---|---|---|
| "Hi" got a robotic/odd reply | every message went to triage -> clarify wording | `converse` branch: short warm greeting that asks how to help (3 variants) |
| "Okay, thank you. That's it. You can stop the call." got a KB abstention | no notion of call control | `goodbye` intent: short closing, `end_call`; voice speaks it, then hangs up gracefully (`call_end`) |
| "Please forward this call towards your supporting" got an unrelated FAQ | floor pattern too narrow for spoken wording | widened `human_request` floor rule (+ no model call); escalates through the existing escalation workflow with honest wording |
| "I am just testing" got an abstention | everything treated as an FAQ question | `interruption` / `acknowledgement` / `thanks` intents with fixed replies |

All of it lives in the shared support graph, so text and voice behave identically. Safeguards are checked BEFORE a pleasantry is accepted: injection screen, escalation floor.
An utterance is a pleasantry only if every sentence of it is one; "Thanks, what is the fee?" still goes through the normal flow. "yes"/"no" and a bare "stop" are never intents.

## Files
New: `backend/app/conversation/{__init__,intents}.py`, `backend/tests/unit/test_intents.py`, `backend/tests/graph/test_m21_conversation.py`, `docs/M2_1.md`.
Changed: `backend/app/graph/support.py` (triage short-circuit, `converse` node/route), `backend/app/graph/nodes/{router,replies,common,escalate}.py`, `backend/app/graph/state.py`, `backend/app/graph/review.py` (goodbye while approval pending),
`backend/app/guardrails/escalation_floor.py`, `backend/app/api/routes/voice.py`, `backend/app/voice/session.py` (`call_end`, `on_end`), `backend/app/evals/outcomes.py`, `backend/scripts/e2e_local.py` (+5 code-handled cases),
`evals/datasets/tickets_qspiders_v1.jsonl` (T052) and `tickets_qspiders_v2_heldout.jsonl` (T144) relabelled `clarify` -> `converse` (reference NOT refreshed, gate passes),
tests: `tests/unit/test_escalation_floor.py`, `tests/voice/{test_voice_ws,test_session}.py`, `tests/graph/{test_support_graph,test_e2e_local_script}.py` (one input changed from "hi" to a real question: that test is about a crashing triager), `tests/data/test_ticket_dataset.py` (`converse` allowed; excluded from the 3-per-route rule),
frontend: `src/lib/voice.ts`, `src/components/VoiceCall.tsx` (+test: goodbye ends the call after the audio finishes), `docs/{M2,ROADMAP,TESTING}.md`.

## Tests actually run (sandbox)
- Backend SQLite: 1025 passed, 30 skipped. PostgreSQL 16: 1053 passed, 2 skipped. With numpy blocked (your pre-voice-setup venv): 951 passed, 30 skipped. Offline eval regression gate passes without refreshing the reference.
- New: intent unit tests (positive and negative), floor tests (18 spoken requests, 13 look-alikes), 51 graph tests through the real support graph (a triager that raises if called proves no model is used for pleasantries and person requests), voice WebSocket tests (greeting, goodbye ends the call, forward-call escalation, "just testing" and the call continues, voice vs text identical replies, goodbye while approval pending, friendly words do not unlock records), session tests (closing spoken before hang-up, barge-in unchanged).
- Frontend: 40 vitest tests, `tsc --noEmit` clean.
- NOT run here: your microphone/speakers, real Whisper transcripts of these phrases, Ollama, your PostgreSQL.

## Verify locally
```
unzip -o ../supportgraph-m2-1.zip
make test-fast && git diff --check
make test-pg
(cd frontend && npm test && npx tsc --noEmit)
make e2e-local            # 17 messages now; the 5 new ones are code-handled and should all be [ok ]
make api                  # restart; open /voice and try:
```
Try by voice: "Hi" - "Thank you" - "I am just testing" - "Please forward this call to your support" - "Okay, thank you. That's it. You can stop the call." (the assistant says goodbye, the audio finishes, the call ends).
If a phrase you naturally say is not recognised, send me the transcript line from the page and I will add it.
