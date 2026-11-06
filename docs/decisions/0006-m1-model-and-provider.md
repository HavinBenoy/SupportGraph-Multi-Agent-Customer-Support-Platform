# ADR 0006: M1 default model and provider stance

Status: accepted 2026-11-05. Supersedes the "documented default" wording in ADR 0005.

- Default chat model: `qwen3:1.7b` for every role (`SG_LLM_MODEL_TRIAGE` empty = same model). History: the original default `qwen3:4b` was a thinking-only build
  (its reasoning cannot be switched off and consumed the token cap), so it is excluded. The non-thinking `qwen3:4b-instruct-2507-q4_K_M` was better on FAQ correctness
  (dev 83% vs 70%; held-out 63% vs 53%) with no truncation, but answered 1 of 7 held-out must-abstain questions, which fails the abstention rule set before the run
  (must-abstain not below 1.7b's 100%). It stays an opt-in candidate; its triage over-escalates (12-15% of ordinary tickets). Working defaults, not final choices.
- Providers stay local (Ollama). Hosted inference is not part of the product path. `openai_compat` remains an opt-in, explicitly gated option for synthetic-data experiments.
- Sampling is configurable (`SG_LLM_TEMPERATURE`, `_TOP_P`, `_TOP_K`, `_REPEAT_PENALTY`, `_SEED`); defaults reproduce the previous behaviour (temperature 0).
- A model reply stopped by the output-token limit is never shown to a person (`app/llm/truncation.py`, FAQ `validate`, QA reviser).
- Consequence: if your `.env` sets `SG_LLM_MODEL`, it overrides this default.
