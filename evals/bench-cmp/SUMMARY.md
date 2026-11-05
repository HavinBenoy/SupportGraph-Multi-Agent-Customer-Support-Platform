# Benchmark summary

Machine: 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz | 4C/8T | RAM 15.4 GB (free 2.6) | load before run 3.87

Run(s): 746d4a14 | schema 2 | splits ['dev'] | limit per split None | context: lexical_top_k
Ollama 0.40.14; model digests: qwen3:1.7b=8f68893c685c, qwen3:4b-instruct-2507-q4_K_M=0edcdef34593

## FAQ answering: dev and held-out are separate; the full text of every answer is in the JSON

| model | split | answerable (ctx-sufficient / gap) | correct, all answerable | correct, ctx-sufficient | over-abstain raw | over-abstain corrected | truncated | bad citations | must-abstain correct | total p50 / p95 (s) | tok/s p50 | cold start (s) | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b [cmp] | dev | 23 (22 / 1) | 70% | 73% | 9% | 5% | 0/31 | 0% | 100% (n=8) | 2.87 / 5.74 | 26.7 | - | 0 |
| qwen3:4b-instruct-2507-q4_K_M [cmp] | dev | 23 (22 / 1) | 83% | 86% | 4% | 0% | 0/31 | 0% | 100% (n=8) | 4.38 / 8.19 | 12.9 | - | 0 |

### FAQ failure taxonomy (one label per case)

| model | split | ok | truncated | retrieval_gap | over_abstain | mixed_output | bad_citation | no_citation | missing_fact | answered_unanswerable | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b [cmp] | dev | 24 | 0 | 1 | 1 | 0 | 0 | 0 | 5 | 0 | 0 |
| qwen3:4b-instruct-2507-q4_K_M [cmp] | dev | 27 | 0 | 1 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |

## Triage: routes come from the real route node

| model | split | cases | route accuracy | false escalations | missed escalations | own-records flag | category (secondary) | unusable | confidence p50 | below min conf | latency p50 (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b [cmp] | dev | 57 | 77% | 0/51 (0%) | 1/6 (17%) | 67% (n=12) | 22% (n=49) | 0% | 0.80 | 9% | 1.35 |
| qwen3:4b-instruct-2507-q4_K_M [cmp] | dev | 57 | 79% | 6/51 (12%) | 0/6 (0%) | 100% (n=12) | 35% (n=49) | 2% | 0.95 | 4% | 1.99 |

---
Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge). A reply cut at the token limit counts as a failure. See docs/BENCHMARKS.md for how to read each column.
