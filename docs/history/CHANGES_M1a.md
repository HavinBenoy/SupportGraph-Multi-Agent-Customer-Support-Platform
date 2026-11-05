# CHANGES: M1a (truncation-safe text path, model decision, faster development loop)

**Delta ZIP**: added and modified files only, at their project-relative paths. Extract over your project (`unzip -o`). No `.env`, credentials, database, migration or
schema change. One dependency added to the `dev` extra: `pytest-xdist` (optional; `make test-fast` runs serially without it). The previous CHANGES.md is kept as
`docs/history/CHANGES_M0_closure.md`.

Read first: **`docs/MODEL_SELECTION.md`** (decisions, evidence labels, the experiment to run) and `docs/TESTING.md`.

## Decisions (details and evidence in docs/MODEL_SELECTION.md and ADR 0006)
1. qwen3:1.7b is the working default for every role (config default changed from qwen3:4b). Suitable as a *safe* default, not a final answer.
2. qwen3:4b is not the default. A cheap diagnostic decides whether it is revisited: Qwen documents sampling (not greedy) as the recommended mode, and our gateway hard-coded temperature 0. **Hypothesis, untested.**
3. `nomic-embed-text` stays. Everything stays local. No hosted provider adopted (Groq limits are too small for a service; Cerebras limits I could not verify; HF/Colab unsuitable).
4. M1 is split: **M1a (this ZIP) is done; M1b (enable the real-model path in the product) waits for your experiment results**, because I cannot run your models and will not tune prompts blind.

## What changed
- **Blocking prerequisite met.** `app/llm/truncation.py` (new): `finish_reason` / `is_truncated` (Ollama `done_reason`, OpenAI-style `finish_reason`, `stop_reason`; "length" / "max_tokens"; and,
  only when no reason is reported, output tokens >= `SG_LLM_MAX_OUTPUT_TOKENS`; an explicit "stop" is trusted). `app/rag/faq.py`: `generate` records it, `validate` rejects first with reason
  `truncated_answer` (a valid citation never rescues it; the student gets the standard abstention). `app/qa/critic.py`: a truncated QA revision is discarded.
- **Sampling is configurable** (`app/config.py`, `app/llm/gateway.py`): `SG_LLM_TEMPERATURE` (default 0.0 = unchanged), `SG_LLM_TOP_P`, `SG_LLM_TOP_K`, `SG_LLM_REPEAT_PENALTY`, `SG_LLM_SEED`; only explicitly set options are sent. Added to the benchmark manifest, so the comparability WARNING covers them.
- **Benchmark experiment tooling** (`scripts/bench.py`, `app/bench/report.py`): `--suites`, `--no-cold-start`, `--tag`, `--set KEY=VALUE` (generation keys only), global `--out-dir`; variants never overwrite baselines; tables label variants.
- **Faster loop**: `make test-smoke | test-fast | test-targeted | test-full | bench-quick | bench-full | bench-experiment`, `backend/scripts/test_select.py` (change-scope selector; unknown backend files mean the full suite). `make test` / `make test-pg` unchanged.
- Docs: `docs/MODEL_SELECTION.md`, `docs/TESTING.md`, `docs/decisions/0006-m1-model-and-provider.md`, `docs/BENCHMARKS.md`, `docs/ROADMAP.md`, README model line, `.env.example` (default model + commented sampling keys; your `.env` is untouched: **if it sets `SG_LLM_MODEL=qwen3:4b`, that still wins**).

## What I executed (sandbox) and what I did not
| Check | Result | Where |
|---|---|---|
| Full SQLite backend suite | **681 passed, 28 skipped** (was 658 + 28; +23 new tests: 12 truncation, 9 selector, 2 experiment-override) | my sandbox |
| Postgres 16 + pgvector suite (`make test-pg`) | **709 passed** (was 686) | my sandbox |
| `make test-fast` (parallel, 2 cores) | 681 passed, 28 skipped in 57 s | my sandbox |
| `make test-smoke` | 185 passed in ~17 s | my sandbox |
| Truncation through the real `langchain-ollama` client and gateway against a mock Ollama that never stops | rejected as `truncated_answer` | my sandbox |
| `bench.py llm` with `--set/--tag/--suites/--no-cold-start/--out-dir` + `report` against the mock | ran; variant file kept separate; WARNING block fired for differing temperature/top_k | my sandbox |
| Frontend tests / build / e2e, Langfuse | untouched; **not re-run** | - |
| Any real model (qwen3:1.7b / 4b), the sampling hypothesis, and every provider claim on a live account | **never executed: I cannot reach your Ollama or any provider account** | **your laptop** |

## Run it on your laptop
```bash
cd ~/your-project && unzip -o supportgraph-m1a.zip
.venv/bin/pip install -e "backend[dev]"        # adds pytest-xdist (optional)
make test-smoke                                 # expect: 185 passed in seconds
make test                                       # expect: 681 passed, 28 skipped
make db-up && make test-pg                      # expect: 709 passed
grep -n SG_LLM_MODEL .env                       # check it is qwen3:1.7b (or unset)
# The experiment (docs/MODEL_SELECTION.md section 5). Close heavy apps, plug in the charger first.
make bench-experiment MODELS="qwen3:1.7b" TAG=samp17 SETS="llm_temperature=0.7 llm_top_p=0.8 llm_top_k=20" N=20
make bench-experiment MODELS="qwen3:4b"   TAG=samp4b SETS="llm_temperature=0.7 llm_top_p=0.8 llm_top_k=20" N=20
```
Then send `evals/bench-samp17/SUMMARY.md` and `evals/bench-samp4b/SUMMARY.md`. (A `git diff --check` before committing is still worth running; I scanned the files in this ZIP for trailing whitespace and removed the one in `tests/bench/test_bench_kit.py` that you had fixed locally.)

## Security and privacy
No new network path. External endpoints remain refused unless `SG_ALLOW_EXTERNAL_LLM=true`. Truncated text can no longer reach a person. No key, credential or student data was added; the benchmark and experiments use synthetic data only.

## Limits
Nothing here shows the sampling hypothesis is true; the 1.7B default rests on the M0 numbers (small n, deterministic exact-phrase scoring, lexical context, load ~4.3, held-out now seen). Triage prompt/design changes are proposed in MODEL_SELECTION.md section 3 but deliberately not implemented. The frontend, e2e, Langfuse and real models were not run by me.
