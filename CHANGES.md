# CHANGES: Step 2 review + Step 3 (triage, routing, knowledge agent, action agent)

Extract this ZIP over your project folder. It is the **complete tree** minus `.venv`, caches, generated files and secrets;
your `.env` and `infra/langfuse/.env` are not included and are untouched. **No database migration** in this step.

```bash
cd ~/your-project && unzip -o supportgraph-step3.zip       # does not touch .env or .venv
make test                                                  # fast suite on SQLite, no Docker
make db-up && make db-migrate && make db-seed && make ingest   # only if your DB is not already set up (do NOT reset it)
make test-pg                                               # Postgres + pgvector
make support Q="What courses are available?"
make support Q="What is my attendance in PYF-101?" STUDENT=stu_asha
make support Q="What is my attendance in PYF-101?"         # prospect: asked to sign in
make support Q="I paid twice and want my money back" STUDENT=stu_asha
make eval-routing                                          # exit code 1 on any leak or unauthorized tool call
make verify-tracing-support                                # needs your Langfuse (make langfuse-up)
```

## Verified in my sandbox (executed)
- Full suite on SQLite: **406 passed, 20 skipped** (the 20 are the Postgres-only tests), also re-run from a clean extraction of this ZIP.
- Full suite on a throwaway Postgres 16 + pgvector: **425 passed** (run before the last test, `test_support_tracing.py`, was added, so expect 426 on your machine).
- `eval_routing` on SQLite with the fakes: route accuracy 100% of 53 (rule-fit on the tickets the rules were written against, not a prediction),
  escalation precision/recall 100%/100%, exact tool match 100% on 9 action tickets, action facts 89%, 0 unexpected/unauthorized tool calls, 0 leaks.
  Knowledge questions, step 2 single pass -> corrective: answered 91% -> 96%, all-facts-correct 65% -> 74%, correct abstention 75% -> 75%.
- Mutation check: removing the sign-in guard in the router and the `anonymous` rejection in `require_scope` makes 3 security tests fail (restored afterwards).

## NOT verified here (please run on your Ubuntu machine)
`make test`, `make test-pg`, `make eval-routing` (on your Postgres), `make support ...`, `make verify-tracing-support` against your real Langfuse,
and anything with a real LLM or embedding model (`LlmTriager` and `LlmRewriter` have only been run against stub models).

## What changed
**Added:** `app/agents/{schemas,triage}.py`, `app/guardrails/injection.py`, `app/llm/fake_triage.py`, `app/rag/rewrite.py`,
`app/graph/{support,evaluate}.py`, `app/graph/nodes/router.py`, `app/graph/subgraphs/knowledge.py`, `scripts/{ask_support,eval_routing}.py`,
tests (`unit/test_router.py`, `unit/test_triage.py`, `unit/test_graph_evaluate.py`, `rag/test_rewrite_and_knowledge.py`,
`graph/test_support_graph.py`, `graph/test_support_tracing.py`), `docs/decisions/0002-step3-triage-routing.md`, `docs/STEP2_REVIEW.md`.
**Modified:** `rag/chunking.py` (**bug fix**: oversized chunk) + regression test; `rag/faq.py` (optional `search_query`); `tools/scope.py` (`anonymous` rejected);
`graph/state.py`, `graph/nodes/replies.py`; `llm/gateway.py` (`get_triager`, `get_rewriter`); `config.py` + `.env.example` (`SG_TRIAGE_MIN_CONFIDENCE`,
`SG_KNOWLEDGE_MAX_ATTEMPTS`); `tenants/schema.py` + `tenants/qspiders_sample/config.yaml` (`query_synonyms`); `scripts/verify_tracing.py` (`--workflow support`);
`Makefile` (`support`, `eval-routing`, `verify-tracing-support`); ticket T048 relabelled `clarify` -> `action`; README.
**Removed:** nothing.

See `docs/STEP2_REVIEW.md` for the Step 2 review and `README.md` "Build step 3" for the design and limits.

---------------------------------------------------------------------------------------------------

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
