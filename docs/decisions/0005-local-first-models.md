# ADR 0005: Local-first models, explicit external opt-in, replaceable speech components

Status: accepted by Havin on 2026-11-05 (decision report "SupportGraph for QSpiders: Architecture and Decision Report"). Milestone 0 implements the gateway part.

## Decisions

1. **Local first.** The mandatory demo runs on the developer laptop (i5-11300H, 16 GB, no GPU) at zero running cost. Chat and embeddings go to Ollama on `localhost`. Candidates: `qwen3:4b`, `qwen3:1.7b` (chat), `nomic-embed-text` (768-d embeddings, matches the DB column). **The final choice is made from `make bench-llm` / `make bench-embed` results on that laptop**, not from estimates. Until those results are reviewed, `qwen3:4b` is only the documented default.
2. **No silent fallback to an external model, ever.** If the local model is down, the bot says so in fixed wording (`replies.MODEL_UNAVAILABLE`) and stays honest; it never calls another provider. Escalation floor and routing are code, so safety paths still work without a model.
3. **External providers are opt-in, explicit, and synthetic-data-only.** The gateway refuses any chat/embedding endpoint that is not loopback/private/`ollama`/`host.docker.internal` unless `SG_ALLOW_EXTERNAL_LLM=true`. The app prints which endpoint is in use at startup and flags `EXTERNAL`. Data handling of the free tiers considered (from the report; re-check before use, terms change): Gemini free tier may use/human-review content (do not use with anything real); Groq documents no default retention beyond short troubleshooting windows, with US storage. Neither is enabled by this milestone.
4. **Thinking mode off** for qwen3 (`SG_LLM_THINKING=false`): hidden reasoning multiplies CPU latency.
5. **Bounded calls.** Per-call timeout, output-token cap, context window, optional daily token budget. A failing call becomes `ModelUnavailable`, which the knowledge and action nodes turn into a fixed reply. Triage already degrades to "ask a clarifying question".
6. **Per-role models.** `SG_LLM_MODEL_<ROLE>` lets a small model do triage and a larger one answer, if benchmarks justify it. Same provider and endpoint for all roles.

## Known limits of the milestone-0 guard

- Budget accounting and error normalisation wrap the FAQ `generate` and action-agent calls (the two unprotected call sites found). Triage, rewrite, proposer, critic, reviser and judge each already degrade on error, but their tokens are not counted against the daily budget. A per-ticket token cap is not implemented (needs plumbing through the graph); the per-call cap and bounded node counts limit spend instead. Irrelevant for local models (budget 0).
- The "local endpoint" test is by address/hostname. It cannot know that a private IP is really your own machine; it protects against typos and copied `.env` files, not a deliberately misconfigured network.

## Text-to-speech licence (for the voice milestone; decision recorded now)

Piper's maintained fork is **GPL-3.0-or-later**. Accepted for the demo on these conditions:
- Piper runs as a **separate process / local service** behind our own `TextToSpeech` interface (milestone 2); SupportGraph code does not import or vendor it. The interface keeps Piper replaceable (Kokoro, a cloud TTS later).
- GPL obligations attach to **distributing** Piper (for example a Docker image containing it): if that ever happens, ship its licence and source offer. Running it on your own machine for a portfolio demo, and publishing this repository without Piper's code, distributes nothing.
- **Voice files have their own licences.** Check each voice's model card before use or redistribution.
- No security or privacy impact: it runs offline, receives text we generated, sends nothing out.
- This is an engineering note, not legal advice; get a lawyer's view before any commercial distribution.

## Consequences

Slower than cloud on CPU (expect seconds per answer, not milliseconds; unmeasured until your benchmark). Voice latency targets are handled by design in milestone 3 (fast path, truthful filler phrases), not by pretending the model is fast.
