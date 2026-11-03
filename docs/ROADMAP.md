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
| 5 | Frontend: customer widget, agent console, admin analytics | not started | |
| 6-9 | Evals; config-driven tenants; voice; polish (auth, docs, deployment) | not started | |

Cross-cutting work not tied to one step: real LLM/embedding evaluation and calibration, authentication/authorization, PII masking in
traces, rate limiting, student notification channel, student-only documents.
