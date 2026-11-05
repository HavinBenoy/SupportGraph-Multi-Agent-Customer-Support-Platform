# ADR 0006: M1 default model and provider stance

Status: accepted 2026-11-05. Supersedes the "documented default" wording in ADR 0005.

- Default chat model changes from `qwen3:4b` to `qwen3:1.7b` for every role. Reason (observed on the project laptop, CPU only): 4B truncated 44 of 68 FAQ
  answers, scored 13-17% correct and took ~37 s per answer; 1.7B truncated none, scored 53-70% and took ~2.8 s. Triage differences were within noise.
  This is a working default, not a final choice; see `docs/MODEL_SELECTION.md` for the diagnostic that decides whether 4B is revisited.
- Providers stay local (Ollama). Hosted inference is not part of the product path. `openai_compat` remains an opt-in, explicitly gated option for synthetic-data experiments.
- Sampling is configurable (`SG_LLM_TEMPERATURE`, `_TOP_P`, `_TOP_K`, `_REPEAT_PENALTY`, `_SEED`); defaults reproduce the previous behaviour (temperature 0).
- A model reply stopped by the output-token limit is never shown to a person (`app/llm/truncation.py`, FAQ `validate`, QA reviser).
- Consequence: if your `.env` sets `SG_LLM_MODEL`, it overrides this default.
