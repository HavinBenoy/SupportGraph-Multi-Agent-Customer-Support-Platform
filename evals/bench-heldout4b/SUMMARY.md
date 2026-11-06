# Benchmark summary

Machine: 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz | 4C/8T | RAM 15.4 GB (free 6.1) | load before run 4.07

Run(s): ab9ca310 | schema 2 | splits ['heldout'] | limit per split None | context: lexical_top_k
Ollama 0.40.14; model digests: qwen3:4b-instruct-2507-q4_K_M=0edcdef34593

## FAQ answering: dev and held-out are separate; the full text of every answer is in the JSON

| model | split | answerable (ctx-sufficient / gap) | correct, all answerable | correct, ctx-sufficient | over-abstain raw | over-abstain corrected | truncated | bad citations | must-abstain correct | total p50 / p95 (s) | tok/s p50 | cold start (s) | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b-instruct-2507-q4_K_M [heldout4b] | heldout | 30 (23 / 7) | 63% | 83% | 17% | 0% | 0/37 | 0% | 86% (n=7) | 5.19 / 8.27 | 13.2 | - | 0 |

### FAQ failure taxonomy (one label per case)

| model | split | ok | truncated | retrieval_gap | over_abstain | mixed_output | bad_citation | no_citation | missing_fact | answered_unanswerable | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b-instruct-2507-q4_K_M [heldout4b] | heldout | 25 | 0 | 7 | 0 | 0 | 0 | 0 | 4 | 1 | 0 |

## Triage: routes come from the real route node

| model | split | cases | route accuracy | false escalations | missed escalations | own-records flag | category (secondary) | unusable | confidence p50 | below min conf | latency p50 (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b-instruct-2507-q4_K_M [heldout4b] | heldout | 62 | 82% | 8/55 (15%) | 0/7 (0%) | 100% (n=13) | 28% (n=54) | 0% | 0.95 | 5% | 2.03 |

---
Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge). A reply cut at the token limit counts as a failure. See docs/BENCHMARKS.md for how to read each column.
