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
| qwen3:4b? | **Not the default. Do not abandon the model family blindly: run one diagnostic (section 5).** | Observed: 19/31 and 25/37 truncated at the 400-token cap, correct 17%/13%, p50 ~37 s, 11.8 tok/s. Documented: Qwen's own guidance recommends sampling (temperature 0.6-0.7, top-p 0.8-0.95, top-k 20) and warns against greedy decoding because it can loop endlessly (Unsloth's Qwen3 guide, which restates the Qwen recommendations). Our gateway hard-codes temperature 0. Hypothesis: that is why 4B never stops. Even if fixed, 4B costs several times the 1.7B latency on this CPU, so it must show a clear quality gain to be worth it. |
| Which model(s)? | **qwen3:1.7b for FAQ and triage (config default changed), `nomic-embed-text` for embeddings (unchanged).** One model keeps RAM and cold-start low on a 16 GB laptop; per-role overrides (`SG_LLM_MODEL_<ROLE>`) stay available. | Observed: 1.7b >= 4b on every FAQ metric; triage differences between the two are within noise (dev 77 vs 81%, held-out 77 vs 73%, n=51-55). Embedding Hit@4 100% on both splits. |
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

## 5. The experiment to run next (30-60 min of your laptop, no code changes)
Keep the baseline files in `evals/bench/` untouched. Every variant goes to its own folder so nothing is mixed.
```bash
# A. Does sampling fix 4B's non-termination? Defaults: dev split, FAQ suite only, 20 tickets, no cold start. ~3 min if it stops (~8 s/answer), ~13 min if it still hits the cap.
make bench-experiment MODELS="qwen3:4b" TAG=samp4b SETS="llm_temperature=0.7 llm_top_p=0.8 llm_top_k=20" N=20
# B. Same sampling on 1.7b (is it the sampling or the model size?). ~1 min.
make bench-experiment MODELS="qwen3:1.7b" TAG=samp17 SETS="llm_temperature=0.7 llm_top_p=0.8 llm_top_k=20" N=20
```
Read `evals/bench-<TAG>/SUMMARY.md`. Decision rule I will apply (stated before seeing results): 4B is worth keeping only if truncation falls to ~0 **and**
correct (context-sufficient) beats 1.7B by a margin larger than the interval, **and** p50 stays under ~10 s. Otherwise it is dropped. Sampling makes runs
non-deterministic: if the result is close, repeat with `llm_seed=1`, `2`, `3` and compare, rather than trusting one run. Then send me both SUMMARY.md files.
