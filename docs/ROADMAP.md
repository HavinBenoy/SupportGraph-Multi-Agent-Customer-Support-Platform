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
| 7-9 | Config-driven tenants and onboarding; voice; polish (auth, docs, deployment) | not started | |

Cross-cutting work not tied to one step: real LLM/embedding evaluation and calibration, hardening authentication (refresh tokens/MFA/SSO, httpOnly cookies, shared rate limiter), PII masking in
traces, rate limiting, student notification channel, student-only documents.
