# CHANGES: M1a-3 (default models decided from the full-dev comparison)

Small delta on top of `supportgraph-m1a.zip` and `supportgraph-m1a2.zip`. No `.env`, schema, migration, prompt or retrieval change. Previous notes: `docs/history/CHANGES_M1a2.md`.

## Decision (evidence and caveats: docs/MODEL_SELECTION.md)
- FAQ and every role except triage: `qwen3:4b-instruct-2507-q4_K_M`. Triage: `qwen3:1.7b`. Embeddings unchanged. One held-out confirmation run is still to do (below); revert with `SG_LLM_MODEL` if it fails.
- Full dev, same settings: FAQ correct 83% vs 70%, 0 truncated, must-abstain 100% both (n=8), p50 4.4 s vs 2.9 s. Triage: 4B 6/51 false escalations vs 0/51 for 1.7b; missed 0/6 vs 1/6.

## Changed
- `backend/app/config.py`: `llm_model` default = the 4B instruct build; `llm_model_triage` default = `qwen3:1.7b`.
- **Bug found and fixed while doing this** (`backend/app/bench/llm_bench.py`): with a per-role default, `bench.py llm --models X` would have benchmarked the triage role on the role default instead of X. `settings_for_model()` now clears all per-role overrides for the model under test (FAQ, triage and cold start). Your earlier runs were not affected (no triage default existed then).
- `backend/scripts/bench.py`: default model list now the instruct build + 1.7b (plain `qwen3:4b` is the thinking-only build).
- `.env.example`, `README.md`: model names and pull commands. **Check your `.env`**: any `SG_LLM_MODEL` / `SG_LLM_MODEL_TRIAGE` you set still wins.
- Tests: default-model test (`tests/unit/test_llm_gateway_hardening.py`), role-override benchmark test (`tests/bench/test_bench_kit.py`).
- Docs: `docs/MODEL_SELECTION.md`, `docs/decisions/0006-m1-model-and-provider.md`; `docs/history/CHANGES_M1a2.md` = previous notes.

## Executed (sandbox) / not executed
| Check | Result | Where |
|---|---|---|
| Full SQLite backend suite | **684 passed, 28 skipped** (was 682; +2 tests) | my sandbox |
| Postgres + pgvector suite | **712 passed** (was 710) | my sandbox |
| Bench tests alone | 40 passed | my sandbox |
| Held-out confirmation, any real model | **not run** (needs your Ollama) | your laptop |

## Run on your laptop
```bash
unzip -o supportgraph-m1a3.zip
ollama pull qwen3:4b-instruct-2507-q4_K_M && ollama pull qwen3:1.7b      # both already present on your machine
grep -n "SG_LLM_MODEL" .env                                             # remove or update stale values
make test-fast                                                          # expect: 684 passed, 28 skipped
make bench-experiment MODELS="qwen3:4b-instruct-2507-q4_K_M" TAG=heldout4b SETS="llm_temperature=0.0" SPLITS="heldout" SUITES="faq triage"
make bench-review DIR=bench-heldout4b
```
Send `evals/bench-heldout4b/SUMMARY.md`. Revert rule is in docs/MODEL_SELECTION.md section 5.
