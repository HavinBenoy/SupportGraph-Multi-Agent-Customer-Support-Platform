# Test tiers and benchmark commands

Goal: the default loop is seconds, the full gate is minutes, and nothing hides a regression. `make test` and `make test-pg` are unchanged (same counts as before).
Timings below were measured on my 2-core sandbox; yours (4C/8T) should be faster with `-n auto`. They are not measured on your machine.

| Command | What it runs | Sandbox time | Does NOT cover |
|---|---|---|---|
| `make test-smoke` | security/tenant isolation, benchmark kit, FAQ validation + truncation, prompts, gateway hardening, router/triage/escalation-floor units (185 tests) | ~17 s | API, DB-backed flows beyond the FAQ tests, frontend, Postgres, real models |
| `make test-targeted` | only what your changed files need (`git status`), via `backend/scripts/test_select.py`; `DRY=1` previews, `FILES="..."` overrides | seconds to ~1 min | anything outside the rules; unknown backend files fall back to the FULL backend suite |
| `make test-fast` | whole SQLite backend suite, `-x --ff`, parallel (`-n auto`) when `pytest-xdist` is installed | ~48 s on 2 cores (76 s serial) | Postgres/pgvector, frontend, real models |
| `make test` / `make test-pg` | unchanged: full backend on SQLite / on real Postgres (+ pgvector) | ~76 s / ~85 s | frontend, real models |
| `make test-full` | `test-fast` + `test-pg` + frontend tests + production build | minutes | browser e2e (`make e2e`), real models |
| `make bench-quick` | real model, 8 tickets per split, no cold start, own folder `evals/bench-quick` | ~1-2 min for 1.7b | decisions: 8 tickets cannot separate models |
| `make bench-full` | real models, both splits, all tickets, cold start | ~6 min (1.7b); much longer for slow models | |
| `make bench-review DIR=bench-x` | writes `REVIEW.md`: failures the strict scorer cannot judge on meaning, for a human to read. Read-only, changes no score | seconds | |
| `make bench-experiment` | one controlled variant (generation settings only) in `evals/bench-<TAG>` | see MODEL_SELECTION.md | |

## Which tier when
- Editing code or prompts: `make test-targeted` (add `DRY=1` to see the plan first). For FAQ/triage prompt changes it also prints a reminder to run `make bench-quick`.
- Before commit: `make test-fast`. Before handing over / merging: `make test-full`.
- Database models or migrations: the selector adds `make test-pg`.
- Anything touching the model gateway or sampling: `make bench-quick`, then `make bench-full` only when you intend to compare models.

## Benchmark speed without hiding regressions
- `--no-cold-start` and `--suites` separate quality runs from latency runs. Cold start is measured in `bench-full` only.
- `keep_alive` is already 30 minutes, so the model is not reloaded between tickets.
- Model outputs are **never cached**: a cache would hide exactly the generation changes the benchmark exists to detect. Code-level tests use the fake/mock
  models (no model at all), which is why they are fast.
- No parallel model calls: one CPU-bound model at a time, otherwise latencies are meaningless.
- Quick and experiment runs use separate folders and the report's comparability warning (Ollama version, digests, settings, code hashes, hardware, dataset,
  prompts) fires if files from different conditions are put together.

## Adding `pytest-xdist`
`pip install -e "backend[dev]"` (it is now in the `dev` extra) or `pip install pytest-xdist`. Without it `make test-fast` simply runs serially.
