# CHANGES: Build step 4 (write actions, approvals, pauses, checkpointer, QA loop, escalation briefs)

Extract this ZIP over your project folder. It is the **complete tree** minus `.venv`, caches, generated files and secrets; your
`.env` and `infra/langfuse/.env` are not included and are untouched.

## Apply and verify on your Ubuntu machine

```bash
cd ~/your-project && unzip -o supportgraph-step4.zip     # does not touch .env or .venv
.venv/bin/pip install -e "backend[dev]"                  # NEW dependency: langgraph-checkpoint-postgres (see pyproject.toml)
# add these two lines to your .env (they are in .env.example; your .env was not changed):
#   SG_CHECKPOINTER=postgres
#   SG_QA_MAX_REVISIONS=1
make test                                                # fast suite, SQLite, no Docker
make db-up && make db-migrate                            # migration 0004 (additive) + checkpointer tables. Does NOT reset or delete data.
make test-pg                                             # real Postgres + pgvector (throwaway databases)
make support Q="Please arrange a make-up class for the session I missed in PYD-301" STUDENT=stu_meera TICKET=demo1   # PAUSES
make review ARGS="list"
make review ARGS="approve demo1 --staff s_1"             # resumes the run from a different process
make support Q="Please update my phone number to +91 98765 43210" STUDENT=stu_meera TICKET=demo2
make support Q="yes" STUDENT=stu_meera TICKET=demo2
make support Q="I paid twice and want my money back" STUDENT=stu_asha TICKET=demo3
make eval-routing                                        # exit 1 on a leak, unauthorized tool call or write without a decision
make verify-tracing-support                              # your real Langfuse; now also expects a `qa` span
```
`make support` passes `--ticket` when you give `TICKET=...`; reuse the same ticket to continue one conversation.
`make db-seed` now **refuses** to re-seed a tenant that already has cases (tickets/requests); add `--reset-cases` only to discard them.

## Test results: what I ran vs what I did not

**Run in my sandbox (executed, this ZIP's code):**
- SQLite suite: **511 passed, 28 skipped** (the 28 are Postgres-only tests); also re-run from a clean extraction of this ZIP.
- Throwaway Postgres 16 + pgvector 0.6.0: **539 passed** (includes 8 new `tests/db/test_postgres_step4.py` tests: migration 0004 up/down with data
  preserved, models-vs-migration diff, constraints, a pause that survives a simulated process restart on the Postgres checkpointer,
  thread isolation, concurrent double approval where exactly one wins, reseed guard).
- Cross-process run on Postgres through the real scripts: `ask_support.py` paused a make-up request, a separate `review_queue.py approve`
  process resumed and approved it, a second approve was refused; an escalation stored a brief; `seed_store.py` refused to re-seed over cases.
- `eval_routing` on Postgres, fake models, 57 tickets: route accuracy 100%, escalation precision/recall 100%/100%, exact tool match 100%,
  write tickets routed 100%, paused for the correct approver 100%, escalations recorded with a brief 100%, 0 unauthorized tool calls,
  **0 writes without a decision**, 0 leaks. QA blocked T021 (Java fee answered from the Python table). Knowledge baseline -> corrective+QA:
  answered 91% -> 96%, all-facts-correct 65% -> 74%, correct abstention 75% -> **88%**. T026 (trainer for PYF-101) still gets answered: the batch id
  appears in the cited batch table, so the entity check cannot see that trainers are missing. Treat these numbers as rule-fit with offline fakes,
  not as real-model quality.
- Mutation checks (each made tests fail, then restored): removing the role check in `authorize_decision`, removing the `interrupt` (auto-approve),
  disabling the QA entity check.

**NOT run (needs you):** `make test`, `make test-pg`, `make db-migrate` on your database, the commands above, `make verify-tracing-support`
against your real Langfuse, and anything with a real LLM or embedding model (`LlmProposer`, `LlmCritic`, `LlmReviser`, `LlmTriager` are tested only with stub models).

## What was built (one line each; design in README "Build step 4")
- **Write tools + boundaries** `app/actions/`, `app/db/write_repo.py`: the model only proposes; code validates (own active enrolment, a session really missed within `makeup_window_days`), policy picks the approver, code writes after re-checking who decided.
- **Approval policy**: make-up class -> staff; phone change -> the student's own "yes". Nothing automatic. Concessions/refunds are escalations, never tools.
- **Interrupts + resume** `graph/nodes/write.py`, `graph/review.py`: `interrupt()` pause; `resume_ticket`, `converse`; wrong-party answers are ignored by re-asking (bounded).
- **Persistent checkpointer** `graph/checkpointer.py`: memory or Postgres (`SG_CHECKPOINTER`); thread id `tenant:student:ticket` (the brief says `tenant:ticket`; the student was added so a guessed ticket id cannot reach another student's thread).
- **QA loop** `app/qa/`, `graph/nodes/qa.py`: deterministic checks (leak, other student id, contact details, forbidden promises, uncited, unsupported numbers, entity mismatch) -> revise once -> safe fallback; security failures also escalate. Optional LLM critic/reviser.
- **Escalation briefs** `app/escalation/`, `graph/nodes/escalate.py`: ticket + code-built, PII-masked brief, team and SLA target from tenant config; staff queue (`make review`).
- **Database** migration `0004`: `tickets`, `escalations`, `makeup_requests`, `audit_log`, nullable `students.phone`. Additive; downgrade drops exactly these.

## Files
**Added:** `app/actions/{__init__,schemas,policy,proposer,executor}.py`, `app/qa/{__init__,checks,critic,revise}.py`, `app/escalation/{__init__,brief,queue}.py`,
`app/db/write_repo.py`, `app/db/migrations/versions/0004_actions_and_escalations.py`, `app/graph/{checkpointer,review}.py`,
`app/graph/nodes/{common,escalate,write,qa}.py`, `app/guardrails/pii.py`, `scripts/{review_queue,setup_checkpointer}.py`,
`tests/step4/*` (conftest + 7 test files), `tests/db/test_postgres_step4.py`, `docs/ROADMAP.md`.
**Modified:** `app/db/models.py`, `app/db/seed.py` (+ `scripts/seed_store.py`: cases guard), `app/config.py` (`checkpointer`, `qa_max_revisions`),
`app/tenants/schema.py` + `tenants/qspiders_sample/config.yaml` (`makeup_window_days`, `escalation_teams`, `default_team`, `sla_hours`),
`app/agents/{schemas,triage}.py` (`contact_update`, `WRITE_CATEGORIES`; `makeup_request` is no longer an escalation), `app/llm/fake_triage.py`,
`app/llm/gateway.py` (`get_proposer/get_critic/get_reviser`), `app/graph/{support,state,evaluate}.py`, `app/graph/nodes/{router,replies}.py`,
`scripts/{ask_support,eval_routing,verify_tracing}.py`, `Makefile`, `.env.example`, `backend/pyproject.toml`, dataset `tickets_qspiders_v1.jsonl` (57 tickets) + README + dataset test, step 3 tests adapted (thread id), README.
**Removed:** nothing.

## Behaviour changes to know about
- Replies of escalations now carry a reference id and the team name (no timing promise). Make-up requests no longer escalate: they follow the write path.
- Dataset: T038 relabelled `escalate` -> `write` (student changed to stu_meera, batch PYD-301); T054-T057 added.
- `build_support_graph` defaults to an in-memory checkpointer; every run needs a thread id (`support_config` supplies it).
- `ask_support` result may contain `awaiting` ("staff"/"student") and `pending` when the run paused.

## Known limitations / unfinished
No authentication (student id and `--staff` are trusted); students are not notified when staff decide; LLM proposer/critic/reviser untested with a real model;
QA cannot catch a plausible cited wrong paraphrase or T026-type answers; one shared Postgres connection for the checkpointer (use a pool for concurrent traffic);
anonymous escalations cannot be answered (no contact captured); the brief's `thread_id = tenant:ticket` was changed to include the student; no frontend yet (step 5 in the brief).

---------------------------------------------------------------------------------------------------

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
