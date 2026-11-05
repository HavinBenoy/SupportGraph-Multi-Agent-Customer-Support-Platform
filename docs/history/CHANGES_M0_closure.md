# CHANGES: M0 closure (trustworthy, reproducible benchmark measurements)

This is a **delta ZIP**: only added and modified files, at their project-relative paths. Extract over your project (`unzip -o`). It touches no `.env`, no
credentials, no database, no migration, no prompt, no model setting and no retrieval or product graph code. No new dependency. The previous CHANGES.md is kept
as `docs/history/CHANGES_M0_initial.md`.

Scope was exactly the measurement layer you approved. Nothing here changes how the product answers; it changes what the benchmark records and how it scores.
**Benchmark results from your first run are not compatible** (they lack the answer text, `done_reason`, confidence and manifest). `bench-report` refuses them
and prints the commands to move them aside; re-run the benchmarks.

## Amendment (reproducibility manifest and comparability warnings)
Narrow change to `backend/app/bench/report.py` plus tests and docs. No prompt, model configuration, retrieval, product logic, schema, migration or `.env` change.
1. `bench_code_sha256` now covers `metrics.py`, `llm_bench.py`, `corpus.py`, `report.py`, `stats.py`, `embed_bench.py` and `scripts/bench.py`.
2. `manifest_conflicts()` (and a detailed `manifest_conflict_details()`) now detect differences in: Ollama version; digest of the same model tag; every effective
   setting; role-specific model overrides; package versions; hardware (CPU, cores, threads, RAM, architecture) when results from different machines are combined;
   plus the earlier dataset, documents, prompts, context mode, limit, splits.
3. `SUMMARY.md` opens with a grouped **WARNING ... Do not compare or combine them.** block (model/runtime/settings, benchmark configuration, hardware) with the
   exact values and which result each came from. Same-run results produce no warning; stt/tts files without a manifest are ignored.
Existing result files remain schema 2; they lack the new code-file hashes only if produced before this amendment, which will show as a "benchmark code" difference: re-run.

**Executed in my sandbox after the amendment:** bench tests 36 passed (7 new); full SQLite suite **658 passed, 28 skipped**; Postgres + pgvector suite **686 passed**.
Not run: frontend tests, real Ollama/models (cannot reach yours). New expected counts on your laptop: `make test` 658 passed, 28 skipped; `make test-pg` 686 passed.
Extra file changed vs. the list below: none beyond `report.py`, `test_bench_kit.py`, `docs/BENCHMARKS.md`, `CHANGES.md`.

## What I executed, and what you still need to run

| Check | Result | Where |
|---|---|---|
| Fast suite (SQLite) | **658 passed, 28 skipped** (was 631 + 28; +27 new bench tests) | my sandbox |
| Postgres + pgvector suite | **686 passed** (was 659; +27) | my sandbox (Postgres 16) |
| Bench tests incl. truncation (`done_reason == "length"`) through the real `langchain-ollama` client against a mock Ollama, dev/held-out kept separate, route metrics via the real route node | **36 passed** | my sandbox |
| `bench.py llm / embed / report` end to end against the mock | ran; JSON has schema 2, manifest, per-split blocks, full text; summary rendered | my sandbox |
| Frontend tests / build / e2e | untouched; **not re-run** | - |
| Real model behaviour (qwen3:4b, qwen3:1.7b) and every new metric on real outputs | **never executed: I cannot reach your Ollama** | **your laptop** |

The mock is not a model: its numbers (20 tok/s, 100% unusable triage) prove the plumbing only.

## Changes by requirement

**1. Complete LLM outputs** (`app/bench/llm_bench.py`)
- Every FAQ row stores `text`, `done_reason`, `truncated`, `question`, `context_ids`, token counts, provider timing counters (load, prompt-eval, eval, total), the model name the
  provider reported, and (per result) the generation options of the client that was used (`num_ctx`, `num_predict`, `temperature`, `reasoning`, `keep_alive`, ...). Synthetic data only.
- `done_reason == "length"` (also OpenAI-style `finish_reason`) => `truncated`; a truncated row **never passes**, answerable or must-abstain, and is labelled `truncated`.
- `cold_start` now keeps the reply text, `done_reason` and token count of the "single word: ready" request.

**2. Triage measurements** (`app/bench/metrics.py`, `llm_bench.py`)
- Rows record `confidence`, `wants_own_records`, `priority`, parsed category, the chosen route and reason. (The raw JSON text is not available: structured output is parsed inside LangChain.)
- **Routes come from the support graph's actual `route` node**: `metrics.get_route_fn` extracts the real closure from `build_support_graph` (so `decide_route`, the escalation floor and your
  `SG_TRIAGE_MIN_CONFIDENCE` apply exactly as in production); it touches no model or database and raises, rather than approximating, if the node cannot be reached. A test compares it with
  `decide_route` on varied cases, checks the floor, and checks the confidence setting is honoured.
- New metrics per split: route accuracy over **all** tickets in the split (the previous run scored only the 103 tickets that have an expected category), false escalations (n / non-escalation tickets),
  missed escalations (n / escalation tickets) with the ids, route confusion matrix, confidence distribution, share below the clarify threshold. Category accuracy remains as a secondary number.
- **`own_records_ok` fixed.** Expectation is derived from existing dataset labels (no dataset edit): True for `expected_route == action` and for anonymous own-record requests tagged
  `needs_sign_in` (T143: the model saying True is correct and used to be scored wrong); False for record-topic policy questions; not scored otherwise (vague signed-in requests T037/T142,
  make-up requests T056/T150, non-record topics). The scored count is printed next to the accuracy.

**3. FAQ evaluation** (`metrics.py`, `corpus.py`)
- **Dev and held-out are separate** everywhere: result files hold a `splits` block per split; the summary prints one row per model per split; nothing is merged. `--splits dev heldout` (default both).
- **Retrieval gap:** `corpus.context_check` tests whether the case's `expected_facts` (same normaliser as before) are in the context given to the model. `retrieval_gap` applies **only to answerable
  questions** whose facts are missing. A correct refusal of a must-abstain question is `ok`, never a gap (tested).
- **Taxonomy** (one label per case, checked in this order): `truncated`, `answered_unanswerable`, `retrieval_gap`, `over_abstain`, `mixed_output`, `bad_citation`, `no_citation`, `missing_fact`, `other`,
  plus `ok`. Mapped to failure classes: retrieval, generation, citation, incorrect.
- **Over-abstain** is reported two ways: `raw` (the original definition) and `corrected` (genuine refusals where the context held the answer / context-sufficient cases). Likewise `correct_rate`
  (all answerable, original denominator) and `correct_rate_context_sufficient`. A 95% Wilson interval for the former is stored.
- **Criteria preserved:** answered, cited, every cited id in context, every expected fact present. The only addition is the documented truncation rule. No paraphrase matching was added.

**4. Reproducibility** (`report.py`, `bench.py`, `stats.py`)
- Every result carries `schema_version: 2` and a manifest: run id, command line, splits, tickets-per-split limit, context mode, SHA-256 of both ticket files, tenant documents (per file and combined),
  FAQ and triage prompt text (read only), the benchmark code; effective settings (no keys), per-role models, endpoint host, Ollama version, **model digests**, package versions.
- `bench-report` refuses result files without schema 2 (naming them, with the `mkdir`/`mv` command) and prints a WARNING if the summarised files differ in dataset, documents, prompts, context mode,
  benchmark code or limit.
- `bench embed` also reports per split (the benchmark of the embedder itself is unchanged).
- `--heldout` is no longer needed (both splits run by default); it is accepted and ignored.

## Blocking prerequisite for M1 (recorded, not implemented)
Before a real LLM is connected to the product FAQ path, `validate` in `app/rag/faq.py` must reject any response with `done_reason == "length"` or otherwise known to be truncated. Today it accepts any
reply with a valid citation. Recorded in `docs/ROADMAP.md`. **Not changed here**, per your instruction.

## File list
Modified: `backend/app/bench/llm_bench.py`, `backend/app/bench/corpus.py`, `backend/app/bench/report.py`, `backend/app/bench/stats.py`, `backend/scripts/bench.py`,
`backend/tests/bench/test_bench_kit.py`, `backend/tests/bench/mock_ollama.py`, `CHANGES.md`, `docs/ROADMAP.md`.
Added: `backend/app/bench/metrics.py`, `backend/tests/bench/test_bench_metrics.py`, `docs/BENCHMARKS.md`, `docs/history/CHANGES_M0_initial.md`.
Deleted: none. Not touched: prompts, gateway, `config.py`, RAG/retrieval, graph, API, datasets, database, migrations, `.env`, `Makefile`, `pyproject.toml`.
Dependencies / environment: none added or changed.

## Run it on your laptop
```bash
cd ~/your-project && unzip -o supportgraph-m0-closure.zip
make test                          # expect: 658 passed, 28 skipped (same count as my sandbox)
make db-up && make test-pg         # expect: 686 passed
mkdir -p evals/bench/archive && mv evals/bench/*.json evals/bench/SUMMARY.md evals/bench/archive/ 2>/dev/null   # move first-run (schema 1) files aside
make bench-check                   # expect: Ollama reachable, no 'missing' lines
# Close heavy apps (Docker/Langfuse, browsers) and plug in the charger first; the load average is recorded.
SG_LLM_PROVIDER=ollama SG_EMBEDDING_PROVIDER=ollama make llm-smoke
make bench-llm MODELS="qwen3:1.7b qwen3:4b" ARGS="--limit 10"        # quick pass: 10 tickets per split. Check the output first.
make bench-llm MODELS="qwen3:1.7b qwen3:4b"                          # full run, both splits. SLOW: if qwen3:4b still hits the token limit this is roughly an hour or more
make bench-embed
make bench-report                  # prints evals/bench/SUMMARY.md
```
If `make test` shows a different count, send me the tail of the output. If `bench-report` says result files are incompatible, the `mv` line above was skipped.

## Expected output and how to read it
`SUMMARY.md` begins with run id(s), splits, limit, context mode, Ollama version and model digests, then:
- **FAQ table** (one row per model per split) and a **taxonomy table**: counts of `ok`, `truncated`, `retrieval_gap`, `over_abstain`, ... that sum to the cases run in that split.
- **Triage table**: route accuracy, false and missed escalations, own-records flag, category (secondary), unusable, confidence.
- **Embeddings** per split.
Expect, if the earlier behaviour holds: `truncated` counts high for qwen3:4b (it hit the 400-token limit on 45 of 68 calls before), so its FAQ "correct" will fall below the 66% reported earlier;
that is the new rule working, not a regression. Expect `retrieval_gap` counts around 8 over both splits (the cases whose facts the lexical context lacked). Read `docs/BENCHMARKS.md` for every column.
To see what a model actually wrote for any failure, use the one-liner in `docs/BENCHMARKS.md`.

Cannot tell from the mock, and unmeasured until you run it: whether truncation persists, real route accuracy, real false/missed escalation rates. I will not draw conclusions until you send the results.
