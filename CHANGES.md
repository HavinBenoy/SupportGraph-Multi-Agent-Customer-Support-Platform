# CHANGES: M1a-2 (4B diagnosis, corrected model notes, human review sheet)

Small delta on top of `supportgraph-m1a.zip`. No `.env`, schema, migration, prompt, retrieval or model-config change; the default model stays `qwen3:1.7b`.
Previous notes: `docs/history/CHANGES_M1a.md`.

## What your results established
- `qwen3:4b` as pulled is a **thinking-only** build (probe: `think:false` and `/no_think` ignored, reasoning in the visible answer, `thinking` capability, 262,144 context). Sampling cannot fix it. My earlier greedy-decoding hypothesis was wrong.
- `qwen3:4b-instruct-2507-q4_K_M` (dev, first 20 tickets, one run): 85% correct, 0/20 truncated, p50 6.6 s, vs qwen3:1.7b 70%, 0/20, 2.9 s. The 3-ticket gap is within noise, and those 20 tickets have **no must-abstain cases**, so abstention is unmeasured. Default unchanged; see docs/MODEL_SELECTION.md.

## Changed
- `backend/app/bench/report.py`: `render_review()` (read-only human-review sheet of `missing_fact` / `mixed_output` FAQ failures; never alters a score or result file).
- `backend/scripts/bench.py`: `review` sub-command (writes `REVIEW.md`).
- `Makefile`: `make bench-review [DIR=bench-x]`.
- `backend/tests/bench/test_bench_kit.py`: one new test.
- `docs/MODEL_SELECTION.md`, `docs/decisions/0006-m1-model-and-provider.md`, `docs/TESTING.md`: corrected 4B finding, new decision rule and next measurement.
- `docs/history/CHANGES_M1a.md`: the previous CHANGES.md.

## Executed (sandbox) / not executed
| Check | Result | Where |
|---|---|---|
| Full SQLite backend suite | **682 passed, 28 skipped** (was 681; +1 review-sheet test) | my sandbox |
| Postgres + pgvector suite | **710 passed** (was 709) | my sandbox |
| Bench tests alone | 39 passed | my sandbox |
| `make bench-review` against a real model's output | **not run** (needs your results); the renderer is unit-tested on synthetic rows | your laptop |

## Run on your laptop
```bash
unzip -o supportgraph-m1a2.zip
make test-fast                                   # expect: 682 passed, 28 skipped
make bench-experiment MODELS="qwen3:1.7b qwen3:4b-instruct-2507-q4_K_M" TAG=cmp SETS="llm_temperature=0.0" SPLITS="dev" SUITES="faq triage"
make bench-review DIR=bench-cmp
```
Send `evals/bench-cmp/SUMMARY.md` and, if you can, your verdicts from `evals/bench-cmp/REVIEW.md` (just list which numbers are correct paraphrases).
