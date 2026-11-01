# CHANGES: realign to QSpiders / PySpiders, then Step 2 (RAG FAQ baseline)

Apply by extracting this ZIP over your project folder (it is the **complete tree**, minus `.venv`, caches and secrets;
your `.env` and `infra/langfuse/.env` are not included and are untouched). Then:

```bash
rm -rf tenants/acme_store evals/datasets/tickets_acme_v1.jsonl       # retired retail files (ZIPs cannot delete)
make venv && make test                                                # fast suite, no Docker
make db-up && make db-migrate && make db-seed && make ingest          # migration 0003 (see below), sample data, FAQ docs
make demo && make faq Q="Can I pay the fee in instalments?" && make eval-rag
make test-pg                                                          # real Postgres + pgvector tests
make verify-tracing && make verify-tracing-faq                        # needs your Langfuse (make langfuse-up)
```

**Migration 0003 is destructive for the retail demo data only:** it drops `customers`, `orders`, `order_items` and deletes the
tenants `acme_store` / `other_store` plus their ingested demo documents. Other tenants are kept. Downgrade restores structure, not rows.

## What changed
**Step 0 realigned (not renamed):** new schema (students, courses, batches, enrollments, class sessions, attendance, assessments,
results) + migration `0003`; seed with six scenario students (above/below/exactly-at the attendance threshold, on hold, two
batches, withdrawn) and an isolation-fixture tenant; tools `get_my_batches`, `get_my_schedule`, `get_my_attendance`,
`list_my_assessments`; new agent prompt and scripted fake model; `customer_id` to `student_id`; tenant config now has timezone,
attendance threshold, `is_sample`. Tracing code unchanged apart from names.
**Step 2 added:** `backend/app/rag/` (documents loader, chunking, ingest, retrieval, prompts, FAQ graph, evaluate), embedding/fake
modules in `app/llm/`, migration `0002` (pgvector tables; **it was already in the workspace and I did not write it**, along with
the chunking/loader/embedding/fake-model foundation, which I reviewed, tested and built on), scripts `ingest_docs.py`, `ask_faq.py`,
`eval_rag.py`, `verify_tracing.py --workflow faq`, Makefile targets, `.env.example` entries.
**Content:** 8 public docs (1 from qspiders.com facts read on 2026-10-31, 7 labelled SAMPLE), 1 internal playbook (SAMPLE), 53 synthetic tickets.
Audience value `customer` became `public` (migration 0003). Details: README, `docs/decisions/0001-institute-domain.md`.
**Removed:** `tenants/acme_store/`, `tickets_acme_v1.jsonl`.

## Checks actually run (sandbox, no Docker, no real Langfuse, no real LLM)
| Check | Result |
|---|---|
| Full suite, SQLite | 329 passed, 20 skipped (the skips are the Postgres-only tests) |
| Full suite with throwaway Postgres 16 + pgvector 0.6.0 | 349 passed |
| Migration 0001-0003 up/down, retail-to-institute upgrade path, models-vs-migrations sync | passed; upgrade test shown to fail when the migration is deliberately broken |
| Agent demo on Postgres (5 questions, cross-batch probe, re-seed twice) | worked as expected |
| `ingest_docs.py` (twice), `ask_faq.py`, `eval_rag.py` on pgvector | worked; second ingest was a no-op; exit 0 |
| Tracing | span export tested with the real Langfuse SDK against a local stand-in server (agent and FAQ spans). **The real Langfuse server was NOT run** |

Baseline with the offline fakes (plumbing only, not language understanding): retrieval right-doc-first 91%, in top 4 96%, MRR 0.93;
answered 91% of answerable questions, all facts correct 65%; unsupported numbers 0%; **leaks 0 of 53**; correctly abstained 6 of 8 (75%).
Known misses: Java fee and "trainer for PYF-101" answered from unrelated passages, "marks" vs "score" vocabulary gap, romanised Hindi.

## Remaining manual steps
Run `make test-pg`, `make verify-tracing`, `make verify-tracing-faq` on your machine; try a real embedding model
(`SG_EMBEDDING_PROVIDER=ollama`, `ollama pull nomic-embed-text`) and calibrate `SG_RAG_MIN_SCORE`; replace sample documents with the
institute's approved content; update `PROJECT_BRIEF.md` (still describes the retail store).
