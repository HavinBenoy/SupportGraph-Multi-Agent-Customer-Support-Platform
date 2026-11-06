# ADR 0006: M1 default model and provider stance

Status: accepted 2026-11-05. Supersedes the "documented default" wording in ADR 0005.

- Default chat models (superseding the first M1a wording, after the full-dev comparison): `qwen3:4b-instruct-2507-q4_K_M` for FAQ and every role except triage; `qwen3:1.7b` for triage (`SG_LLM_MODEL_TRIAGE`). Pending one held-out confirmation run. History: the original default `qwen3:4b` was replaced first by `qwen3:1.7b`. Reason (observed on the project laptop, CPU only): 4B truncated 44 of 68 FAQ
  answers, scored 13-17% correct and took ~37 s per answer; 1.7B truncated none, scored 53-70% and took ~2.8 s. Triage differences were within noise.
  The tested `qwen3:4b` tag turned out to be a thinking-only build (its reasoning cannot be switched off and consumed the token cap), so it is excluded. The non-thinking 4B instruct build was better on full dev (FAQ 83% vs 70%, no truncation, same abstention, p50 4.4 s) but escalated 12% of ordinary tickets in triage, hence the split. Working defaults, not final choices.
- Providers stay local (Ollama). Hosted inference is not part of the product path. `openai_compat` remains an opt-in, explicitly gated option for synthetic-data experiments.
- Sampling is configurable (`SG_LLM_TEMPERATURE`, `_TOP_P`, `_TOP_K`, `_REPEAT_PENALTY`, `_SEED`); defaults reproduce the previous behaviour (temperature 0).
- A model reply stopped by the output-token limit is never shown to a person (`app/llm/truncation.py`, FAQ `validate`, QA reviser).
- Consequence: if your `.env` sets `SG_LLM_MODEL`, it overrides this default.
