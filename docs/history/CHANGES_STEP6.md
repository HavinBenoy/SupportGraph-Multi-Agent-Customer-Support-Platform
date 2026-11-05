# CHANGES: Build step 6 (evaluation)

Extract this ZIP over your project folder. It is the **complete tree** minus `.venv`, `node_modules`, `.next`, caches, generated files and
secrets. Your `.env` and `infra/langfuse/.env` are not included and are untouched. Nothing resets or deletes database data; no migration in this step.

I worked from the project tree as I last delivered it (step 5), not from a ZIP you uploaded this time. If your local tree changed since (you ran
`make e2e` and the app), diff before overwriting.

## Apply and verify on your Ubuntu machine

```bash
cd ~/your-project && unzip -o supportgraph-step6.zip          # does not touch .env or .venv
.venv/bin/pip install -e "backend[dev]"                       # no new Python dependency
make test                                                     # fast suite incl. the new evaluation regression guard
make db-up                                                    # if not running
make test-pg                                                  # real Postgres + pgvector
make frontend-install && make frontend-test && make frontend-build    # next 15.1.6 -> 15.5.29, vitest 3.0.4 -> 3.2.7 (security patches, same majors)
make eval                                                     # dev + held-out + security + baseline comparison; writes evals/reports/latest.{md,json}
make judge-calibrate                                          # judge vs labels; add ARGS=--relabel to label the 48 cases yourself (~10 min)
# optional full-stack check after the dependency bump:  make api / make frontend-dev, then E2E_PASSWORD=... make e2e
```
`make eval` needs ingested documents. For the cross-tenant security cases to touch real other-tenant rows it needs the second-tenant fixture; see
`docs/STEP6.md` for the throwaway-database recipe (seeding with `--isolation-fixture` replaces the sample tenant's rows, so do not run it on your working DB).

## What changed

**Evaluation harness** (`backend/app/evals/`, `scripts/run_eval.py`, `scripts/judge_calibrate.py`, `make eval`, `make judge-calibrate`)
- Per-ticket outcomes for the support graph and the step 2 baseline under one "handled correctly" definition; the brief's metric list (triage, routing,
  knowledge, groundedness, escalation precision/recall, automation, wrong-action rate, latency, tokens/cost per ticket), slices, JSON + Markdown reports.
- Judge: offline `RuleJudge`, `LlmJudge` (stub-tested only), calibration with Cohen's kappa and a trust rule.
- Security suite runner with deterministic oracles; regression gates and `--reference` comparison; `tests/test_eval_regression.py` runs it all inside `make test`.
- Datasets (`evals/datasets/`): `tickets_qspiders_v2_heldout.jsonl` (62), `security_v1.jsonl` (55), `judge_labels_v1.jsonl` (48, **Claude-drafted labels**). Reference report in `evals/reports/`.

**Defects found by the evaluation and fixed** (each with a test that fails without the fix)
1. Write request naming another student ("update Rahul's phone") was reinterpreted as a change to the caller's own record -> refused in code (`actions/policy.py`, `graph/nodes/write.py`).
2. Escalation recall 71% on held-out safety / duplicate-charge messages -> model-independent `guardrails/escalation_floor.py` (can only add an escalation); recall 100%, precision 83% -> 88%. **This is the one routing behaviour change; it is additive and covered by tests.**
3. Signed token whose `student_id` differed from the account's was accepted -> checked with the role on every request (`api/deps.py`). Your step 5 note 1.
4. A follow-up escalation on a ticket whose earlier escalation was resolved returned the resolved one (lost; ticket "escalated", nothing open) -> new case with new reference (`db/write_repo.py`). Plausible cause of your step 5 notes 7 and 8; I could not reproduce your exact data, so please re-check.
5. Judge graded the system's own "(Demo data: ...)" footer as a claim -> ignored.

**Also**: direct security regression tests for cross-student access, role changes and stale tokens (`tests/api/test_security_regression.py`, 9 tests; your step 5 note 2); frontend dependency patches for your note 3 (below); `SG_EVAL_PRICE_*` settings; docs `docs/STEP6.md`, `docs/decisions/0004-...`, ROADMAP, README, datasets README; STEP5 doc default for the chat rate limit corrected (30; `.env.example` sets 20).

## Results (sandbox, executed by me; offline fakes only)

| Check | Result |
|---|---|
| Backend suite, SQLite | **601 passed, 28 skipped** (the 28 are Postgres tests; +59 new) |
| Backend suite, throwaway Postgres 16 + pgvector | **629 passed, 0 skipped** |
| Frontend after dependency bump | 26 tests passed, `tsc` clean, `next build` ok; live Chromium run against Postgres + built frontend: **9/9** |
| `make eval` on Postgres (dev / held-out) | graph handled correctly **88% / 60%** vs baseline 47% / 37%; escalation recall 100% / 100%; security **55/55**; zero leaks, unauthorized or unexpected tool calls, writes without a decision, crashes |
| Regression guard mutation check | disabling the escalation floor makes the guard fail (held-out recall 100% -> 71%); restored afterwards |
| Judge calibration (`RuleJudge`, 48 Claude-drafted labels) | groundedness kappa **0.74**, 4 of 21 hallucinations missed; relevance **not trusted** (2 negative examples) |
| ZIP hygiene | see end of this file's verification line in the final message: clean extraction, suites re-run from the extracted copy, no `.env`/`.venv`/`node_modules`/caches/DB files, key-pattern scan |

**Read the numbers honestly (details in docs/STEP6.md).** The dev set flatters the system (the fakes were written while looking at it). First measurement on the
held-out set was 56% handled / 77% route accuracy / 71% escalation recall; 60% / 81% / 100% after fix 2. On plain knowledge questions held-out the graph is **no
better than the baseline** (40% each) and declines unanswerable questions less often (43% vs 86%): the fake triager over-asks for detail. Its advantage is actions, writes and
escalations, which the baseline cannot do. I deliberately did not tune the fakes to the held-out tickets.

## Not verified here (needs you)

- `make test`, `make test-pg`, `make eval`, `make judge-calibrate` on **your** machine and database; frontend build/tests after the dependency bump on your machine.
- **Any real LLM, embedding model or judge model.** The LLM judge, real triage and real answer quality are unmeasured. This is the next evidence the project needs.
- Human review of the judge labels (they are my drafts, flagged `needs_human_review`).
- Cross-tenant security cases on your own DB (they need the second-tenant fixture); without it the report says they only prove no other-tenant text appeared.
- Langfuse tracing of eval runs (tracing is off in the eval path unless you enabled it).

## Known limitations and open items

- Small sets (57 + 62 tickets, 55 attacks, 48 judge cases); labels are one judgment; a few points of difference is noise.
- `npm audit` (5 findings left, was 6): the Next.js RCE (image optimisation) and DoS findings are fixed by 15.5.29. Remaining: `vitest`'s `tinypool` / `@vitest/mocker` (test runner only, never shipped; fix needs vitest 5, breaking) and PostCSS bundled inside Next (build-time processing of our own CSS; fix needs Next 16, breaking). I did not force breaking upgrades; plan them as their own step.
- Still open from step 5 and unchanged: token in `sessionStorage` (XSS exposure), per-process in-memory rate limiter, single shared PostgreSQL checkpointer connection (pool needed before concurrent traffic), no refresh tokens/MFA/SSO. The platform is not production-ready.
- Observation 8 (reference vs ticket heading) not reproduced; may be fixed by defect 4.
- T026 ("who is the trainer for PYF-101", expected decline) still gets an answer from a same-batch passage (known since step 3).
