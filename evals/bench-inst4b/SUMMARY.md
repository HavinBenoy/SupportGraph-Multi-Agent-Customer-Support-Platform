# Benchmark summary

Machine: 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz | 4C/8T | RAM 15.4 GB (free 3.3) | load before run 3.87

Run(s): ffab59fd | schema 2 | splits ['dev'] | limit per split 20 | context: lexical_top_k
Ollama 0.40.14; model digests: qwen3:4b-instruct-2507-q4_K_M=0edcdef34593

## FAQ answering: dev and held-out are separate; the full text of every answer is in the JSON

| model | split | answerable (ctx-sufficient / gap) | correct, all answerable | correct, ctx-sufficient | over-abstain raw | over-abstain corrected | truncated | bad citations | must-abstain correct | total p50 / p95 (s) | tok/s p50 | cold start (s) | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b-instruct-2507-q4_K_M [inst4b] | dev | 20 (20 / 0) | 85% | 85% | 0% | 0% | 0/20 | 0% | - (n=0) | 6.57 / 9.53 | 12.9 | - | 0 |

### FAQ failure taxonomy (one label per case)

| model | split | ok | truncated | retrieval_gap | over_abstain | mixed_output | bad_citation | no_citation | missing_fact | answered_unanswerable | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:4b-instruct-2507-q4_K_M [inst4b] | dev | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |

---
Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge). A reply cut at the token limit counts as a failure. See docs/BENCHMARKS.md for how to read each column.
