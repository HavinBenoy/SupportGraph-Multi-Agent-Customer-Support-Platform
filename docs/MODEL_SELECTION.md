# Model and provider selection (M1 review of the M0 benchmark)

Evidence is labelled: **Observed** (in your M0 results), **Documented** (a source I read this session), **Hypothesis** (plausible, not yet tested),
**Unknown**. I cannot reach your Ollama, so nothing below was measured by me; every decision is one the existing numbers support, and the
rest is an experiment you run (section 5).

## 1. Qualifications that stay attached to every number
Synthetic data; deterministic exact-phrase scoring (a correct paraphrase can fail); held-out FAQ = 30 answerable + 7 must-abstain, triage held-out = 62
tickets with 7 expected escalations; differences of a few points are not conclusive (with n=30 a 95% interval is roughly +/-17 points); latency was measured
under load average ~4.3; context was `lexical_top_k`, not the product's embedding retrieval; embeddings were benchmarked separately; results from different
limits or configurations must never be combined (the report now warns when they are). **The held-out split has now been looked at for these two models**, so
it no longer gives an unbiased choice among them: tune on dev, and treat any new held-out number as confirmation only. A fresh unseen set is needed before
a final model claim.

## 2. Decisions

| Question | Decision | Evidence |
|---|---|---|
| Is qwen3:1.7b suitable for FAQ + triage? | **As the working default, yes; as a final answer, no.** Safe (fails closed), not yet good enough. | Observed: 0 truncations, must-abstain 100% (15 cases), p50 2.7-2.9 s, but held-out correct 53% (70% when the context held the facts) and 7 held-out retrieval gaps. Triage route accuracy 77%/77%, 1 missed escalation per split. |
| qwen3:4b (the tag you pulled)? | **Not usable: it is a thinking-only build.** Dropped from the product path. | Observed (2 laptop runs + probe): with sampling it still truncated 16/20; raw answers start "Okay, let me tackle this question..."; the probe showed `think:false` and `/no_think` are both ignored (reasoning lands in the visible answer and hits the cap); `ollama show` lists a `thinking` capability and a 262,144 context. Inference: the tag points at the 2025-07 thinking refresh. My earlier hypothesis (greedy decoding causes the loop) was **wrong**: sampling changed nothing. |
| qwen3:4b-instruct-2507-q4_K_M (non-thinking)? | **Promising candidate, unproven. Default unchanged until it is measured on abstention and the full dev split.** | Observed, dev first 20 tickets, one run: correct 85% vs 1.7b 70% (a 3-ticket difference, inside the +/-20 point interval at n=20); 0/20 truncated; p50 6.6 s, p95 9.5 s; 12.9 tok/s. Those 20 tickets contain **no must-abstain cases**, so abstention safety is unmeasured for it. |
| Which model(s)? | **qwen3:1.7b for FAQ and triage for now (config default changed), `nomic-embed-text` for embeddings (unchanged).** One model keeps RAM and cold-start low on a 16 GB laptop; per-role overrides (`SG_LLM_MODEL_<ROLE>`) stay available. | Observed: 1.7b >= 4b on every FAQ metric; triage differences between the two are within noise (dev 77 vs 81%, held-out 77 vs 73%, n=51-55). Embedding Hit@4 100% on both splits. |
| Cloud (Groq / Cerebras / HF / Colab)? | **None in the product path. Not adopted.** Optional, synthetic-data-only benchmark ceiling test if you approve and supply a key (section 4). | See section 4. |
| What stays local? | Everything that touches student text or records: chat, embeddings, triage, FAQ, voice. | Privacy and the zero-cost demo requirement. |
| Architecture changes? | (a) FAQ validator now fails closed on truncation (done). (b) Sampling is configurable and recorded in the benchmark manifest (done). (c) Triage should stop asking one small model for route + category + priority in one shot (proposed, not implemented: needs a measured experiment). | See section 3. |
| Is M1 ready? | **M1a is done and shippable; M1b (switching the product's real-model path on) waits for the section 5 results.** The blocking prerequisite is satisfied. | This delivery. |

## 3. Triage: what I would change, and why I did not change it blind
Observed: category accuracy 19-33% while route accuracy is 73-81%, so the model's category is already barely used by the route decision (`decide_route`
is pure code plus the escalation floor). Hypotheses worth testing, each one a prompt change that must be measured on dev before held-out:
1. Reduce the model's job to the fields the code consumes (route intent, wants_own_records, confidence) and derive the detailed category separately or by rules.
2. Add category definitions and 3-5 few-shot examples (the current triage prompt has none).
3. Treat confidence as one input: keep `SG_TRIAGE_MIN_CONFIDENCE`; never lower the escalation floor. Deterministic rules for unambiguous cases (e.g. explicit
   "talk to a human", legal/safety terms) may only ever *add* escalations.
I did not edit the triage prompt: without a way to run the model I would be tuning blind, and the instruction is to improve the product, not the score.

## 4. Providers (researched 2026-11-05; free tiers change, re-check before relying on any)
- **Groq** (official rate-limit page): listed models `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, each 30 requests/min, 1,000/day, 8K tokens/min,
  200K tokens/day. The page labels the table both "Free" and "Developer" and says the numbers are the Developer base limits, so confirm your own limits at
  console.groq.com/settings/limits. Its data-retention terms were not on the page I could read. At 8K tokens/min a ~700-token FAQ call allows roughly ten calls
  a minute for ALL users: fine for benchmarking, too small for a persistent service.
- **Cerebras**: I could not read the official limits (the docs host was not reachable from my sandbox) and the third-party pages I found contradict each other
  (one describes a $5 time-limited trial needing a payment method). **Unknown; no recommendation.**
- **Hugging Face**: $0.10/month of Inference Providers credit on a free account ($2 on PRO), ZeroGPU ~5 GPU-minutes/day free, free CPU Spaces sleep after
  48 h (third-party summary of HF's pricing). Not viable for a persistent support service.
- **Google Colab**: free GPU is not guaranteed and sessions end; fine for a one-off experiment, not a server.
- **Conclusion:** no free hosted option shown to be both reliable and private enough for student conversations. The product stays local-first. The gateway already
  supports `openai_compat` endpoints behind `SG_ALLOW_EXTERNAL_LLM=true` (refused otherwise, startup banner says EXTERNAL). If you want a *ceiling* test (does a
  much larger model fix the FAQ failures, i.e. are they capacity or retrieval/prompt?), run `make bench-experiment` with an external base URL on **synthetic
  data only**; this needs your explicit go-ahead and your own key in `.env` (I never see it). Not enabled by this delivery.

## 5. Next measurement (replaces the earlier sampling experiment, which is finished: sampling does not matter)
Same settings for both models, full dev split including the must-abstain cases, FAQ + triage, one shared folder so the report can compare them:
```bash
make bench-experiment MODELS="qwen3:1.7b qwen3:4b-instruct-2507-q4_K_M" TAG=cmp SETS="llm_temperature=0.0" SPLITS="dev" SUITES="faq triage"
make bench-review DIR=bench-cmp      # evals/bench-cmp/REVIEW.md: read the failures the strict scorer cannot judge on meaning
```
(about 10 min). Rule, stated before seeing results: switch the default to the Instruct model only if (a) 0 truncated, (b) must-abstain correct is not worse than 1.7b,
(c) it is better on correct answers by more than the interval, or by the human-reviewed count, and (d) p50 stays under ~10 s. The held-out split is then run **once** as confirmation for the chosen model only.
Why a review sheet: in the 1.7b `missing_fact` failures I read, the answers were right but worded differently from the expected phrase ("offered in both online and
offline modes" vs "online or offline"), so the strict score is a floor. I do not loosen the scorer; the sheet lets a person count real errors separately.
