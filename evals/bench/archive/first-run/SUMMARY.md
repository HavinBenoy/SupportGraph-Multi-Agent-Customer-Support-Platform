# Benchmark summary

Machine: 11th Gen Intel(R) Core(TM) i5-11300H @ 3.10GHz | 4C/8T | RAM 15.4 GB (free 5.5) | load before run 3.73

## FAQ answering (fixed context, same prompts as the product)

| model | answerable correct | over-abstain | bad citations | must-abstain correct | TTFT p50 (s) | total p50 (s) | total p95 (s) | tok/s p50 | cold start (s) | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:1.7b | 60% (n=53) | 11% | 6% | 100% (n=15) | 1.75 | 2.67 | 4.36 | 27.2 | 3.79 | 0 |
| qwen3:4b | 66% (n=53) | 15% | 2% | 87% (n=15) | 4.22 | 37.47 | 40.09 | 11.9 | 20.17 | 0 |

## Triage (structured output)

| model | category accuracy | unusable output | latency p50 (s) | p95 (s) |
|---|---|---|---|---|
| qwen3:1.7b | 20% | 0% | 1.39 | 1.88 |
| qwen3:4b | 30% | 1% | 2.37 | 3.04 |

## Embeddings

| model | dim | index (s) | query p50 (s) | hit@1 | hit@4 | score gap |
|---|---|---|---|---|---|---|
| nomic-embed-text | 768 | 4.1 | 0.020 | 91% | 100% | overlap: score distributions overlap: a similarity threshold alone cannot separate them; keep the model-side abstention |

---
Speeds are measured on this machine only. Quality scores are on synthetic data with deterministic checks (no LLM judge).
