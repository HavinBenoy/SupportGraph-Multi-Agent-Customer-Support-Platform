# CHANGES: M1a-4 (FAQ default reverted to qwen3:1.7b after the held-out confirmation)

Small delta on top of m1a / m1a2 / m1a3. No `.env`, schema, migration, prompt or retrieval change. Previous notes: `docs/history/CHANGES_M1a3.md`.

## Why
The rule written before the held-out run: revert if the 4B instruct model shows truncation, must-abstain correct below 1.7b's 100%, or correctness below 1.7b's 53%.
Result (held-out, run once): truncation 0/37 (ok), correct 63% (ok), **must-abstain 6/7 (fails)**. It answered one unanswerable question, so FAQ goes back to the 1.7b.
Triage held-out for the 4B: 8/55 false escalations, which also confirms 1.7b for triage. Full numbers and caveats: `docs/MODEL_SELECTION.md`.

## Changed
- `backend/app/config.py`: `llm_model` default `qwen3:1.7b`; `llm_model_triage` default empty (= `llm_model`).
- `.env.example`, `README.md`: model lines; the 4B instruct is documented as an opt-in with the two env vars to set.
- `backend/tests/unit/test_llm_gateway_hardening.py`: default-model test updated (and checks the opt-in config resolves per role).
- `docs/MODEL_SELECTION.md`, `docs/decisions/0006-m1-model-and-provider.md`: decision, evidence, what would bring the 4B back.
- `docs/history/CHANGES_M1a3.md`: previous notes. The `settings_for_model` benchmark fix from m1a3 stays.
- **Your `.env`:** if it sets `SG_LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M` or `SG_LLM_MODEL_TRIAGE`, change or remove them to follow the new defaults.

## Executed (sandbox) / not executed
| Check | Result | Where |
|---|---|---|
| Full SQLite backend suite | **684 passed, 28 skipped** (same count; one test rewritten) | my sandbox |
| Postgres + pgvector suite | **712 passed** | my sandbox |
| Any real model | **not run by me** (the held-out numbers above are yours) | - |

## Run on your laptop
```bash
unzip -o supportgraph-m1a4.zip
grep -n "SG_LLM_MODEL" .env        # should be qwen3:1.7b or unset
make test-fast                     # expect: 684 passed, 28 skipped
```
