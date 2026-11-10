# Part 2: AI-powered knowledge change intelligence and the integrated admin workflow

Part 2 of 3. Builds on M6 (versions, processing, publish, audit, fencing) and Part 1 (folders, reader, change requests). Part 3 (landing page, application-wide redesign) is not included.

## 1. Principles
* **The AI only suggests.** It writes rows with `origin='ai'` in `kb_change_items`. Nothing in `app/knowledge/impact/` can write a published version, `documents` or `chunks`.
* **A human decides twice.** Staff accept / edit / discard each suggestion; an administrator then reviews, approves and publishes. Approval publishes nothing.
* **One publication unit.** A multi-document request is processed document by document, but published in ONE database transaction.
* **Guards run after any model.** Whatever the model returns passes the same deterministic checks (historical, scope, value, one line, length). A proposal that cannot be defended is dropped and shown as "could not write a safe replacement"; it is never invented.
* **Honest uncertainty.** Confidence is the engine's own estimate; a low-confidence "definite" is shown as "potential". Related documents with no change are listed as such.

## 2. Two entry points, one request structure
* **A.** Open a document → *Make changes* → edit → *Find related documents with AI*. The staff member's own edit stays a manual item (`origin='manual'`); the AI looks for the other documents.
* **B.** *Create AI-assisted change* (Knowledge sidebar): description, optional reference document, *Analyse*.
Both create the same `kb_change_requests` row with `source='ai_assisted'`. B can ask follow-up questions (for example which Data Science batch) instead of guessing.

## 3. State machine (`changes.TRANSITIONS`, every change goes through `check_transition`)
| From | To |
|---|---|
| draft | analysing, submitted, cancelled |
| analysing | awaiting_clarification, awaiting_decisions, draft (failure, retryable), cancelled |
| awaiting_clarification | analysing, cancelled |
| awaiting_decisions | submitted, analysing (re-run), cancelled |
| submitted | under_review, approved, rejected, needs_revision, cancelled |
| under_review | approved, rejected, needs_revision, cancelled |
| needs_revision | submitted, analysing, cancelled |
| approved | processing, applied (Part 1 legacy), cancelled |
| processing | publishing, failed |
| publishing | published, failed |
| failed | processing (retry), under_review (reopen), cancelled |
| rejected, published, cancelled, applied | terminal |
`draft`, `analysing`, `awaiting_clarification`, `awaiting_decisions` are private to the author. Editable: those plus `needs_revision`.

## 4. Impact analysis (`app/knowledge/impact/`)
1. **Parse** (`spec.py`, `values.py`): summary, facts (attribute, old value, new value, effective date), entity terms. Money, times, dates and counts are recognised in many spellings (₹45,000 / Rs. 45000/- / 45k).
2. **Clarify**: if the description cannot choose between sibling scopes (weekend/weekday, morning/evening, online/offline, batch A/batch B …) and the documents hold more than one, questions are asked and **no suggestion is shown until they are answered**.
3. **Discover** (`discover.py`): five recorded signals per document: old value present, entity words in title/path/category/tags/headings, lexical overlap, semantic similarity to the document's LIVE passages, folder structure. A document with the old value is always a candidate.
4. **Retrieve passages** (`passages.py`): numbered, verbatim passages with their headings.
5. **Evaluate** (`evaluate.py`): `RuleImpactModel` (offline) or `LlmImpactModel` (structured output). The model may only point at a passage by number.
6. **Validate** (`evaluate.validate`): historical/dated records never change; scope and sibling guard; the replacement must contain the new value and not the old one; one line; length cap.
7. **Persist** (`analyse.py`): classification (`definite`, `potential`, `related_no_change`; not-relevant documents are summarised, not stored), confidence, explanation, uncertainty notes, signals and the **patch** `[{start,end,original,replacement}]` over whole lines of the immutable base version. `apply_edits` refuses if the base no longer matches. No HTML is stored.

### 4a. Changes that name no old value (Part 2 final fix)
"The machine learning faculty is changed to santhosh sir from today" states the NEW value only; the old one is whatever the document says today. Such a *slot change* has an
attribute (faculty, trainer, fee, timing ...), a subject (the entity words) and a new value, and `old_value = null`.
* `spec.parse_rules` reads "X is/was changed|updated|replaced ... to Y", "change X to Y", "X is now Y", "new X is Y". `from today / now / immediately` mean the current fact (no date is
  written); `from tomorrow` becomes the calendar date; other dates are written as "(effective <date>)".
* `passages.slot_passages` finds lines of the form "<subject> <attribute> is|are|:|=|- <value>" and returns each with a copy of the fact whose old value is that line's current value, so
  the rest of the pipeline treats it as an ordinary old -> new change. A value must be the right kind (a name for a person attribute, an amount for a fee, a time for a timing).
* Slot facts are only accepted with a known attribute word and at least one subject word. Otherwise the analysis still asks staff to restate.
* A line that already holds the new value is "no change: already says ...".
* Not covered: slot values in sentences with no "is/are/:" before the value ("meets on Saturday, 10 AM - 1 PM"); those need the old value in the description.

### 4b. How a model's answer is checked (`evaluate.ground_spec`, `LlmImpactModel`)
1. The rule parser reads the sentence first; the model is asked only when the rules cannot.
2. Every value a model extracts must occur in the description (any spelling); an old value that does not is dropped, a new value that does not discards the fact.
3. Entity terms are single words: phrases are split, generic/attribute words and the words of the new value are removed (`ChangeSpec` validators).
4. The rule engine judges every passage too. If the evidence says "affected" and the model says "no change", the result is **potential** with the rule engine's grounded replacement and
   a reduced confidence, never silent "no change". If the model's rewrite is unusable the rule engine's is used. A model that fails or answers invalidly gives **potential** from the
   rule engine, never "definite".
5. When a guard (historical, scope, already-new) overrides a model verdict, the guard's explanation replaces the model's; the model's text is kept as `model_note` for the reviewer.
6. A "no change" verdict whose own explanation says the passage "must be updated" and for which no evidence exists becomes **potential, no replacement** ("verdict and explanation disagreed").

## 5. Model strategy
* `kb_ai_mode`: `auto` (default) = rule engine when the fake provider is configured, otherwise the chat model for role `impact`; `rules` = never call a model; `llm` = always the model.
* Role model: `llm_model_impact` (empty = the default chat model, currently the Ollama Qwen setup). `impact_max_output_tokens` caps output. Prompt version `impact-v2` is recorded (v1 before the final fix).
* Output is validated against a Pydantic schema; invalid output fails the analysis cleanly (request back to `draft`, short error, retry).
* No paid external model, no fine-tuning, no document text leaves the machine.

## 6. Staff workflow
Describe → progress (polled) → optional questions → decision workspace: per candidate title, folder path, published version, classification, confidence, explanation, uncertainty, the exact passage (orange, tagged *Existing*) and replacement (blue, tagged *New*), **Accept / Edit manually / Discard (optional reason)**. *Edit manually* shows the resulting patch before saving. A summary table lists every document; *Submit* is disabled until all are decided. A stale document (published again since the analysis) cannot be accepted or edited, only discarded.

## 7. Administrator workflow (Knowledge → Change Requests, no Publishing tab needed)
List: ID, description, submitted by, time, documents, accepted / edited / discarded, status. Detail: **Overview** (counts, stepper, per-document progress, failure box, published summary with previous → new versions), **Documents** (Original / Proposed / Compare, final manual edit, Exclude with required reason, Confirm, the staff discard reason, overlap warnings), **Audit history**. Request actions: Start review, Approve (publishes nothing), Return for revision (note required), Reject, Cancel, **Process and Publish Approved Changes** (explicit confirmation), Retry, Reopen.

## 8. Publication (`app/knowledge/release.py`)
1. *begin*: fenced claim (`release_attempt`), re-check every included document is still at its base version, create one immutable DRAFT version per document (a retry reuses the version an earlier attempt made).
2. *process*: the M6 pipeline per document (chunk, embed outside any transaction, stage, validate). Any failure stops the release before publishing.
3. *publish*: ONE transaction: re-verify ownership and staleness, `publish_version` for every document, mark items and request `published`, store the release record.

**Guarantees.** All live rows change in one transaction (PostgreSQL and SQLite): a single retrieval statement sees all-old or all-new. **Limits.** Embedding is outside the transaction by design. Two separate statements in one chat turn can straddle the commit (read committed). A crash leaves `processing`/`publishing`; after `kb_release_stale_s` a retry takes over (the attempt number fences the old worker). Draft versions from a failed release stay as unpublished drafts. Atomicity is across the database, not across any external system.

## 9. Database (migration 0012, additive, reversible)
`kb_change_requests`: `reference_doc_id`, `analysis` (JSON), `analysis_attempt`, `analysis_started_at/completed_at`, `return_note`, `release_attempt`, `phase_started_at`, `release` (JSON), `failure` (JSON), `published_by/at`; status CHECK widened to 15 states. `kb_change_items`: `classification`, `confidence`, `ai_suggestion` (JSON), `final_edits` (JSON), `decided_by/at`, `admin_decision/note/by/at`, `admin_edited`, `process_status`, `publish_status`, `process_error`, with CHECKs. Downgrade maps new states back to Part 1 states.

## 10. API (all tenant-scoped from the token; the client never supplies a tenant)
Staff: `POST /ai-changes`, `POST /changes/{id}/analyse`, `POST /changes/{id}/items/{doc}/decision`, `GET /changes/{id}/audit`. Admin: `POST /changes/{id}/start-review`, `PUT /changes/{id}/items/{doc}/edit`, `POST /changes/{id}/items/{doc}/review`, `POST /changes/{id}/publish` (202, poll), `POST /changes/{id}/reopen`, `POST /changes/{id}/cancel`, `GET /changes/{id}/audit`; `GET /changes` now takes the new statuses and returns counts. Part 1 routes are unchanged.

## 11. Audit
Every step writes an append-only `kb_events` row limited to `audit.SAFE_KEYS` (ids, counts, statuses, versions, classification, confidence): initiator, analysis start/finish/failure, questions, suggestions, each decision, submission, review, admin edit, approve/reject/return/cancel, release start, per-document processing, publication, failure, retry and final versions. **Document text, replacement text and discard reasons never reach the audit.** Reject and cancel keep the history.

## 12. Evaluation
`make eval-impact` runs 15 deterministic scenarios on synthetic documents (`tests/knowledge/test_ai_eval.py`) and prints the metrics. `make eval-impact REAL=1` additionally runs an optional check against the real local model (needs Ollama; it asserts safety properties, not quality). Results are in PART2_IMPLEMENTATION_REPORT.md. They describe the deterministic engine and guards, not language-model quality.

## 13. Known limitations
Rule engine and sibling lexicon are deliberately small and English-only. Re-running the analysis discards earlier decisions. Dates inside prose are recognised only in common formats. Only one-line replacements are produced. Draft versions from a failed, later-edited release remain as drafts. The real LLM path is tested only with stub models in the sandbox.
