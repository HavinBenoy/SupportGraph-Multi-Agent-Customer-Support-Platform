# M1b fix batch 1 of 3 - routing and escalation (delta on top of m1b)

Based on your first real-model e2e run and the read-only diagnostic. Models, Nomic prefixes, citation validation, abstention, RBAC,
tenant isolation, approval flow, `.env`, database, schema and volumes are untouched. No new dependency.

## Findings addressed
| Case | Cause found | Fix |
|---|---|---|
| `human` | real 1.7B triage returned `other` at 0.5; no floor rule for explicit human requests | floor rule `human_request` + prompt line |
| `faq-unanswerable`, `faq-outside` | triage `other` at 0.5 (< 0.6) -> clarify "name the batch"; the FAQ itself abstained correctly (`INSUFFICIENT_CONTEXT`) | router rule: `other` at 0.4..<0.6 -> knowledge (FAQ) |
| `faq-fee` (escalated in e2e, `other` 0.8 in diagnostic) | 1.7B triage unstable between runs | prompt definitions (concession vs published payment options); stability reporting arrives in batch 3 |

## Changes
- `app/graph/nodes/router.py`: `decide_route(..., other_floor=0.4)`. Category `other` with `other_floor <= confidence < min_confidence` -> `knowledge` (reason `other_low_confidence`). Below 0.4, parse failures (0.0) and every other category behave as before. Injection and escalation are checked first, as before.
- `app/config.py`: `triage_other_floor` (SG_TRIAGE_OTHER_FLOOR, default 0.4). `app/graph/support.py` passes it.
- `app/guardrails/escalation_floor.py`: new `human_request` rule (talk/speak/connect/transfer to a person/human/agent/representative/someone, "real person", "human agent", "not a bot"). Can only ADD an escalation; priority is not raised.
- `app/agents/triage.py` (prompt): defines `human_request`, `concession_request` (discount/waiver/scholarship only; "can I pay in instalments?" is `fees`), `other`, and says confidence is about the category.
- `evals/reports/reference_fake_v1.{json,md}` and `latest.{json,md}`: refreshed deliberately (see below).
- Tests: `tests/unit/test_router.py` (+band/priority/config tests), `tests/unit/test_escalation_floor.py` (+7 must-fire, 7 look-alikes that must not fire), `tests/graph/test_m1b_batch1_cases.py` (4 full-graph cases with scripted triage = what the real model returned).

## Offline eval reference refresh (a documented step, reviewed)
`tests/test_eval_regression.py` failed after the router change (heldout over-abstain 16.7% -> 20.0%). Cause: five vague heldout tickets that used to get a clarifying question now reach the FAQ. Offline fake triage, fake keyword FAQ model:
| heldout (graph) | before | after |
|---|---|---|
| handled correctly | 59.7% | 64.5% |
| correct abstention on must-abstain (abstain_rate) | 42.9% | 71.4% |
| knowledge correct_rate | 40.0% | 43.3% |
| over_abstain_rate | 16.7% | 20.0% (T110 "Do you teach Selenium?": answerable, fake FAQ abstains) |
| wrong_answer_rate | 36.8% | 38.1% |
| escalation recall / precision | 100% / 87.5% | 100% / 87.5% |
Dev set unchanged. Gates were NOT loosened; the reference was regenerated and the gate passes against it (tolerance 0.005). Real-model numbers come from `make e2e-local`.

## Tests actually run (sandbox)
- Targeted: router + floor + batch-1 graph tests: 71 passed.
- Full SQLite suite (`pytest -n auto`): 760 passed, 30 skipped.
- PostgreSQL suite and real Ollama: not in this batch (PostgreSQL runs in batch 3; real models are your run).

## Verify locally
```
make test-fast
git diff --check
cd backend && ../.venv/bin/python -m pytest -q tests/unit/test_router.py tests/unit/test_escalation_floor.py tests/graph/test_m1b_batch1_cases.py
make e2e-local            # expect: human now escalates; faq-unanswerable / faq-outside now route=knowledge and abstain
```
Optional knob: `SG_TRIAGE_OTHER_FLOOR=0.6` restores the old "always clarify" behaviour for `other`.
Files modified: `app/agents/triage.py`, `app/config.py`, `app/graph/nodes/router.py`, `app/graph/support.py`, `app/guardrails/escalation_floor.py`, `tests/unit/{test_router,test_escalation_floor}.py`, `evals/reports/*` (4). Added: `tests/graph/test_m1b_batch1_cases.py`. Deleted: none.
