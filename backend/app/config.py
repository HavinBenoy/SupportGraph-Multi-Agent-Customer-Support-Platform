"""Application settings, read from environment variables (prefix SG_) or the repo-root .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SG_", env_file=_REPO_ROOT_ENV, extra="ignore", populate_by_name=True)

    llm_provider: Literal["fake", "ollama", "openai_compat"] = "fake"
    llm_model: str = "qwen3:1.7b"  # M1 decision (docs/decisions/0006): safest measured model; qwen3:4b-instruct-2507-q4_K_M is an opt-in candidate
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    # --- Model gateway hardening (milestone 0) ---
    # Per-role model override, e.g. SG_LLM_MODEL_TRIAGE=qwen3:1.7b. Roles: action, faq, triage, rewrite, proposer, critic,
    # reviser, judge. Empty = use SG_LLM_MODEL. A role override never changes the provider or endpoint.
    llm_model_action: str = ""
    llm_model_faq: str = ""
    llm_model_triage: str = ""  # empty = use llm_model. (The 4B instruct escalated 12-15% of ordinary tickets, so triage should stay on the 1.7B.)
    llm_model_rewrite: str = ""
    llm_model_proposer: str = ""
    llm_model_critic: str = ""
    llm_model_reviser: str = ""
    llm_model_judge: str = ""
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)   # one call; CPU models are slow, so generous
    llm_max_retries: int = Field(default=1, ge=0, le=3)              # transient network errors only (openai_compat)
    llm_max_output_tokens: int = Field(default=400, ge=16, le=4096)  # per call cap (num_predict / max_tokens)
    llm_num_ctx: int = Field(default=4096, ge=512, le=32768)         # Ollama context window; bigger = slower + more RAM
    llm_keep_alive: str = "30m"                                      # Ollama: keep the model loaded between calls
    llm_thinking: bool = False                                       # qwen3 "thinking" off: it multiplies latency on CPU
    # Sampling. Defaults keep the current behaviour (temperature 0, provider defaults for the rest). Qwen3's authors advise AGAINST
    # greedy decoding (it can loop until the token cap): for the experiment see docs/MODEL_SELECTION.md. Unset (None) = not sent.
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_top_p: float | None = Field(default=None, gt=0, le=1)
    llm_top_k: int | None = Field(default=None, ge=1)                # Ollama only
    llm_repeat_penalty: float | None = Field(default=None, ge=0.5, le=2)  # Ollama only
    llm_seed: int | None = None
    # Process-wide daily cap on tokens spent through guarded calls. 0 = unlimited (the right value for local models).
    llm_daily_token_budget: int = Field(default=0, ge=0)
    # Hard gate: a chat/embedding endpoint that is not on this machine is refused unless this is true. This is what stops
    # a typo or a copied .env from silently sending student text to a third party.
    allow_external_llm: bool = False
    default_tenant: str = "qspiders_sample"
    # Matches the defaults in docker-compose.yml. SQLite URLs also work (used by the fast tests).
    database_url: str = "postgresql+psycopg://supportgraph:supportgraph@localhost:5432/supportgraph"

    # --- Embeddings + retrieval (build step 2) ---
    # "fake" is a deterministic offline embedder (feature hashing): good for tests and the no-LLM demo, NOT for
    # judging real retrieval quality. Vectors must be 768-wide (see app/db/models.py EMBEDDING_DIM).
    embedding_provider: Literal["fake", "ollama", "openai_compat"] = "fake"
    embedding_model: str = "nomic-embed-text"
    embedding_base_url: str = "http://localhost:11434"
    embedding_api_key: str = ""
    # Some embedding models want a task prefix (nomic-embed-text: "search_query: " / "search_document: ").
    # "auto": nomic-embed-text gets the prefixes its authors specify ("search_query: " / "search_document: ") unless you set
    # the two prefixes yourself; any other model gets none. "none": never add prefixes. Real providers only (the fake embedder is unaffected).
    embedding_prefix_mode: Literal["auto", "none"] = "auto"
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    rag_top_k: int = Field(default=4, ge=1, le=20)
    # Chunks scoring below this cosine similarity are not shown to the model, and if none remain the bot abstains.
    # Similarity scales differ per embedding model: calibrate with `make eval-rag` when you change the model.
    rag_min_score: float = Field(default=0.2, ge=0.0, le=1.0)

    # --- Triage, routing and corrective RAG (build step 3) ---
    # Below this triage confidence the router asks a clarifying question instead of acting.
    triage_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    # Category `other` with confidence from this value up to triage_min_confidence goes to the grounded FAQ (answer with a citation or
    # abstain) instead of a clarifying question. Below it, and for parse failures (0.0), the router still asks. Escalation always wins.
    triage_other_floor: float = Field(default=0.4, ge=0.0, le=1.0)
    # How many times an UNCITED FAQ answer is regenerated with a reminder to cite (0 = never; 1 = once). The retry is validated exactly like
    # the first answer and no other failure (truncated, declined, unknown citation, ...) is ever retried.
    faq_citation_retries: int = Field(default=1, ge=0, le=1)
    # Knowledge agent: first search + at most (attempts - 1) corrective retries. The cap prevents runaway loops and cost.
    knowledge_max_attempts: int = Field(default=2, ge=1, le=3)

    # --- Actions, approvals, checkpointing, QA (build step 4) ---
    # Where paused graph runs (waiting for an approval) are stored: "memory" is lost on restart (fine for demos/tests);
    # "postgres" survives restarts and uses the same database as everything else.
    checkpointer: Literal["memory", "postgres"] = "memory"
    # QA loop: how many times a failing reply may be revised before it is replaced by a safe fallback.
    qa_max_revisions: int = Field(default=1, ge=0, le=3)
    # M1b: roles outside the agreed final model configuration stay deterministic / off until they are measured with the real model.
    qa_llm_critic: bool = False                      # the LLM groundedness critic on every answered reply (adds a model call; can block good answers)
    rewriter_mode: Literal["glossary", "llm"] = "glossary"  # search-query rewriting: tenant synonym glossary (no model call) or the chat model
    # Startup checks for a local Ollama: off | warn (log and continue) | strict (refuse to start). Warm-up loads the models in the background.
    llm_preflight: Literal["off", "warn", "strict"] = "warn"
    llm_warmup: bool = True

    # --- M3: latency (every default keeps the pre-M3 behaviour unless noted; see docs/M3.md) ---
    # Ollama CPU threads for chat calls AND the warm-up preload (they must agree, or Ollama reloads the model). None = Ollama's own default.
    # On a 4-core/8-thread laptop try 4 (physical cores) - measure with `make bench-voice` before keeping it.
    llm_num_thread: int | None = Field(default=None, ge=1, le=64)
    obs_ollama_metrics: bool = True                    # record Ollama's load / prompt-eval / decode counters per call (names and numbers only)
    obs_cold_load_ms: int = Field(default=800, ge=0)   # a call whose model load took at least this long marks the turn "cold"
    obs_cold_embed_ms: int = Field(default=1500, ge=0)  # ... or an embedding call at least this slow
    warmup_prime_triage: bool = True                   # the warm-up also runs one real (fixed, synthetic) triage call and one embedding + DB query
    # Triage: "compact" is a shorter prompt (fewer prompt tokens = less CPU time). It is OFF until `make eval-routing` shows no regression
    # with the real model on your machine. triage_max_output_tokens caps the structured answer (0 = the global llm_max_output_tokens).
    triage_prompt: Literal["full", "compact"] = "full"
    triage_max_output_tokens: int = Field(default=0, ge=0, le=4096)
    # Same message text => same classification, so a repeated question skips the triage model call. Only successful model answers are cached.
    triage_cache_size: int = Field(default=256, ge=0, le=10000)          # 0 = off
    triage_cache_ttl_seconds: int = Field(default=3600, ge=1)
    # Validated-answer cache for repeated FAQ questions (anonymous callers only by default). Keyed by tenant + normalised question + a
    # fingerprint of the tenant's ingested documents and the answer-relevant settings; only a fully validated, cited answer is stored and it is
    # re-validated on every hit. Re-ingesting a changed document changes the fingerprint, so stale answers are never served. 0 = off.
    answer_cache_size: int = Field(default=128, ge=0, le=5000)
    answer_cache_ttl_seconds: int = Field(default=1800, ge=1)
    answer_cache_anonymous_only: bool = True
    # Prompt size controls (both OFF by default: they can drop the passage that holds the answer). 0 = off.
    # band: keep only passages scoring within this distance of the best one. max_chars: cap the total passage text sent to the model.
    # The best passage is always kept.
    rag_context_band: float = Field(default=0.0, ge=0.0, le=1.0)
    rag_max_context_chars: int = Field(default=0, ge=0, le=20000)

    # --- Evaluation (build step 6) ---
    # Price per 1M tokens, only used to turn measured token counts into "cost per ticket". 0 = local/free model (cost reported as 0).
    eval_price_per_m_input: float = Field(default=0.0, ge=0.0)
    eval_price_per_m_output: float = Field(default=0.0, ge=0.0)

    # --- API and sign-in (build step 5) ---
    # Signs login tokens and anonymous ticket ids. REQUIRED to start the API (>= 32 chars). Generate one with `make auth-init`;
    # never commit it. Rotating it signs everyone out.
    auth_secret: SecretStr | None = None
    token_ttl_minutes: int = Field(default=480, ge=5, le=60 * 24 * 7)
    login_max_failures: int = Field(default=5, ge=1, le=50)       # per (tenant, username, client) inside the window
    login_lockout_seconds: int = Field(default=300, ge=10, le=86400)
    chat_max_messages_per_minute: int = Field(default=30, ge=1, le=600)  # per client address or signed-in student
    # Comma-separated browser origins allowed to call the API directly. Empty = none (the frontend proxies /api, so it needs none).
    cors_origins: str = ""

    # --- Browser voice line (M2) ---
    # real = faster-whisper (speech to text) + Piper (text to speech) + Silero VAD; fake = scripted stand-ins for tests/demos without models.
    voice_enabled: bool = True
    voice_provider: Literal["real", "fake"] = "real"
    voice_stt_model: str = "base.en"                # faster-whisper model name or a local folder; downloaded on first use
    voice_stt_compute_type: str = "int8"
    voice_stt_threads: int = Field(default=4, ge=1, le=32)
    voice_stt_language: str = "en"
    voice_tts_voice: str = ""                       # path to a Piper voice .onnx (its .onnx.json sits beside it); see docs/M2.md
    voice_vad: Literal["auto", "silero", "energy"] = "auto"   # auto: Silero with real models, a crude energy gate with the fake provider (tests)
    voice_vad_model: str = ""                       # path to a Silero VAD .onnx; empty = the copy that ships inside faster-whisper
    voice_vad_threshold: float = Field(default=0.5, ge=0.1, le=0.95)   # speech probability to START speech; it ends below threshold - 0.15
    voice_silence_ms: int = Field(default=600, ge=200, le=3000)        # silence that ends one SEGMENT (a turn may merge several; see the grace settings)
    voice_grace_complete_ms: int = Field(default=250, ge=0, le=5000)   # extra wait after a segment whose transcript looks like a finished sentence
    voice_grace_short_ms: int = Field(default=700, ge=0, le=5000)      # ... one or two words
    voice_grace_incomplete_ms: int = Field(default=1600, ge=0, le=8000)  # ... trailing "and"/"about", a comma, "I want to know about"
    voice_max_turn_s: int = Field(default=30, ge=5, le=120)            # merged audio of one turn is never longer than this
    voice_max_pending_turns: int = Field(default=3, ge=1, le=10)       # finished turns waiting for the (single) graph runner
    voice_filler_after_ms: int = Field(default=1800, ge=0, le=15000)   # M3: say "One moment" when the answer is still not ready after this long (0 = never)
    voice_cache_fillers: bool = True                                   # M3: synthesise the fixed holding phrases once and reuse the audio (answers are never cached)
    voice_stt_beam: int = Field(default=1, ge=1, le=5)                 # 1 = fastest; 2-3 can hear unusual words better at some CPU cost
    voice_pace_audio: bool = True                                      # stream the reply at ~real time (tests switch it off)
    voice_pace_lead_ms: int = Field(default=1500, ge=200, le=10000)    # how far ahead of playback the server may send
    voice_min_utterance_ms: int = Field(default=300, ge=100, le=2000)  # shorter blips (clicks, coughs) are ignored
    voice_max_utterance_s: int = Field(default=15, ge=3, le=60)        # a longer monologue is cut and transcribed
    voice_barge_in_ms: int = Field(default=250, ge=0, le=2000)         # speech this long while the bot talks interrupts it
    voice_max_session_s: int = Field(default=600, ge=30, le=7200)
    voice_max_turns: int = Field(default=60, ge=1, le=1000)
    voice_max_reply_chars: int = Field(default=600, ge=100, le=3000)   # spoken reply is cut at a sentence boundary; the full text is still shown
    # Browser origins allowed to open the voice WebSocket (browsers do not apply CORS to WebSockets, so the server checks Origin itself).
    # cors_origins are allowed too. A client that sends no Origin (scripts, tests) is allowed: it still needs a valid ticket/token to act.
    voice_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- M4: staff live calls (listen / take over / return to AI) ---
    calls_takeover_grace_s: int = Field(default=15, ge=1, le=300)      # M4.1: if the staff member who took over has NO audio connection for this long the call ENDS as failed (it never goes back to the AI)
    calls_max_listeners: int = Field(default=5, ge=1, le=50)           # staff audio connections per call (each one may listen; at most one speaks)
    calls_listener_queue: int = Field(default=200, ge=10, le=5000)     # frames buffered per listener; a slow listener drops its OWN oldest audio, never delays the call
    calls_staff_frame_max: int = Field(default=16 * 1024, ge=1024, le=64 * 1024)   # one staff microphone message (a normal 40 ms frame is 1280 bytes)
    calls_recheck_s: int = Field(default=30, ge=5, le=600)             # a staff socket re-checks token expiry and that the account is still enabled this often
    calls_keep_ended_minutes: int = Field(default=30, ge=0, le=1440)   # ended calls stay visible in the list this long
    # M4.1: what the caller hears when staff take over. It is synthesised by the same voice as the assistant and is recorded as a SYSTEM entry in the transcript.
    calls_handover_announcement: str = Field(default="I want to make sure you receive accurate information. One of our support specialists is joining us to clarify this. "
                                                     "I'll hand over the conversation now.", min_length=10, max_length=400)
    calls_announcement_timeout_s: int = Field(default=30, ge=3, le=120)   # a notice that has not finished by then is abandoned (failure is shown to staff, who may speak)
    # --- M5: Trial Verification Mode ---
    # Recording is OFF unless a provider is chosen here AND an administrator offers recording in the trial settings AND the caller consents.
    # "local" writes WAV files under trial_recordings_dir (never the database). "none" = the consent/retention framework runs, nothing is ever recorded.
    trial_recording_provider: Literal["none", "local"] = "none"
    trial_recordings_dir: str = "data/recordings"
    trial_purge_interval_min: int = Field(default=60, ge=0, le=1440)   # how often the retention job runs inside the API process (0 = only on demand / `make purge-trial`)
    trial_metrics_max_rows: int = Field(default=5000, ge=100, le=100000)
    trial_recording_max_mb: int = Field(default=200, ge=1, le=2000)    # a recording that reaches this size is stopped (reason `size_limit`)

    # --- M6: knowledge store ---
    kb_max_bytes: int = Field(default=200_000, ge=1_000, le=2_000_000)         # largest document body accepted (UTF-8 bytes)
    kb_max_chunks: int = Field(default=300, ge=1, le=2000)                     # a document that would need more passages is refused (nothing is embedded)
    kb_processing_stale_s: int = Field(default=300, ge=10, le=86400)           # a version stuck in `processing` this long (crash) may be processed again
    # Part 2: AI impact analysis. kb_ai_mode: "auto" = the rule engine with the fake provider, the configured chat model (role "impact") otherwise; "rules" = never call a
    # model (offline, deterministic); "llm" = require the model. The model role is SG_LLM_MODEL_IMPACT (empty = SG_LLM_MODEL). Nothing leaves the machine: the endpoint
    # guard (SG_ALLOW_EXTERNAL_LLM) applies exactly as for every other role.
    kb_ai_mode: Literal["auto", "rules", "llm"] = "auto"
    kb_ai_max_candidates: int = Field(default=12, ge=1, le=40)                 # documents evaluated per analysis
    kb_ai_max_passages: int = Field(default=6, ge=1, le=20)                    # passages shown to the model per document
    kb_ai_semantic_floor: float = Field(default=0.3, ge=0, le=1)               # semantic similarity that can nominate a document that lacks the old value
    kb_ai_definite_confidence: float = Field(default=0.75, ge=0, le=1)         # below this a "definite" verdict is shown as "potential"
    kb_ai_analysis_stale_s: int = Field(default=900, ge=30, le=86400)          # an analysis stuck in `analysing` this long (crash) may be started again
    kb_ai_background: bool = True                                              # run analysis and release in a background thread (False = inline; tests)
    llm_model_impact: str = ""
    impact_max_output_tokens: int = Field(default=900, ge=64, le=4096)         # the impact role writes replacement text, so it gets a larger output cap than 400
    kb_release_stale_s: int = Field(default=900, ge=30, le=86400)              # a request stuck in processing/publishing this long (crash) may be retried
    kb_require_separate_reviewer: bool = False                                 # true: the administrator who proposed a feedback-based change cannot review it

    # --- Tracing (Langfuse). Off unless SG_TRACING_ENABLED=true AND both keys are set. ---
    # The keys use Langfuse's standard variable names, so the same .env also works for its tools.
    tracing_enabled: bool = False
    langfuse_public_key: str | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "langfuse_public_key"))
    langfuse_secret_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "langfuse_secret_key"))
    langfuse_base_url: str = Field(default="http://localhost:3100", validation_alias=AliasChoices("LANGFUSE_BASE_URL", "langfuse_base_url"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
