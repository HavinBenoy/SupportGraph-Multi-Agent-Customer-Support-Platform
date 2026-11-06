# M2.1.1 - Natural intent coverage and the e2e write-makeup regression

Apply: `unzip -o ../supportgraph-m2-1-1.zip` over the project (M2.1 already applied). No migration, no schema change, no dependency, no model change, `.env`, database and volumes untouched. Design notes: `docs/M2_1.md` (new section).

## 1. The `write-makeup` failure: root cause
Your first `make e2e-local` of the M1b final run created a real pending make-up request (stu_meera, PYD-301, 2026-10-28). The second run (this M2.1 check) answered
"You already have a pending make-up request ... " : outcome `duplicate`, route `write`, **no new row written, nothing approved**. That is the duplicate guard doing its job.
The hard check only accepted "paused for approval" (`awaiting`), so it flagged the correct reply: **the evaluation checked the wrong state for a second run**. The graph, the pending state and persistence are fine.

| Question | Answer |
|---|---|
| Did the graph fail to create the pending approval? | No: it was created by the earlier run and still pends; this run must not create a second one |
| Was approval state/event not persisted? | It was persisted (that is exactly why the guard fired) |
| Expected response wrong? | No, "already pending" is the correct reply |
| Assertion checking the wrong state? | Yes |

Fix without weakening: for outcome `duplicate` the check now looks in the DATABASE: the referenced request must exist, belong to this student and still be `pending`. Tests prove it still fails for a missing reference, a nonexistent id, another student's request, an approved request, and a write that neither paused nor is a duplicate. Reproduced in the sandbox with two runs on one database (second = duplicate, row count stays 1).
To exercise the pause again, approve/reject the old request in the staff console (`/agent`); nothing is deleted or reset by this patch.

## 2. Natural phrasing (all in the shared graph, so text and voice agree)
| You said | Now |
|---|---|
| "I didn't ask anything in the first place for you to say." | new `correction` intent: brief apology, no retrieval |
| "Okay, please proceed towards your support desk." | escalation floor (`human_request`): escalates before any FAQ |
| "I'm actually just testing you." | `interruption` intent (adverbs allowed): acknowledged |
| "Okay, that's everything. You can just cut the cord." / "You may please end the call." | `goodbye`: closing spoken, call ends |

More paraphrases covered: that's everything / that'll do / I have no more questions / you can hang up / disconnect the call / cut the line / we can wrap up; correction variants ("you didn't have to say that", "that's not what I meant", "why did you say that"); support-desk variants ("send me to your support team", "hand me over to support", "let a human handle this", "have someone from support call me", "support desk please").
Guards: a command to end the call needs a call word (call, chat, line, cord, ...) or "hang up"; "stop the course fee payment", "when does the session end", "how do I end my enrolment", bare "stop"/"end"/"cut" are NOT call-ending. Weak movement verbs (go/send/take/proceed...) need "your/our", so "Can I go to the support centre?" and "Where is the support desk?" stay questions. A correction or goodbye that carries a real question ("I didn't ask about attendance, I asked what is the fee ...") is not swallowed. Precedence is unchanged: injection screen, then escalation, then pleasantries; "yes"/"no" are never intents.

## Files
Changed: `backend/app/conversation/intents.py`, `backend/app/guardrails/escalation_floor.py`, `backend/app/graph/nodes/replies.py` (correction replies), `backend/scripts/e2e_local.py` (duplicate-aware hard check, `duplicate_is_genuine`, 5 more code-handled cases = 22 messages),
tests: `backend/tests/unit/test_intents.py`, `backend/tests/unit/test_escalation_floor.py`, `backend/tests/graph/test_m21_conversation.py`, `backend/tests/graph/test_e2e_local_script.py`, `backend/tests/voice/test_voice_ws.py`, docs: `docs/{M2_1,ROADMAP,TESTING}.md`, `CHANGES.md`.
No frontend change.

## Tests actually run (sandbox)
- SQLite: 1142 passed, 30 skipped. PostgreSQL 16: 1170 passed, 2 skipped. With numpy blocked: 1064 passed, 30 skipped. Offline eval regression gate passes (reference unchanged).
- New: exact failing utterances and paraphrases through the real support graph with a triager that raises if called (no model for pleasantries/person requests); unrelated "stop/end/cut" and mixed sentences stay in the normal flow; FAQ, abstention, escalation, sign-in RBAC, student isolation, injection, pending staff approval re-checked; the same new phrasings by voice WebSocket and by text give identical replies; the duplicate-aware e2e check (positive and negative).
- NOT run here: your Ollama, microphone, Whisper transcripts, PostgreSQL.

## Verify locally
```
unzip -o ../supportgraph-m2-1-1.zip
make test-fast && git diff --check
make test-pg
make e2e-local      # 22 messages. write-makeup should now be [ok ] (it will say "already pending" until you decide the old request in /agent)
make api            # restart, then say the five sentences above at /voice
```
Not changed: the "somebody is getting discounts" answer from your transcript (a legitimate knowledge/concession question, out of this patch's scope).
