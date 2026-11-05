# Benchmark summary

Machine: 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz | 4C/8T | RAM 15.4 GB (free 3.0) | load before run 4.12

Run(s): e5e21e08 | schema 2 | splits ['dev'] | limit per split 20 | context: lexical_top_k
Ollama 0.40.14; model digests: qwen3:4b=359d7dd4bcda

## FAQ answering: dev and held-out are separate; the full text of every answer is in the JSON

| model | split | answerable (ctx-sufficient / gap) | correct, all answerable | correct, ctx-sufficient | over-abstain raw | over-abstain corrected | truncated | bad citations | must-abstain correct | total p50 / p95 (s) | tok/s p50 | cold start (s) | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b [samp4b] | dev | 20 (20 / 0) | 10% | 10% | 10% | 10% | 16/20 | 5% | - (n=0) | 38.42 / 42.20 | 11.8 | - | 0 |

### FAQ failure taxonomy (one label per case)

| model | split | ok | truncated | retrieval_gap | over_abstain | mixed_output | bad_citation | no_citation | missing_fact | answered_unanswerable | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b [samp4b] | dev | 2 | 16 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 |

---
Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge). A reply cut at the token limit counts as a failure. See docs/BENCHMARKS.md for how to read each column.
