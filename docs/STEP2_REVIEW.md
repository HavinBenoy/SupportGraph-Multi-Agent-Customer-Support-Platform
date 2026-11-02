# Step 2 review (against source, 2026-11-02)

Three different things are kept apart: **tests pass** (code does what the tests say), **functionality works** (the pipeline runs end to end),
**answer quality** (are answers good). Only the first two were measured. Quality with real models is **unmeasured**.

## Component check (verified by reading/running the source, not the README)

| Component | Implemented? | Where | Note |
|---|---|---|---|
| Institute schema + migrations | Yes | `db/models.py`, migrations 0001-0003 | Alembic `0003 (head)` confirmed by you locally |
| Sample data / tenant config | Yes | `db/seed_data.py`, `tenants/qspiders_sample/config.yaml` | Mostly invented, labelled SAMPLE |
| Public doc loading + validation | Yes | `rag/documents.py` | front-matter, audience, provenance checks |
| Chunking | Yes, **one bug found** | `rag/chunking.py` | unpunctuated text produced a ~3000-char chunk; fixed + regression test |
| Embeddings + pgvector | Yes (fake embedder only) | `llm/`, `rag/ingest.py`, `vector(768)` | real model never run |
| Retrieval | Yes | `rag/retrieval.py` | tenant + `audience='public'` filters in SQL |
| LangGraph FAQ workflow | Yes | `rag/faq.py` | retrieve -> generate -> validate / abstain |
| Citations | Yes | `rag/faq.py` | cite-or-abstain; unknown/missing citation abstains |
| Abstention | Yes | `rag/faq.py` | `rag_min_score` gate; model not called when nothing relevant |
| Internal-doc protection | Yes, 3 layers | DB CHECK, SQL filter, ingest skips `internal/` | eval detects verbatim leaks only |
| RAG evaluation | Yes | `rag/evaluate.py`, `scripts/eval_rag.py` | hit@k, MRR, correctness, unsupported numbers, leaks |
| Langfuse instrumentation | Yes (stand-in server tested) | `observability/tracing.py` | real dashboard not verified |

## Your local results, read honestly

- 329/20 skipped and 349 passed, migration head, seed and ingest numbers match the sandbox: the **plumbing works**.
- Retrieval Hit@1 91% / Hit@4 96% / MRR 0.935 come from a **hashing (lexical) embedder**: they say little about a real embedding model.
- "All facts correct 65%, wrong among answers 29%" is largely the **extractive fake model** (it copies passages, so facts are truncated).
  T002/T012/T014/T015/T017/T020 are of this kind: judge them again with a real model.
- "What courses are available?" returned batch timings: a **vocabulary gap** ("courses" vs "Programmes") that a lexical fake cannot bridge.
  Addressed in Step 3 by tenant-vocabulary query expansion (a mitigation, not a semantic fix).
- T021 (Java fee answered from the Python fee table) and T026 (trainer answered for PYF-101) are **wrong-entity answers with valid citations**:
  a citation proves provenance, not correctness. Needs a real model and a groundedness critic (step 4).
- T016 wrongly abstained ("marks" vs "score"): vocabulary gap; Step 3 expansion now answers it.
- 0 internal leaks and 0% unsupported numbers: good, but leak detection is verbatim-only.
- Langfuse "could not parse the LLM model name": harmless with fake models (no model name to parse); recheck with a real model.

## Classification of remaining issues

1. **Fix now (bugs):** oversized chunk from unpunctuated text - **fixed**.
2. **Known limitations (documented, not fixed):** lexical-only fakes; wrong-entity cited answers; extractive truncation; romanised Hindi (T050);
   verbatim-only leak detection; sample data is invented.
3. **Real embedding/LLM integration:** calibrate `SG_RAG_MIN_SCORE`; re-run T002/T012/T014/T015/T017/T020/T021/T026; LLM relevance grader;
   multilingual retrieval; confirm the Langfuse model-name warning disappears; real-model triage/rewrite quality.
4. **Security/auth work:** real authentication behind "signed in"; PII masking in traces; rate limiting; student-only documents path; stronger injection defence.
