# Part 2 implementation report: AI knowledge change intelligence and the integrated admin workflow

Everything below was executed in the sandbox. Nothing was run on your machine, and the real Ollama model was not available here.

## 1. Architecture and workflow
Two entry points (open a document → *Find related documents with AI*; or *Create AI-assisted change*) create the same `kb_change_requests` row (`source='ai_assisted'`). A background job analyses the change and writes suggestions (`origin='ai'`). Staff decide per document, submit, and an administrator reviews, approves and publishes, all inside Knowledge → Change Requests. A 15-state machine (`changes.TRANSITIONS`) governs every step; full table in docs/PART2.md section 3. Published knowledge is only ever written by M6 `publish_version`.

## 2. AI impact-analysis design
Parse (facts, old/new values, dates, entity terms) → clarify if ambiguous → discover (value, scope, lexical, semantic, structure signals) → retrieve numbered passages → evaluate → deterministic validation → persist. Classes: definite, potential, related-no-change (not-relevant documents are summarised). Each result carries confidence, explanation, uncertainty and a line patch `{start,end,original,replacement}` over the immutable base version. Historical or dated records and different-scope documents are never changed.

## 3. Model and prompt strategy
`kb_ai_mode` auto/rules/llm; role model `llm_model_impact` (default chat model, the existing Ollama Qwen setup); structured output validated by a Pydantic schema; `impact_max_output_tokens`; prompt version `impact-v1`. The model can only point at a passage by number. The same guards run after any model. No paid service, no fine-tuning, no document text leaves the machine.

## 4. Database changes
Migration 0012 (additive, reversible): new request columns (`reference_doc_id`, `analysis`, `analysis_attempt`, timestamps, `return_note`, `release_attempt`, `phase_started_at`, `release`, `failure`, `published_by/at`), widened status CHECK, and item columns (`classification`, `confidence`, `ai_suggestion`, `final_edits`, decision and admin-decision fields, `process_status`, `publish_status`, `process_error`). Downgrade maps new states back to Part 1 states and preserves Part 1 requests (tested on SQLite and PostgreSQL).

## 5. API changes
Staff: `POST /ai-changes`, `POST /changes/{id}/analyse`, `POST /changes/{id}/items/{doc}/decision`, `GET /changes/{id}/audit`. Admin: `start-review`, `items/{doc}/edit`, `items/{doc}/review`, `publish` (202, poll), `reopen`, `cancel`, `audit`; `GET /changes` returns counts and the new statuses. Part 1 routes unchanged. Tenant always comes from the token.

## 6. Staff workflow
Describe (optional reference document) → Analyse → polled progress bar → follow-up questions when needed (for example which batch) → decision cards: title, folder path, published version, classification, confidence, explanation, orange *Existing* / blue *New* passage with text tags and screen-reader labels, Accept / Edit manually (live patch preview) / Discard (optional reason), reviewer and time → summary table → Submit (disabled until all are decided). Stale documents can only be discarded.

## 7. Admin integrated approval and publishing workflow
List with ID, description, submitter, time, documents, accepted / edited / discarded, status. Detail: Overview (counts, stepper, per-document progress, failure box, published versions), Documents (Original / Proposed / Compare, final edit, Exclude with required reason, Confirm, staff discard reasons, overlap warnings), Audit history. Actions: Start review, Approve (publishes nothing), Return (note required), Reject, Cancel, Process and Publish Approved Changes (explicit confirmation), Retry, Reopen. The Publishing tab is not needed and remains for standalone documents.

## 8. Publication consistency and transaction guarantees
Begin (fenced claim, staleness re-check, immutable drafts) → process each document through the M6 pipeline (embedding outside any transaction) → publish all documents in ONE transaction. Verified on PostgreSQL with a concurrent reader: all-old until commit, all-new after; an injected failure inside the publish transaction leaves the database exactly as it was. Limits: embedding is not in that transaction; two separate statements in one chat turn can straddle the commit; atomicity is across the database only.

## 9. Failure and retry behaviour
Analysis failure returns the request to draft with a short error and a retry. Release failure at any stage leaves live knowledge unchanged, shows the stage and documents, and offers Retry or Reopen. Retries reuse draft versions and never publish twice (tested: replayed workers, racing clicks). Stuck `analysing`/`processing`/`publishing` states are taken over after the stale timeouts, fenced by attempt numbers.

## 10. Security and tenant isolation
Tenant from the token only; RBAC (staff own requests, admins review); private states hidden from admins; optimistic concurrency on requests and items; stale patches refused at decision, submit, approve and publish; only accepted or edited text is ever published, discarded text never reaches versions or RAG; separate-reviewer setting honoured; audit keys whitelisted so document text, replacements and discard reasons never enter the audit; diffs rendered as text, never HTML. Cross-tenant tests are in tests/knowledge/test_ai_flow.py.

## 11. Tests actually run (sandbox)
| Suite | Result |
|---|---|
| Backend, SQLite, whole suite (`-n 4`) | 1917 passed, 76 skipped |
| Backend, PostgreSQL 16, whole suite (`-n 4`) | 1990 passed, 3 skipped |
| New Part 2 backend files | impact units 31, flow 31, release 31, evaluation 15 (+1 gated real-model, skipped), PostgreSQL 9 |
| Frontend (vitest) | 229 passed, 12 files (AiKnowledge.test.tsx 15 new; two Part 1 admin tests replaced) |
| `tsc --noEmit` | clean |
| `npm run build` | succeeded |

Evaluation (15 scenarios, synthetic documents, rule model; describes the deterministic engine and guards, not LLM quality). Scored scenarios s01–s06: mean precision 1.000, mean recall 1.000, exact replacements 15/15, wrong edits 0, historical records guarded in s01 and s02. Other scenarios: ambiguity asked 2 questions with 0 silent guesses and 0 suggestions before the answer; 0 unsupported proposals published; 0 discarded text in RAG; 0 silent overwrites after a stale document; 0 documents published after reject, and 10 audit events kept; 0 partial publications and recovery on retry; 0 duplicate publications or versions on retry; 0 lost updates with overlapping requests. These were synthetic and hand-written, so perfect scores mean the engine handles these cases, not that it is perfect in general.

## 12. Commands to apply locally
```
cd supportgraph && unzip -o ../supportgraph-part2-ai-knowledge-intelligence.zip
make db-migrate            # applies 0012
make test-ai               # backend knowledge tests + workspace frontend tests
make test-ai-pg            # needs the dev Postgres container
make eval-impact           # 15 scenarios with metrics; add REAL=1 for the optional real-model check (needs Ollama)
make test-fast && make test-pg && make frontend-test && make frontend-build
```
Optional settings: `SG_KB_AI_MODE`, `SG_LLM_MODEL_IMPACT` (see app/config.py).

## 13. Known limitations
* The real Ollama/Qwen path was never run here; `LlmImpactModel` is tested with stub models, and the gated `make eval-impact REAL=1` check is for you to run. Small local models may return more "potential" or dropped proposals than the rule engine.
* Rule engine and sibling lexicon are small and English-only; only one-line replacements are produced; unusual date formats may not be recognised.
* Re-running the analysis discards earlier decisions.
* Draft versions left by a failed release that is then edited stay as unpublished drafts.
* Publication is atomic within the database only; embedding is outside the transaction by design; multi-statement chat turns can straddle the commit.
* The knowledge tests are slow (each seeds documents).
* No browser-level (Playwright) test of the new screens; frontend coverage is component tests with mocked APIs.

## 14. How Part 3 builds on this
Part 3 (landing page and application-wide redesign) can reuse the `.kw` design tokens, `ui.tsx` status/classification chips, `PatchView` and the progress components, and the stable state vocabulary. No further backend changes are required.
