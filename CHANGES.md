# M1b fixes after the first real-model run - consolidated (batches 1-3; apply batch 1, 2, then this ZIP)

Preserved throughout: Qwen3 1.7B, Nomic with prefixes, citation validation, abstention, escalation floor (additive only), RBAC, tenant isolation,
approval pause. No model benchmark, no schema/migration, no dependency, `.env`/database/volumes untouched.

## Findings and fixes (from your e2e report + read-only diagnostic)
| # | Case | Root cause | Type | Fix (batch) |
|---|---|---|---|---|
| 1 | `faq-unanswerable` | triage `other` 0.5 < 0.6 -> "name the batch"; FAQ would have abstained (`INSUFFICIENT_CONTEXT`) | graph routing | `other` at 0.4..<0.6 -> grounded FAQ (1) |
| 2 | `faq-outside` | same | graph routing | same (1) |
| 3 | `human` | triage `other` 0.5; no floor rule | model classification + missing floor rule | `human_request` floor rule + prompt (1) |
| 4 | `faq-fee` | same text gave `other` 0.8 in the diagnostic, but escalated in e2e: 1.7B triage unstable run to run | model classification (unstable) | prompt definitions (1); instability now reported (3) |
| 5 | `faq-courses` | correct answer without citation -> `uncited_answer` | model compliance, caught by the validator | one validated citation retry (2) |
| 6 | `write` | invented "reschedule" request: no such action | test-data error (mine) | e2e uses labelled T038 (3) |
Also fixed: tests depended on your `.env` embedding provider (found by your batch-1 `make test-fast`): `tests/conftest.py` forces fake embeddings (2).

## Behaviour changes
- Router: category `other`, confidence >= `SG_TRIAGE_OTHER_FLOOR` (0.4) and < `SG_TRIAGE_MIN_CONFIDENCE` (0.6) -> knowledge (reason `other_low_confidence`). Below 0.4 and parse failures (0.0): still clarify. Other categories unchanged. Injection and escalation first.
- Floor: `human_request` rule (talk/speak/connect/transfer to a person/human/agent/representative/someone; real person; human agent; not a bot).
- FAQ: an `uncited_answer` is regenerated once with a citation reminder; same validator; abstain if it fails again. Nothing else is retried; code never adds a citation. `SG_FAQ_CITATION_RETRIES=0` disables.
- Triage prompt: defines human_request, concession_request (discount/waiver only), other; confidence is about the category.
- Offline eval reference refreshed in batch 1 (heldout handled-correctly 59.7% -> 64.5%, correct abstention 42.9% -> 71.4%, over-abstain 16.7% -> 20.0% due to the fake FAQ on T110; escalation recall 100%).

## This batch (3): e2e and final verification
- `scripts/e2e_local.py`: write case is now labelled ticket T038 (`stu_meera`, make-up class for PYD-301) with a HARD check that a write route must pause for approval; per-case soft "as hoped / differs" column; report shows triage category+confidence, FAQ validator reason, number of generations and the raw answer of every non-answered FAQ result (via a recording wrapper; nothing extra is stored in product state); `--stability-runs N` (default 3, 0 = skip) classifies each message N times through the real triager and the real route node and reports category/route variation; `today` now follows the labelled-ticket rule (`today_for`) so make-up windows match your seeded data.
- `tests/graph/test_m1b_six_cases.py` (new, 11 cases): the six findings through the full support graph with triage scripted to what the real model returned, plus guards: model escalation is not overridden, uncited smuggling fails, anonymous make-up is not written, unsupported "reschedule" writes nothing.
- `tests/graph/test_e2e_local_script.py`: dry run covers the new report fields and stability.
- `docs/M1B.md`, `docs/ROADMAP.md` updated.

## Tests actually run (sandbox, final)
| Run | Result |
|---|---|
| SQLite full suite (`pytest -n auto`, with `SG_LLM_PROVIDER=ollama SG_EMBEDDING_PROVIDER=ollama` exported) | 790 passed, 30 skipped |
| PostgreSQL full suite (`SG_TEST_DATABASE_URL`, sandbox PG 16) | 818 passed, 2 skipped (opt-in real-Ollama tests) |
| New six-case file | 11 passed |
| Real Ollama / real latency | NOT run here: your `make e2e-local` |

## Verify locally
```
make test-fast
make test-pg
git diff --check
make e2e-local              # ~5-8 min now (3 extra triage calls per message for the stability table; use ARGS="--stability-runs 0" to skip)
```
Expect: `HARD failures: 0` (the three earlier failures were human + two abstain checks, now `human` escalates and both out-of-scope cases route to knowledge and abstain). Soft column: faq-courses should answer with a citation (generations 2 if the model forgot), write-makeup should show route write + staff. If faq-fee escalates again, the triage table will show the category; send me the report.
Optional rollbacks: `SG_TRIAGE_OTHER_FLOOR=0.6`, `SG_FAQ_CITATION_RETRIES=0`.

Files (this ZIP): modified `docs/M1B.md`, `docs/ROADMAP.md`, `backend/scripts/e2e_local.py`, `backend/tests/graph/test_e2e_local_script.py`; added `backend/tests/graph/test_m1b_six_cases.py`; `CHANGES.md` replaced. Deleted: none.
