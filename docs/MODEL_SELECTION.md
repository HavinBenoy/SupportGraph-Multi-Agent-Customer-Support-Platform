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
| qwen3:4b-instruct-2507-q4_K_M (non-thinking)? | **FAQ model (and every role except triage), pending one held-out confirmation.** Reversible with `SG_LLM_MODEL`. | Observed, full dev, same settings as 1.7b: correct 83% vs 70% (19 vs 16 of 23; inside the noise at n=23), correct when context sufficient 86% vs 73%, 0/31 truncated, must-abstain 100% (n=8) both, p50 4.4 s / p95 8.2 s vs 2.9 / 5.7. Reviewed the 8 strict-score failures myself: 4B's 3 are acceptable (one thin), 1.7b has 3 correct paraphrases, 1 contradictory ("Yes, you do not need..."), 1 incomplete (omits marks card). Reading of the review sheet adjusts to roughly 22/23 vs 19/23. These are one reader's verdicts on the same tickets, not independent evidence. |
| Triage model? | **qwen3:1.7b** (`SG_LLM_MODEL_TRIAGE` default). | Observed, full dev (57): 4B-instruct route 79% vs 77%; false escalations **6/51 (12%) vs 0/51**; missed 0/6 vs 1/6; own-records 100% vs 67% (n=12); category 35% vs 22%. The 4B never missed an escalation but would flood staff; with 6 expected escalations the missed-escalation difference is not conclusive. Neither is trustworthy alone: the fix is the task design in section 3 (and deterministic rules that can only add escalations), not model size. |
| Which model(s)? | **qwen3:4b-instruct-2507-q4_K_M for FAQ and other roles, qwen3:1.7b for triage, `nomic-embed-text` for embeddings.** Two chat models resident (~4 GB) is fine on 16 GB if the browser/IDE are not hogging RAM (free RAM was 2.6-3.3 GB during your runs); if that is tight, set `SG_LLM_MODEL_TRIAGE` to the same model or lower `SG_LLM_KEEP_ALIVE`. The rewrite / proposer / critic / reviser roles have not been measured with either model. | As above. Embedding Hit@4 100% on both splits. |
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

## 5. Confirmation run (the held-out split is unseen for the 4B instruct model, so it counts once)
```bash
make bench-experiment MODELS="qwen3:4b-instruct-2507-q4_K_M" TAG=heldout4b SETS="llm_temperature=0.0" SPLITS="heldout" SUITES="faq triage"
make bench-review DIR=bench-heldout4b
```
(~5 min.) Revert to qwen3:1.7b for FAQ if the 4B shows any truncation, must-abstain correct below 1.7b's 100% (n=7), or correct (all answerable) below
1.7b's held-out 53% (n=30; these 1.7b numbers are from the M0 run). Otherwise the choice stands for M1b. Each result is one run on a small set: do not quote it as a general accuracy.
