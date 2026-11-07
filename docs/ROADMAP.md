# Roadmap and progress

The original plan lives in the project brief (PROJECT_BRIEF.md in the Claude Project; it still describes the retail example,
see `docs/decisions/0001-institute-domain.md`). Status of each build step for the QSpiders / PySpiders sample tenant:

| Step | Scope | Status | Where verified |
|---|---|---|---|
| 0 | Skeleton, Postgres + pgvector, ReAct agent with read tools, Langfuse tracing | done | your machine (`make test`, `make test-pg`, `make verify-tracing`) |
| 1 | Institute data model, sample documents, labelled tickets | done | your machine |
| 2 | Single-agent RAG FAQ baseline + evaluation | done | your machine (fakes only; real embeddings/LLM not evaluated) |
| 3 | Triage, conditional routing, knowledge (corrective RAG) and action (read tools) agents | done | your machine (fakes only) |
| 4 | Write tools + approval policy, interrupts/resume, persistent checkpointer, QA loop, escalation briefs | built; sandbox-verified; **awaiting your local run** | sandbox only (see CHANGES.md) |
| 5 | Web API + auth + frontend: customer chat widget, staff console (escalations, approvals), admin analytics | built; sandbox-verified (API tests, 26 frontend tests, production build, live browser run on Postgres); **awaiting your local run** | sandbox only (see CHANGES.md, docs/STEP5.md) |
| 6 | Evals: held-out + security sets, baseline comparison, groundedness judge + calibration, latency/cost, regression gates, fixes | built; sandbox-verified with offline fakes only; **awaiting your local run** | sandbox only (see CHANGES.md, docs/STEP6.md) |
| M0 | Benchmark kit + model-gateway hardening, then measurement fixes after the first run (schema 2: full outputs, truncation = failure, per-split reports, route/escalation metrics, run manifest) | built; sandbox-verified (mock Ollama only); **awaiting your local test + benchmark run** | sandbox only (CHANGES.md, docs/BENCHMARKS.md, ADR 0005) |
| M1a | Truncation-safe FAQ validator (blocking prerequisite met), default model qwen3:1.7b, configurable sampling, tiered tests (`docs/TESTING.md`), experiment tooling | built; sandbox-verified; **awaiting your local run** | sandbox only (CHANGES.md, docs/MODEL_SELECTION.md, ADR 0006) |
| M1b | Real local AI on the text path: preflight/warm-up, Nomic prefixes, reasoning strip for every role, citation retry, `other`-band routing, human-request floor, mock-Ollama integration tests, `make e2e-local` | **CLOSED 2026-11-06**: verified on your machine (SQLite 790 passed/30 skipped, PostgreSQL 818 passed/2 skipped, real Ollama e2e 12/12 scenarios, 0 hard failures; retrieval Hit@1 95.7% dev / 90% heldout with prefixes) | your machine (CHANGES.md, docs/M1B.md) |
| M2 | Browser voice line: WebSocket + Silero VAD + faster-whisper + Piper over the existing graph, call page, barge-in, `make voice-setup` / `make voice-smoke` | **VERIFIED on your machine 2026-11-06** (`make voice-smoke` all sentences round-tripped, WER 0-11%, TTS first audio ~110 ms, STT RTF ~0.13; SQLite 790/30, PostgreSQL 818/2 before M2.1; a real voice call works) | your machine (docs/M2.md) |
| M2.1 / M2.1.1 | Conversational polish (and natural-phrasing coverage after real calls, plus the e2e duplicate-aware write check): greetings, thanks, goodbye (ends the call), "just testing", honest hand-off to a person for spoken/imperfect wording; one shared layer for text and voice | M2.1 verified locally (SQLite 1024/31, PostgreSQL 1052/3, frontend 40, e2e all but the duplicate-unaware check); M2.1.1 sandbox-tested (SQLite 1142/30, PostgreSQL 1170/2); **awaiting your run and a real call** | sandbox (docs/M2_1.md) |
| M2.2 | Voice turn management: utterance aggregation (pauses inside a sentence stay one turn), one graph run and one spoken response at a time, turn ids end to end, stale-reply handling, paced audio, turn-aware browser player, conversation context (follow-up/correction), new phrase coverage, structured voice events | verified locally (SQLite 1244/31, PostgreSQL 1272/3, frontend 54, e2e 22/22, 0 hard failures); real-call bugs fixed in M2.2.1 (transcript lines overwritten across calls, more goodbye phrasing); real-call fixes applied in M2.2.1/M2.2.2; **M2 CLOSED 2026-11-07** | sandbox (docs/M2_2.md) |
| M3 | Low-latency path, evidence-based (one change set, `supportgraph-m3-complete.zip`): stage-level timings and Ollama counters in every turn with a cold/warm verdict and reconciled node times; four distinct voice latencies (graph done, first audio, answer audio, playback done) plus the browser's first-audible ack; complete model warm-up (same Ollama options as real calls, triage and retrieval primed, sequential, failure-tolerant); triage cache; validated-answer cache for repeated anonymous FAQ questions; cached fixed holding-phrase audio; opt-in compact triage prompt, prompt-size controls and `SG_LLM_NUM_THREAD`; `make bench-voice` benchmark. See docs/M3.md | built; sandbox-tested (SQLite, PostgreSQL, frontend); **awaiting your real-hardware run** (docs/M3.md section 10) | sandbox (docs/M3.md) |
| M4 | Call sessions, staff Live Calls, listening and human takeover (one change set, `supportgraph-m4-complete.zip`): durable `calls`/`call_events` (migration 0006) with an atomic ownership state machine and epochs; staff REST + audio WebSocket; listen, take over, return to AI, end; Live Calls tab in the staff console; audit events. Also M3.1: the embedder keeps its `keep_alive`. See docs/M4.md | built; sandbox-tested (SQLite, PostgreSQL, frontend); **awaiting your real-hardware run** (docs/M4.md section 12) | sandbox (docs/M4.md) |
| M5-M9 | Trial verification; knowledge management; Edesy adapter (approval needed); closure | not started | |

The earlier "Steps 7-9" (generic multi-tenant onboarding, e-commerce pack) were dropped: this is a QSpiders-specific product.

Cross-cutting work not tied to one step: real LLM/embedding evaluation and calibration, hardening authentication (refresh tokens/MFA/SSO, httpOnly cookies, shared rate limiter), PII masking in
traces, rate limiting, student notification channel, student-only documents.


## Blocking prerequisites for M1 (agreed 2026-11-05)

1. **DONE in M1a - FAQ validator rejects truncated replies** (`truncated_answer`; also the QA reviser). Before any real LLM is connected to the product FAQ path, `validate` in `app/rag/faq.py`
   must reject a response whose `done_reason == "length"` (or that is otherwise known to be truncated). Today it accepts any reply with a valid
   citation, so a reply cut at the token limit would reach the student. Deliberately NOT changed in M0 (benchmark layer only).
2. Model-selection decision reviewed jointly from the schema-2 benchmark results.
