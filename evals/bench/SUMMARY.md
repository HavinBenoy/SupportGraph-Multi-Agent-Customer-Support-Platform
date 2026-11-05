# Benchmark summary

Machine: 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz | 4C/8T | RAM 15.4 GB (free 3.6) | load before run 4.29

Run(s): bced09d9, f96caa6d | schema 2 | splits ['dev', 'heldout'] | limit per split None | context: lexical_top_k
Ollama 0.40.14; model digests: nomic-embed-text:latest=0a109f422b47, qwen3:1.7b=8f68893c685c, qwen3:4b=359d7dd4bcda

## FAQ answering: dev and held-out are separate; the full text of every answer is in the JSON

| model | split | answerable (ctx-sufficient / gap) | correct, all answerable | correct, ctx-sufficient | over-abstain raw | over-abstain corrected | truncated | bad citations | must-abstain correct | total p50 / p95 (s) | tok/s p50 | cold start (s) | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b | dev | 23 (22 / 1) | 70% | 73% | 9% | 5% | 0/31 | 0% | 100% (n=8) | 2.85 / 4.78 | 26.4 | 3.00 | 0 |
| qwen3:1.7b | heldout | 30 (23 / 7) | 53% | 70% | 13% | 4% | 0/37 | 10% | 100% (n=7) | 2.71 / 4.34 | 25.9 | 3.00 | 0 |
| qwen3:4b | dev | 23 (22 / 1) | 17% | 18% | 9% | 5% | 19/31 | 0% | 75% (n=8) | 38.13 / 42.33 | 11.8 | 20.41 | 0 |
| qwen3:4b | heldout | 30 (23 / 7) | 13% | 17% | 20% | 0% | 25/37 | 3% | 71% (n=7) | 36.76 / 39.68 | 11.9 | 20.41 | 0 |

### FAQ failure taxonomy (one label per case)

| model | split | ok | truncated | retrieval_gap | over_abstain | mixed_output | bad_citation | no_citation | missing_fact | answered_unanswerable | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b | dev | 24 | 0 | 1 | 1 | 0 | 0 | 0 | 5 | 0 | 0 |
| qwen3:1.7b | heldout | 23 | 0 | 7 | 1 | 0 | 2 | 1 | 3 | 0 | 0 |
| qwen3:4b | dev | 10 | 19 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| qwen3:4b | heldout | 9 | 25 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Triage: routes come from the real route node

| model | split | cases | route accuracy | false escalations | missed escalations | own-records flag | category (secondary) | unusable | confidence p50 | below min conf | latency p50 (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b | dev | 57 | 77% | 0/51 (0%) | 1/6 (17%) | 67% (n=12) | 22% (n=49) | 0% | 0.80 | 9% | 1.39 |
| qwen3:1.7b | heldout | 62 | 77% | 1/55 (2%) | 1/7 (14%) | 100% (n=13) | 19% (n=54) | 0% | 0.80 | 18% | 1.36 |
| qwen3:4b | dev | 57 | 81% | 3/51 (6%) | 0/6 (0%) | 75% (n=12) | 33% (n=49) | 4% | 0.90 | 2% | 2.42 |
| qwen3:4b | heldout | 62 | 73% | 6/55 (11%) | 1/7 (14%) | 54% (n=13) | 28% (n=54) | 2% | 0.90 | 0% | 2.36 |

## Embeddings

| model | split | dim | index (s) | query p50 (s) | hit@1 | hit@4 | score gap |
|---|---|---|---|---|---|---|---|
| nomic-embed-text | dev | 768 | 3.2 | 0.020 | 96% | 100% | overlap: score distributions overlap: a similarity threshold alone cannot separate them; keep the model-side abstention |
| nomic-embed-text | heldout | 768 | 2.6 | 0.021 | 87% | 100% | overlap: score distributions overlap: a similarity threshold alone cannot separate them; keep the model-side abstention |

---
Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge). A reply cut at the token limit counts as a failure. See docs/BENCHMARKS.md for how to read each column.
