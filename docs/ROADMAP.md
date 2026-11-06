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
| M1b | Real local AI on the text path: preflight/warm-up, Nomic prefixes, reasoning strip for every role, mock-Ollama integration tests, `make e2e-local` | built; sandbox-verified (mock Ollama only); **awaiting your `make e2e-local` run** | sandbox only (CHANGES.md, docs/M1B.md) |
| M2-M9 | Browser voice; low-latency path; live calls + takeover; trial verification; knowledge management; Edesy adapter (approval needed); closure | not started | |

The earlier "Steps 7-9" (generic multi-tenant onboarding, e-commerce pack) were dropped: this is a QSpiders-specific product.

Cross-cutting work not tied to one step: real LLM/embedding evaluation and calibration, hardening authentication (refresh tokens/MFA/SSO, httpOnly cookies, shared rate limiter), PII masking in
traces, rate limiting, student notification channel, student-only documents.


## Blocking prerequisites for M1 (agreed 2026-11-05)

1. **DONE in M1a - FAQ validator rejects truncated replies** (`truncated_answer`; also the QA reviser). Before any real LLM is connected to the product FAQ path, `validate` in `app/rag/faq.py`
   must reject a response whose `done_reason == "length"` (or that is otherwise known to be truncated). Today it accepts any reply with a valid
   citation, so a reply cut at the token limit would reach the student. Deliberately NOT changed in M0 (benchmark layer only).
2. Model-selection decision reviewed jointly from the schema-2 benchmark results.
