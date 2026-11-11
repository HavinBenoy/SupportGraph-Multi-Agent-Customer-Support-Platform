# M6 — Final completion: knowledge workflow refinement and product polish

This is the last M6 round. It builds on Part 2, its patch-ups, Part 3 and the add-on. Wherever something already worked, it was kept as it was. Discovery, the gateway, the
validators, immutable versions, the rule that drafts never reach RAG, the two human approvals, audit, concurrency fencing, stale protection, tenant isolation and RBAC
all stay as they were.

**Applying it.** `unzip -o supportgraph-m6-final.zip`. There is **no migration** (alembic stays at 0012) and **no new dependency**. Your `.env`, secrets and data are not in the zip.

## 1. What changed, by requirement

### 1.1 High-recall discovery (unchanged on purpose)
No discovery threshold was raised, and the pipeline was not simplified. All of these are as the add-on left them: the independent signals (old value, evidence,
≥ 50% of subject words, semantic ≥ `SG_KB_AI_SEMANTIC_ADMIT` 0.45, semantic + subject word, title), the sibling safeguard (`SG_KB_AI_SEMANTIC_ADMIT_UNKEYED` 0.70), the
trace and the limits. Multi-task requests now run discovery **once per task**, so every task keeps its own recall.

### 1.2 Multi-task change requests (new: `app/knowledge/impact/tasks.py`)
1. **Decomposition.** A request that could hold more than one change asks the impact model for a **schema-validated task list**. This is `TaskList`/`ChangeTask` (Pydantic),
   produced through structured output, so it is never parsed from free text. Each task has: id, description, subject, attribute, new value, existing value, change type,
   context and dependencies. Before the list is used, it is **grounded**: every content word must come from the request, and together the tasks must cover every value in
   the request (amounts, "X sir" names, times). The deterministic splitter always runs as well. If the model's answer is broken, invents something, or covers less than
   the rules, the rule split is used and the request records why (`analysis.decomposition.note`). A value that no task carries is reported as *uncovered*; it is never
   silently dropped. A single change, including "from A to B", stays one task and costs no model call.
   - Bug found and fixed while doing this: `LlmImpactModel` never exposed its chat, so in production the model was never asked. It is now (`self._chat`).
2. **Sequential application on a working copy.** For each document, the tasks that nominated it run in order on an **unpublished working copy**. Task 5 reads the text
   task 1 produced, so "Gen AI faculty → Mohanan sir" and "Gen AI timing → evening" on the same sentence both land. The item keeps **one** proposal against the published
   version, plus **task-level provenance** (`ai.tasks = [{task, classification, reason, edits}]`). Nothing is written to the knowledge base.
3. **Final check.** Every task gets an outcome. A task whose new value is no longer in the final text was overwritten by a later task, and is reported as such.
   Two tasks that change the same attribute of the same subject in one document are named as a conflict, and duplicates are merged. This is a deterministic check of
   the proposal, **not an approval**.
4. **Task status shown to staff and admins:** *Completed proposal · Needs human confirmation · No relevant document found · Clarification required · Processing failed.*

### 1.3 Precise diff visualisation and three versions (new: `frontend/src/components/workspace/Compare.tsx`)
- A **word-level diff** is computed in the browser **after generation**, for display only. It uses Myers O(ND) on word, space and punctuation tokens, after trimming the
  common prefix and suffix. It never changes what was generated, validated or stored.
- **Fixed-height, scrollable panels** show the *complete* text, with only the changed words marked. Deletions are red and struck through; additions are green and
  underlined. In the manual panel, deletions are orange and additions blue. Each panel opens at its first change. *Side by side* and *Inline* layouts are both available.
- **Original | AI proposal | Manual revision.** The AI proposal is rebuilt from the AI's own stored patches (`ai_suggestion`, which is never overwritten). The third
  panel appears **only** when a person changed the AI's proposal (staff "Edit manually", or the administrator's final edit), and it marks only AI → manual differences.
- Staff ("Proposed changes") and administrators ("Documents" tab) use the same component.

### 1.4 Review UI: document-first and compact
- Each document is one compact row: name, the tasks that touched it (or the AI's reason), classification, decision, and **Action required**. A click expands it; a
  second click collapses it. **Only one is open at a time**, and none open automatically.
- Documents that were examined but left unchanged (related with no change, or not relevant) sit in a separate **"Examined, no change needed"** summary.
- **After Accept**, the Accept button is gone. The row says "✓ Accepted — this proposal is part of the request" and offers **Submit for review** (when every document
  is decided), **Edit manually** and **Discard**. After a manual edit, it offers **Edit again**, **Use the AI proposal instead** and **Discard**. The state is persisted by
  the server (`decision`). Duplicate acceptance can't happen, because the button no longer exists. Duplicate submission can't happen either: the buttons are disabled
  while busy, and the server's status transition is atomic. The administrator receives the final accepted or manual revision, with the AI proposal kept for comparison.

### 1.5 Staff Console for administrators (restored)
The app bar shows **Staff console** to administrators again, and `/agent` admits `staff` and `admin`. The Staff console header has **Go to Administration →**, and
Administration has **Open the Staff console →**. Backend RBAC is unchanged: staff routes already accepted administrators.

### 1.6 No folders
- The Knowledge Base is now **one flat, searchable document list**. Search covers names and content. Category can be filtered, and administrators can also filter by
  status (live, drafts, archived). Document rows show category, live version and last update. Version history, compare, "Make changes", "Request deletion" and (for
  admins) "Manage versions", "New document" and "Drafts & document administration" all remain.
- Removed from the UI: root and nested folders, creating, renaming, moving and deleting folders, breadcrumbs, folder navigation, and "move to folder".
- Removed from the client: the five folder API calls.
- **Backend:** the folder *write* routes now answer **410 Gone** unless `SG_KB_LEGACY_FOLDERS=true`. **Nothing was migrated or deleted.** The `kb_folders` and
  `kb_doc_placement` rows stay as they were, so the change is fully reversible. Document IDs, versions, publication states, audit and relationships are untouched.
  `GET /tree` still lists every document. The legacy folder tests still run, with the flag switched on.

### 1.7 Deleting a document needs an administrator's approval (new)
- **Request.** Staff (`POST /api/staff/knowledge/documents/{id}/deletion-request`) or an admin (`/api/admin/...`) can request deletion. An admin must also send
  `confirm: true`. The request needs a reason and the current published version. The result is a change request of kind `deletion` in the administrators' queue, holding
  the document name, ID, version, requester and reason. **Nothing is removed at this point.** Only one deletion can be open per document.
- **Review.** The administrator can approve (nothing removed yet), reject or cancel. *Return for revision* is refused, because a deletion has nothing to edit. Editing
  the item, analysing it or the legacy *apply* are refused too. Approval **rechecks the version**: if the document was published again since the request, the deletion
  is refused as stale.
- **Execution.** *Archive the document now → Confirm — archive* runs the publish step. That step rechecks staleness, archives the document through the existing
  lifecycle (`pipeline.archive_document`), and removes its live chunks so it leaves RAG at once. Every version and the audit history are kept, and there is **no hard
  delete**. The document can be unarchived later.
- **Audit:** `document_deletion_requested` and `document_deletion_published`, plus the existing `document_archived`.
- **Repeats and isolation.** A repeated click returns `already_published`. Four racing clicks on PostgreSQL archive exactly once. Another tenant gets 404.
- **No uncontrolled bypass.** The one-click **Archive** of a *published* document was the old bypass. It now answers 409 ("use Request deletion") unless
  `SG_KB_DIRECT_ARCHIVE=true`. Drafts and unpublished documents can still be archived directly. **Unpublish** is kept: it is an immediate, reversible, audited safety
  stop, not a deletion. With `SG_KB_REQUIRE_SEPARATE_REVIEWER=true`, an admin cannot approve their own deletion request.

### 1.8 Chat and Voice
- **Chat:** avatars and message bubbles, source chips, a "Thinking…" indicator with animated dots, starter suggestions (they fill the box and never auto-send), a
  rounded composer with its keyboard hint, and a **Try again** button when the service can't be reached.
- **Voice:** a call "orb" that shows the current state: pulsing rings while listening or hearing, level bars while speaking, a pulse while thinking or connecting, and
  grey when muted or idle. It uses the same bubbles and chips as Chat.
- Everything is CSS only, and all motion stops under `prefers-reduced-motion`. Every existing label, role and behaviour is unchanged, and all chat and voice tests pass.

### 1.9 Admin analytics (impactful metrics only)
- **Support operations.** Added to `GET /api/admin/analytics`: conversations; resolved by the AI; escalated; AI resolution rate; voice takeover rate; failed calls;
  escalation resolution rate; median reply time (p50/p95); median time to resolve; unresolved now.
- **Knowledge governance** (new `GET /api/admin/analytics/knowledge?days=`):
  - requests by kind (AI-assisted, manual, deletion) and by status
  - documents examined, AI proposals, documents affected
  - staff accepted, edited and discarded; acceptance rate and manual-edit rate
  - admin approved and rejected; approval rate and median review time
  - published changes, split into AI-unmodified and human-modified
  - median time from request to publication
  - failed and still-open requests
  - **funnel:** request → proposal ready → submitted → approved → published
  - requests per day
  - **staff contributions** table: requests, AI accepted and edited, submitted, approved, approval rate, published, median review time
- **Definitions.** Every metric has a definition (hover over a figure, or open "How these are calculated"). A metric with no data shows **n/a**, never a made-up 0.
- **Privacy.** Admin-only and tenant-scoped. No document text, descriptions, reasons or customer data are included; staff usernames, which are internal account names,
  are the only identities.
- **Charts.** Single-hue bars and columns from the existing chart components. Each bar states its value as text and has a hover title.

## 2. API summary (new or changed)

| Method & path | Who | What |
|---|---|---|
| `POST /api/staff/knowledge/documents/{id}/deletion-request` `{reason, expected_version}` | staff, admin (`confirm: true`) | request a deletion (status `submitted`) |
| `POST /api/admin/knowledge/documents/{id}/deletion-request` `{reason, expected_version, confirm: true}` | admin | the same, explicitly confirmed |
| `POST /api/admin/knowledge/changes/{id}/publish` | admin | for a deletion request: archives the document; outcome `deleted` |
| `POST /api/admin/knowledge/documents/{id}/archive` | admin | a **published** document: 409 unless `SG_KB_DIRECT_ARCHIVE=true` |
| `POST/PUT/DELETE /api/admin/knowledge/folders…`, `PUT /documents/{id}/folder` | admin | **410** unless `SG_KB_LEGACY_FOLDERS=true` |
| `GET /api/admin/analytics` | admin | `support` block added (existing fields unchanged) |
| `GET /api/admin/analytics/knowledge?days=1..365` | admin | knowledge-governance metrics + definitions |
| `GET /api/staff|admin/knowledge/changes/{id}` | as before | `analysis.kind`, `deletion_reason`, `intents` (with `status`, `status_label`), `decomposition`, `final_check`; items: `ai.tasks` |

## 3. Environment variables (all optional; defaults shown)
```
SG_KB_DIRECT_ARCHIVE=false        # true restores the one-click archive of a published document (not recommended)
SG_KB_LEGACY_FOLDERS=false        # true re-enables the retired folder API
SG_KB_REQUIRE_SEPARATE_REVIEWER=false   # existing; now also applies to deletion requests
```
Nothing else changed. `SG_KB_AI_SEMANTIC_ADMIT` (0.45) and `SG_KB_AI_SEMANTIC_ADMIT_UNKEYED` (0.70) keep their values.

## 4. Migration
None. No schema change. Folder data and placements are left as they are; deletion requests use the existing `kb_change_requests` and `kb_change_items` (marked by `analysis.kind = "deletion"` and `stats.delete`).

## 5. Tests (run in this sandbox; scripted model, mocked UI; **no real Qwen**)

| Suite | Result |
|---|---|
| backend `tests/knowledge` + `tests/rag` (incl. the impact eval regression) | **728 passed, 3 skipped** |
| backend everything else (api, security, step4, calls, voice, graph, db, unit) | **1362 passed, 77 skipped** (skips need external services: PostgreSQL, Ollama, Langfuse) |
| PostgreSQL 16 (`tests/db` incl. the new `test_postgres_m6_final.py`, + trace/generalized/M6 HTTP tests) | **127 passed** |
| `make eval-impact` | 18 scenarios · precision 1.000 · recall 1.000 · exact replacements 16/16 · wrong edits 0 |
| frontend `tsc --noEmit` / `vitest run` / `next build` | clean / **252 passed** / built, no warnings |
| Playwright on the production build (API mocked) | review page with three panels at 1440 px; phone 390 px with no horizontal overflow (a pre-existing mobile grid overflow was found and fixed: `minmax(0,1fr)` + `max-width:100%`, and screen-reader labels contained by `position:relative`) |

**New tests:**
- **Backend.** `test_m6_tasks.py` (16): decomposition; the model's task list through the real application path; invented or shorter lists refused; one change stays
  one task; six tasks across five documents with provenance and the working copy; a failed task not hiding the others; a task with no document.
  `test_m6_deletion.py` (7): nothing removed until approve + publish; out of RAG; versions and audit kept; idempotent; stale refused; reject and cancel; no return or
  edit; admin must confirm and still goes through review (and separate-reviewer); RBAC and tenant; folder API 410.
  `test_m6_governance.py` (3): funnel and rates from the real workflow, n/a for missing values, no text, admin-only, tenant-scoped, per-conversation support metrics.
  `tests/db/test_postgres_m6_final.py` (2): racing publish archives once, stale refusal, governance on PostgreSQL.
- **Frontend.** `Compare.test.tsx` (5): word diff, lists and tables, three versions, panels. `AiKnowledge.test.tsx`: accordion, accept state, three-version card,
  task panel, deletion review. `KnowledgeWorkspace.test.tsx`: flat list, filters, staff and admin deletion requests. `KnowledgeAdmin.test.tsx`: no one-click archive of a
  live document. `AdminAndAccess.test.tsx`: both analytics sections, definitions, n/a. `Part3.test.tsx`: admins see the Staff console. `ChatWidget.test.tsx`:
  suggestions and the thinking indicator.

## 6. Real Qwen — not run here (no Ollama in the sandbox). Exact commands for your machine
```bash
cd backend
# multi-task decomposition with the REAL impact model + the sequential working copy (scenarios 18-21)
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --tasks 2>&1 | tee /tmp/sg-tasks-1.7b.txt
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b-instruct-2507-q4_K_M ../.venv/bin/python scripts/judge_probe.py --tasks 2>&1 | tee /tmp/sg-tasks-4b.txt
# the existing 17-scenario editing suite (unchanged)
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --suite --runs 2 2>&1 | tee /tmp/sg-qwen-1.7b.txt
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b-instruct-2507-q4_K_M ../.venv/bin/python scripts/judge_probe.py --suite --runs 2 2>&1 | tee /tmp/sg-qwen-4b.txt
```
Through the app, as staff, try "AI-assisted change" with:
1. *"Change the fees of Gen AI to 15000 rupees and the fees of Machine Learning to 6000 rupees."* Expect two tasks, one per document. Check that the decomposition says
   `model` (or `rules`, with the reason).
2. *"Change the Gen AI faculty to Mohanan sir, Machine Learning faculty to Gopan sir, Python faculty to Jobi sir and DSA faculty to Karthik sir. Also change Gen AI class
   timing to evening and German class timing to morning 9 AM."* Expect six tasks with their statuses, and the Gen AI row showing *Task 1 · Task 5* with both changes in
   one proposal.
3. Edit one proposal manually and check the third panel. Submit it, and as admin open Change requests → Documents.
4. On a document, *Request deletion*; then as admin approve it and archive. The document is gone from chat answers and still listed as Archived.

## 7. Known limitations
- The word diff is for display only. On a very different rewrite (more than 4000 edit steps) it falls back to showing the middle as removed + added.
- The decomposition model call adds one call to a multi-change request. On a CPU-only laptop that is a few seconds with qwen3:1.7b, more with 4B. When the model is
  unavailable, the rules split is used.
- The final check compares values. It cannot judge free-text rewrites ("make it friendlier"); for those, the human reviews remain the safeguard.
- `Unpublish` still removes a document from answers immediately without a second approval. This is deliberate: it is reversible, audited, and the emergency stop.
- Analytics count what the system did; answer quality still needs the labelled evaluations (`make eval-routing`, `make eval-impact`).
- No real Qwen result is claimed for this round.
