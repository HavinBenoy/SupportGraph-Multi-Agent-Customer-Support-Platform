# M1b - real local AI integration (delta on top of m1a6)

Model choices are NOT touched (FAQ qwen3:1.7b, triage qwen3:1.7b, embeddings nomic-embed-text, Ollama, CPU). No benchmark, no triage redesign.
No migration, no new dependency (httpx is already installed; langchain-ollama is in the `ollama` extra). `.env` is never read for writing; your database and volumes are untouched.

## What changed
- `app/llm/preflight.py` (new): startup check (server reachable, every chat/embedding model pulled, external endpoints refused) with `SG_LLM_PREFLIGHT=off|warn|strict` (default warn), and background `warm_up` (`SG_LLM_WARMUP`, default true; only for the Ollama provider and only when the API builds its own graph).
- `app/api/main.py`: lifespan runs preflight and starts the warm-up thread.
- `app/llm/gateway.py`: Nomic task prefixes (`search_query: ` / `search_document: `) added automatically for `nomic-embed-text` on a real provider (`SG_EMBEDDING_PREFIX_MODE=auto|none`; explicit prefixes win). The setting is part of the stored embedding name (`...@px-xxxxxx`), so old vectors are ignored, never mixed. Fake embedder name unchanged. `get_critic` / `get_rewriter` honour new flags.
- `app/config.py`: `embedding_prefix_mode`, `qa_llm_critic` (default false), `rewriter_mode` (glossary), `llm_preflight`, `llm_warmup`.
- `app/llm/reasoning.py` + `app/graph/builder.py` + `app/rag/rewrite.py`: reasoning is removed for EVERY role (action agent and query rewriter too, not only FAQ); an unfinished `<think>` yields no answer.
- `app/llm/guard.py`: banner shows embedding provider/prefix state and critic/rewriter state.
- `scripts/e2e_local.py` + `make e2e-local` (new); `scripts/llm_smoke.py` now runs preflight + warm-up.
- `.env.example`: comments/templates only. `docs/M1B.md` (new), `ROADMAP.md`, `TESTING.md`.
- `tests/bench/mock_ollama.py`: answers the triage JSON schema, can emit `<think>` leaks (`reasoning-leak`) and unfinished reasoning (`reasoning-unfinished`). `tests/bench/test_bench_kit.py`: one assertion updated (mock triage output is now parseable, so unusable rate 0.0).
- `pyproject.toml`: registers the `ollama` pytest marker.

## Behaviour you should know about
- Prefixes are ON by default for Nomic on a real provider. M0's Hit@1 (96% dev / 87% heldout) was measured WITHOUT prefixes. `make e2e-local` re-measures it through the real database path; if it drops by more than ~5 points set `SG_EMBEDDING_PREFIX_MODE=none` and re-run `make ingest`.
- Because the name changed you must run `make ingest` after enabling Ollama embeddings (it adds vectors; nothing is deleted).
- LLM critic and LLM rewriter stay off until measured (each adds seconds on CPU). Abstention remains model-side; `rag_min_score` is not tuned.

## Tests actually run (sandbox)
| Run | Result |
|---|---|
| Full SQLite suite `pytest -n auto` | 728 passed, 30 skipped |
| Full suite with Postgres (`SG_TEST_DATABASE_URL`, sandbox PG 16) | 756 passed, 2 skipped (the 2 opt-in real-Ollama tests) |
| New: `tests/graph/test_local_stack_mock.py` (14), `tests/unit/test_local_ai_setup.py` (11), `tests/graph/test_e2e_local_script.py` (1) | pass; real langchain-ollama clients against a MOCK Ollama |
| Real Ollama, real qwen3/nomic, real latency/memory, `make e2e-local` | **NOT run (sandbox has no models). This is your run.** |
| `git diff --check` | not available in sandbox (no git); scanned changed files for trailing whitespace: none in changed files |

The mock proves plumbing and safety behaviour (prefixes sent, reasoning stripped, truncation/unfinished -> abstain, outage -> fixed reply, escalation floor beats a wrong triage, anonymous gets no records, injection refused). It says nothing about model quality or speed.

## Verify locally
```
# 1. unit/integration (no models needed)
make test-fast
git diff --check

# 2. turn the real stack on: edit YOUR .env (do not paste secrets anywhere)
#      SG_LLM_PROVIDER=ollama
#      SG_EMBEDDING_PROVIDER=ollama
ollama pull qwen3:1.7b && ollama pull nomic-embed-text      # if not already pulled
make db-up                                                    # if not running; no reset needed
make ingest                                                   # embeds with the prefixed name; deletes nothing
make llm-smoke                                                # expect: preflight notes "available", warm-up times, chat ok, embedding ok 768 dims
make e2e-local                                                # ~3-6 min on CPU; writes evals/e2e/REPORT.md + results.json
cd backend && SG_TEST_OLLAMA=1 ../.venv/bin/python -m pytest tests/graph/test_real_ollama.py -q   # optional, real models
```
Expected from `make e2e-local`: `HARD failures: 0` and exit 0; soft rows (answer quality, own-records via tools, write proposal) are observations only. Please send me `evals/e2e/REPORT.md` (and the console output). Retrieval Hit@1 should be near 96% dev / 87% heldout.
Rollback: set both providers back to `fake` in your `.env`.

## Files
Added: `backend/app/llm/preflight.py`, `backend/scripts/e2e_local.py`, `backend/tests/graph/{test_local_stack_mock,test_e2e_local_script,test_real_ollama}.py`, `backend/tests/unit/test_local_ai_setup.py`, `docs/M1B.md`.
Modified: `.env.example`, `Makefile`, `backend/pyproject.toml`, `backend/app/{api/main,config,graph/builder,llm/gateway,llm/guard,llm/reasoning,rag/rewrite}.py`, `backend/scripts/llm_smoke.py`, `backend/tests/bench/{mock_ollama,test_bench_kit}.py`, `docs/{ROADMAP,TESTING}.md`.
Deleted: none.
Security: no secrets handled; preflight/warm-up only contact endpoints allowed by the external-endpoint gate.
