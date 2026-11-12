# M6 ACCEPTANCE REFINEMENTS + M7 (`supportgraph-m6r-m7.zip`, 2026-11-12)

Delta ZIP. Apply it from the project root with `unzip -o supportgraph-m6r-m7.zip`, then run `make db-migrate` (one additive migration, **0013**). There are no new
dependencies, and the ZIP contains no `.env`, data, recordings or build output. Details: **docs/M6_REFINEMENTS.md**, **docs/M7.md**, **TEST_REPORT.md**.

## What changed

**2.1 Back navigation from a document** (`frontend/src/lib/nav.ts`, `workspace/KnowledgeWorkspace.tsx`)
- **Cause.** The back arrow called `history.back()` unconditionally. A document that was *restored* rather than opened from the list returned to whatever came
  before it, often Change requests. "Restored" means the open document came back after switching sub-tabs, or from a bookmark or a reload.
- **Fix.** Entries the workspace pushes carry `history.state.sgOrigin`, and Back uses `history.back()` only for those. Anything else falls back to the document list
  with `replaceState`, so earlier history is untouched. Opening from *My change requests* returns there.
- **Checked.** Unit tests, plus a Playwright run of the exact repro on the production build.

**2.2 Live is the default status filter for administrators.** The URL stores only a non-default choice, so reload and Back restore it. *Every status* is an explicit
option. Staff are unchanged: they have no status filter.

**2.3 Real editor attribution** (`app/knowledge/changes.py`, `workspace/ui.tsx`, `AdminRequests.tsx`, `Decisions.tsx`)
- The attribution comes from the persisted audit trail, not from the viewer:
  - **Data:** `edit_history()` feeds the new item fields `staff_edited_by`, `admin_edited_by`, `last_edited_by`, `last_edited_at`, `last_edit_role` and `editors`.
  - **Labels:** "Edited by staff7", "Final wording edited by admin2", and "you" only when the persisted editor is the viewer.
  - **Proposer vs. editor:** "Requested by …" is shown separately from "last edited by …".
- No migration. The API change is additive: new fields only.

**2.4 Permanent deletion** (new `app/knowledge/purge.py`, migration `0013_kb_legal_hold.py`, routes in `app/api/routes/knowledge.py`)
- **Separate from Archive.** It is a distinct administrator operation, and Archive behaviour is unchanged.
- **Only for archived or never-published documents.** A live document must first be archived through the approval-based deletion request.
- **Blocked by:** a legal hold (new, per document), the retention period after archiving, open change requests, pending proposals, M5 recording holds, in-flight
  processing, and the feature switch.
- **Confirmation:**
  - **Preview first:** the administrator sees what will be removed (`purge-preview`) before deleting.
  - **Typed identity:** they must type the exact id and title and tick an acknowledgement.
  - **Stale-proof:** the request carries the page's row_version as a compare-and-set, which is also the PostgreSQL row lock.
- **Removed, in one transaction:** versions, staged and live passages with their embeddings, the document's items in closed change requests, finished proposals, the
  placement and the document row. References inside analyses are scrubbed.
- **Kept:** the content-free audit trail, plus one `document_purged` tombstone. Disk ingest, the import wizard and document creation all refuse a purged id.
  Repeating the request returns `already_deleted`.
- **New endpoints:** `GET /documents/{id}/purge-preview`, `POST /documents/{id}/purge`, `PUT /documents/{id}/legal-hold`.

**M7: the knowledge-management UI over the M6 API** (`frontend/src/components/KnowledgeAdmin.tsx`; docs/M6.md §13 is the scope, see docs/M7.md for the discrepancy
with the roadmap)
- **Documents table:** server-side status and category filters, pagination, legal-hold badge, and empty, loading and error states.
- **Version comparison:** any two versions, using the server's `/compare` statistics plus a word-level view of both full texts (`TextCompare` in `Compare.tsx`).
- **Review queue:** feedback cards plus a table of all proposals with a state filter.
- **Audit log:** tenant-wide, with document, action and actor filters, and *Load older* via the `before_id` cursor.
- **Import from disk:** a wizard that lists the files, asks for confirmation, then shows the result.
- **Legal-hold and Permanently-delete dialogs.**
- `/meta` gains `audit_actions`, `purge_enabled` and `purge_min_archived_days`.

## Files
- **Backend, new:**
  - `app/knowledge/purge.py`
  - `app/db/migrations/versions/0013_kb_legal_hold.py`
  - `tests/knowledge/{test_m6_purge, test_m6_attribution, test_m7_admin_api}.py`
  - PostgreSQL tests added to `tests/db/test_postgres_m6_final.py`
- **Backend, modified:**
  - `app/db/models.py`: 4 legal-hold columns
  - `app/knowledge/changes.py`: editor attribution
  - `app/knowledge/store.py`: legal hold in `doc_view`; a purged id cannot be reused
  - `app/knowledge/importer.py` and `app/rag/ingest.py`: skip purged ids
  - `app/knowledge/audit.py`: 3 new actions
  - `app/config.py`: 2 settings
  - `app/api/routes/knowledge.py`: 3 routes and the `/meta` fields
  - `tests/db/test_migrations_m6.py`: the head is now 0013, plus a 0013 upgrade/downgrade test
- **Frontend, new:** `components/KnowledgeAdminM7.test.tsx`.
- **Frontend, modified:**
  - `components/KnowledgeAdmin.tsx`: M7 tabs and dialogs
  - `components/workspace/{KnowledgeWorkspace, AdminRequests, Decisions, Compare, ui}.tsx`
  - `lib/{nav, api, types}.ts`
  - `app/globals.css`
  - tests: `KnowledgeAdmin`, `KnowledgeWorkspace`, `AiKnowledge`
- **Docs:** `docs/M6_REFINEMENTS.md` (new), `docs/M7.md` (new), `docs/ROADMAP.md`, `docs/M6.md` §13, `.env.example`, `TEST_REPORT.md` (new), three screenshots.

## Configuration (safe defaults)
- `SG_KB_PURGE_ENABLED=true`: permanent deletion is still gated by every rule above.
- `SG_KB_PURGE_MIN_ARCHIVED_DAYS=0`: set it, for example to 30, to enforce a retention period.

## Backward compatibility
- **Migration 0013 is additive:** 4 columns with constant or NULL defaults. Existing documents start without a hold, and the downgrade drops only those columns.
- **No endpoint was removed or renamed.** One client signature changed: `kbDocs(token, {q, status, category, page, pageSize})`, an internal TypeScript client.
- **Behaviour change:** a document id that was permanently deleted can't be created again; the server refuses with 409.

## Limitations
- Chat transcripts that once quoted a purged document are not modified: they are customer records.
- A disk file is never deleted by the server; its id is blocked instead.
- A cached answer becomes unreachable at once and expires by TTL.
- The `make ingest` CLI prints nothing new for purged ids: they are only listed in `IngestReport.purged_skipped`.
- No real Ollama run was done in this round: the changes don't touch the LLM path.

---

# M6 — FINAL COMPLETION (`supportgraph-m6-final.zip`, 2026-11-11)

Apply over the project with `unzip -o supportgraph-m6-final.zip`. This is the one consolidated M6 delivery: Part 2, the patch-ups, Part 3, the add-on, and this final round.
**No migration** (alembic 0012), **no new dependency**, and no `.env`, secrets or data. Full report: **docs/M6_FINAL.md**.

- **Multi-task requests.** The model returns a schema-validated, grounded task list; the rules are the fallback and anything uncovered is reported. Tasks run in order on
  an unpublished working copy, so two tasks on one sentence both land. Each document keeps task-level provenance. A final cross-task check catches overwrites, conflicts
  and duplicates. Every task shows its status: Completed / Needs confirmation / No document / Clarification / Failed. Also fixed: the impact model never exposed its chat,
  so the model-based split could not run.
- **Diffs.** A word-level diff in fixed-height, full-text panels marks only the changes. It shows Original | AI proposal | Manual revision; the third panel appears only
  after a human edit and marks only AI → manual differences. The AI proposal is never overwritten.
- **Review list.** Compact rows that expand one at a time; unchanged documents sit in a separate summary. After Accept there is no second Accept; the row offers Submit
  for review, Edit manually and Discard. Staff and admins see the same view.
- **Admins keep the Staff console**, with links between it and Administration.
- **No folders.** The knowledge base is a flat, searchable, filterable document list. The folder write API returns 410 unless `SG_KB_LEGACY_FOLDERS=true`. No folder data
  was deleted.
- **Deletion needs approval.** Staff or admins request it with a reason and the current version. An administrator approves (version rechecked), then archives. The
  document leaves RAG, its versions and audit are kept, and nothing is hard-deleted. Requests can be rejected or cancelled, and staleness is protected. The one-click
  archive of a published document now returns 409 unless `SG_KB_DIRECT_ARCHIVE=true`.
- **Chat and Voice polish.** Bubbles, avatars, source chips, a thinking indicator, suggestions, and a call orb with listening, speaking and thinking states. Behaviour is
  unchanged.
- **Analytics.** Support operations: conversations, AI resolution, escalations, takeover rate, resolution time and more. Knowledge governance: funnel, staff and admin
  rates, manual-edit rate, time to publication, AI vs human-modified, and staff contributions. Each metric has a definition, empty values show n/a, and no text is exposed.
- **Mobile.** A layout overflow at 390 px in the knowledge workspace was found and fixed.

| Suite (this sandbox; scripted model; mocked UI) | Result |
|---|---|
| backend knowledge + rag + eval regression | 728 passed, 3 skipped |
| rest of the backend | 1362 passed, 77 skipped (external services) |
| PostgreSQL 16 | 127 passed |
| `make eval-impact` | precision 1.000 · recall 1.000 · 16/16 exact · 0 wrong edits |
| frontend tsc / vitest / next build | clean / 252 passed / built (no warnings) |

**No real Qwen was run** (there is no Ollama here). Run these on your machine:

    cd backend
    SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --tasks
    SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b-instruct-2507-q4_K_M ../.venv/bin/python scripts/judge_probe.py --tasks
    SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --suite --runs 2
    SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b-instruct-2507-q4_K_M ../.venv/bin/python scripts/judge_probe.py --suite --runs 2

**Files in this round:**
- backend:
  - `app/knowledge/impact/{tasks (new), analyse, evaluate, spec}.py`
  - `app/knowledge/{changes, release, audit}.py`
  - `app/api/{analytics, governance (new)}.py`
  - `app/api/routes/{admin, knowledge, knowledge_workspace}.py`
  - `app/config.py`, `scripts/judge_probe.py`
  - tests: `tests/knowledge/{test_m6_tasks, test_m6_deletion, test_m6_governance}.py`, `tests/db/test_postgres_m6_final.py`, plus small updates to the folder and
    publication tests and `institute.py`
- frontend:
  - `src/components/workspace/{Compare (new), DeletionRequest (new), Decisions, AdminRequests, Requests, KnowledgeWorkspace}.tsx`
  - `src/components/{AdminDashboard, Bars, ChatWidget, VoiceCall, KnowledgeAdmin, Shell, StaffConsole, AdminConsole}.tsx`, `src/app/agent/page.tsx`
  - `src/lib/{api, types}.ts`, the CSS files and their tests
- docs: `M6_FINAL.md`, `.env.example`, three screenshots.

---

# M6 — FINAL + add-on (`supportgraph-m6-final.zip`, updated 2026-11-11)

Apply over the project: `unzip -o supportgraph-m6-final.zip`. It is the consolidated M6 delivery: Part 2, its patch-ups, Part 3, and this add-on. No migration (alembic 0012), no new dependency, no `.env`/data.
**This add-on: docs/M6_ADDON.md.**

- **Case A** ("change the gen ai course fees to 20000 rupees and machine learning course fees to 12000 rupees"). The request was never split into two intents, and the two amounts were read as 20000 → 12000. Now the splitter cuts at every clause boundary and re-glues pieces that have no value of their own (keeping the original separators), a verb-less piece borrows the first verb, and two "to <amount>" are never old → new. Each intent is discovered, validated, traced and verified with its own values only.
- **Case B** ("Change the work experience of santhosh sir to Mechanical industry", semantic 0.59 vs 0.60). A model parse named the new value as the subject. Now the description's subject words are always searched, and values and time words are excluded. `SG_KB_AI_SEMANTIC_ADMIT` is now **0.45** and an independent path; sibling subjects need `SG_KB_AI_SEMANTIC_ADMIT_UNKEYED` (0.70). Work-experience/career requests may correct "Previously he worked in …", flagged for review.
- **Chat for staff and admins.** Real RBAC change in `/api/chat`: they chat as a visitor of their own tenant, with no student identity and no access to students' tickets. The voice line is unchanged (students/visitors). Admins no longer see or land on the Staff console.
- **Change requests in a master-detail layout** (admin and staff). The list sits left and the detail right; the selection is kept in the URL; phones show one at a time with "Back to the list".
- **Hero "s".** The italic overhang was clipped by `background-clip:text`; padding plus a matching negative margin fixes it. Checked at 1366/820/390 px.
- **Motion.** CSS-only, transform/opacity: page/hero/section/detail/dialog/menu transitions and a loading pulse. All of it is off under `prefers-reduced-motion`.

Verification: Run in this sandbox. Every run uses a scripted model or a mocked UI; **no real Qwen was run**, because there is no Ollama here.

| Suite | Result |
|---|---|
| backend knowledge + rag + eval regression | 706 passed, 3 skipped |
| rest of the backend (api, security, step4, calls, voice, graph, db, …) | 1362 passed, 75 skipped (the skips need external services) |
| PostgreSQL (db + folders + M6 regressions + trace + chat) | 124 passed |
| impact eval | precision 1.000, recall 1.000, 16/16 exact replacements, 0 wrong edits |
| frontend `tsc` / `vitest` / `next build` | clean / 244 passed / built |
| Playwright (production build, API mocked) | hero "s" overhang room 9.6 / 6.9 / 5.0 px at 1366 / 820 / 390 px; change-request list and detail side by side on desktop, one at a time at 820 and 390 px, Back returns to the list; animation durations 1e-05s under reduced motion |

Real Qwen on your machine (17 scenarios; 16 = Case A's ML intent, 17 = Case B):

    cd backend
    SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --suite --runs 2 2>&1 | tee /tmp/sg-qwen-1.7b.txt
    SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b-instruct-2507-q4_K_M ../.venv/bin/python scripts/judge_probe.py --suite --runs 2 2>&1 | tee /tmp/sg-qwen-4b.txt

In the app, run these and read the Processing log:
1. Case A, the exact sentence: expect two "intent discovered" steps, and items for course (25000 → 20000) and Data-Science (9999 → 12000).
2. Case B: expect Data-Science "nominated", a model call on its section, and a flagged proposal.
3. As staff, then as admin: open Chat, then open your own area.

---

# M6 — FINAL: Part 2 patch-up + Part 3 (one consolidated ZIP: `supportgraph-m6-final.zip`)

Supersedes every earlier Part 2 zip. **No migration** (alembic stays at 0012). **No new dependency.** Your `.env`, secrets and data are not included.
Read: docs/PART2_FINAL.md §0b (discovery/multi-intent/editing regressions) and §0 (earlier root causes, processing trace); docs/PART3.md (landing page, redesign, navigation, folder management, Publishing tab).

## Part 2 patch-up (evidence: your staff-console failure and the real Qwen probe logs)
- **Discovery recall.** "Gen AI 20000 and ML 12000" was parsed as a single 20000 → 12000 change, and the discovery gate needed the old value or a title match. Now the request is split into **intents** (each discovered, evaluated, traced and verified on its own). Documents are admitted on **independent signals**: old value / evidence / ≥ 50% of the subject words / semantic ≥ 0.6 alone / semantic + a subject word / title. A sibling subject (Java for a Python change) is kept out.
- **Unrelated rewrite** ("offline" → "hybrid" for "online → hybrid"). "change A to B" with the same head noun is now old → new, and a run that writes the new value where the old one never was is rejected.
- **"Unchanged" for removals/corrections.** The prompt lists the lines search found, and the model gets one second look.
- **The verifier passed incomplete corrections.** It now fills a checklist, and its own contradictions turn into FAIL plus one retry with the reason. Negations are flagged.

## Part 3
- Design system for the whole app.
- App bar with a mobile menu.
- Animated landing page at `/` (chat moved to `/chat`).
- Two-panel login, page headers.
- URL-backed navigation (browser Back/Forward, reload) and Back buttons in every Knowledge Base view.
- Folder management: create top-level and nested, rename, move (no cycles or depth overflow), delete only when empty after confirmation. New backend endpoints are audited and tenant-scoped.
- Publishing tab removed. Its document capabilities moved into the workspace (New document here, Drafts & document administration, Manage versions).

## Files
backend: app/knowledge/impact/{spec,analyse,discover,edit,judge,evaluate,verify,trace,aliases,context}.py, app/knowledge/{workspace,audit,changes}.py, app/api/routes/knowledge_workspace.py, app/config.py, app/llm/gateway.py, scripts/judge_probe.py; tests/knowledge/{test_m6_regressions,test_folder_management,test_pipeline_trace,test_judge,test_generalized_flow}.py.
frontend: src/app/{globals.css,knowledge-workspace.css,page.tsx,chat/page.tsx,login/page.tsx}, src/lib/{nav.ts,api.ts,auth.tsx,types.ts}, src/components/{Shell,Landing,AdminConsole,StaffConsole,KnowledgeAdmin,LoginForm,Part3.test}.tsx, src/components/workspace/{KnowledgeWorkspace,KnowledgeWorkspace.test,ProcessingLog,Decisions,...}.tsx, src/test/setup.ts.
docs: PART2_FINAL.md, PART3.md, ROADMAP.md, screenshots/.

## New environment variables (optional)
`SG_KB_AI_SEMANTIC_ADMIT=0.6`, `SG_KB_AI_RECHECK_UNCHANGED=true` (plus the earlier Part 2 ones listed in docs/PART2_FINAL.md §7).

## Verification status
Run in this sandbox. All runs use a scripted model or a mocked UI; **no real Qwen was run**, because there is no Ollama here.

| Suite | Result |
|---|---|
| backend `tests/knowledge` + `tests/rag` + eval regression | 700 passed, 3 skipped |
| backend everything else | 1362 passed, 75 skipped (the skips need external services) |
| PostgreSQL (`tests/db` + folder management + M6 regressions + trace) | 109 passed |
| `make eval-impact` equivalent | 18 scenarios; precision 1.000, recall 1.000, 16/16 exact replacements, 0 wrong edits |
| frontend `tsc` / `vitest` / `next build` | clean / 241 passed / built |

Real-model check for you:
`SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --suite --runs 2`
and the same with `SG_LLM_MODEL_IMPACT=qwen3:4b-instruct-2507-q4_K_M`. That is 15 scenarios, including the reported fee case (14–15), the education removal (11), the past-industry correction (12), the multi-statement correction (13) and the unrelated "offline" passage (8).

Through the application: ask "The fees for gen ai is 20000 rs and that for machine learning is 12000 rs". Both documents should get a proposal, and the Processing log shows two intents.

---

# M6 Part 2 — FINAL (deep review): root causes fixed, prompts rewritten, processing trace

**This is the single final Part 2 ZIP.** It contains every file changed during Part 2 (it supersedes all earlier Part 2 zips). No migration (alembic stays at 0012), no new dependency, no infrastructure change; `.env`, secrets and data are not in it and are never touched.

Full write-up: **docs/PART2_FINAL.md** — §0 root causes and the processing trace, §1–§7 the generalized editor / adaptive context / validation / decisions / verification / configuration, §8 tests, §9 exact commands, §10 limitations.

## Root causes found (reported Santhosh-sir cases run through the real pipeline)
1. History guard rejected explicit corrections of the past (cases B, C) → `edit.targets_past`.
2. The prompt itself forbade changing the past and had no correction / "only" / removal-by-category guidance → prompt `edit-v2` with three worked examples.
3. Qwen *instruct* builds reject thinking (Ollama 400 "does not support thinking") → every section "unavailable" → automatic retry without thinking.
4. Relevance judged by shared words only (case A) → meaning-based acceptance near the candidate lines, flagged (strict when some change matches by words).
5. A long explanation (> 400 chars) or a long revision (> 2000 chars) raised a ValidationError and failed the WHOLE analysis → shortened / limit raised.
6. One bad line in a block of adjacent changed lines rejected its correct neighbours → line-for-line splitting.
7. No second chance for a correction that kept the contradicted statement → one feedback retry (`SG_KB_AI_EDIT_RETRIES`).
8. Outcomes were indistinguishable ("related, no change") → explicit reasons + per-request processing trace (log + stored + "Processing log" in the staff UI).
9. Acronym aliases built from scattered words ("swp") → only from a name said as one phrase.

## Files in this final round
backend/app/knowledge/impact/{trace.py (new), edit.py, judge.py, evaluate.py, analyse.py, discover.py, aliases.py}, backend/app/knowledge/changes.py (trace in the request detail), backend/app/config.py, backend/scripts/judge_probe.py (13 scenarios incl. the three reported ones), backend/tests/knowledge/{test_pipeline_trace.py (new), test_judge.py}, frontend/src/components/workspace/{ProcessingLog.tsx (new), ProcessingLog.test.tsx (new), Decisions.tsx}, frontend/src/lib/types.ts, frontend/src/app/knowledge-workspace.css, .env.example, docs/PART2_FINAL.md, CHANGES.md.

## New settings
`SG_KB_AI_EDIT_RETRIES=1`, `SG_KB_AI_TRACE_CONTENT=false`. Earlier in Part 2: `SG_KB_AI_CONTEXT_TOKENS`, `SG_KB_AI_JUDGE_NUM_CTX`, `SG_KB_AI_THINKING_RESERVE_TOKENS`, `SG_KB_AI_CONTEXT_MODE`, `SG_KB_AI_MAX_SECTIONS`, `SG_KB_AI_SEMANTIC_CHECK`, `SG_KB_AI_MAX_CHANGED_FRACTION`, `SG_KB_AI_MAX_REMOVED_FRACTION`; default `SG_KB_AI_JUDGE_MAX_TOKENS` 4096 (your `.env` still says 2500: remove or raise it).

## Verification status
Run here (scripted model): backend knowledge+rag+eval 683 passed / 3 skipped; rest of backend 1362 passed / 75 skipped; PostgreSQL 104 passed; frontend 235 passed, tsc and next build clean. **Real Qwen was NOT run** (no Ollama in the build sandbox): run `scripts/judge_probe.py --suite` (docs/PART2_FINAL.md §9).

---

# Part 2 closure - fix: documents found as relevant were not reaching the model / its result was invisible

Reported case: "Remove the education details of santhosh sir" listed the Data-Science document as "related, no change" with the rules' message "can only be drafted by the language model". Three generic causes, all fixed (no example-specific rule):

1. **The validator confused wording with relevance.** A revision was only accepted if the words that differ occurred in the request; "education details" shares no word with "He completed his B.Tech ...", so a perfectly good removal was dropped as "unrelated". Now (`edit.check_op`): if NO run of the revision matches the request's words, runs next to the candidate lines (<= 3 rows, or under the same small heading) are accepted **by meaning**, flagged "related to the request only by meaning: please confirm", and the second check runs because of the flag. If some run clearly matches, extra unrelated runs are still dropped; runs far from the candidates are still dropped; figures, headings, tables, history and duplicates are checked as before.
2. **Focus lines were chosen by subject words only.** A line that continues the person ("He completed ...") or sits under a heading that names the topic ("## Education") was never a focus line, and a document whose lines did not name the subject was dropped as "nothing concerns the change" before the model saw it. `_general_passages` now (a) lets a pronoun line inherit the line above, (b) counts the heading chain, (c) ignores action words ("remove", "add") as subject words, (d) falls back to the lines sharing the most request words.
3. **Model outcomes were invisible.** When the model was asked but its answer was cut off / unreachable / rejected, the reviewer saw the rules' generic text. Now the reason says so ("The language model was asked ... but its answer could not be used: <why>"), and when it returned the section unchanged ("... judged that the requested change does not apply to it").

Tests: 5 new in tests/knowledge/test_judge.py. Real Qwen was not run here (no Ollama in the sandbox): if a document is still "related, no change" after this, its reason now tells you which of the three cases it was.

---

# Part 2 CLOSURE: generalized AI knowledge editing + adaptive context (M6 Part 2, final)

Apply on top of the previous Part 2 delivery: `unzip -o ../supportgraph-m6-part2-final.zip`. **No migration** (alembic stays at 0012), **no new dependency**, no new infrastructure. `.env`, secrets and database content are not in the zip and are never touched. Full report, configuration reference and exact verification commands: **docs/PART2_FINAL.md**.

| Area | Files |
|---|---|
| Adaptive context | backend/app/knowledge/impact/context.py (new): budget (input / output / Ollama window kept apart), whole document -> sub-tree -> section -> blocks, merge/non-overlap |
| Free-form editor + validator | backend/app/knowledge/impact/edit.py (new): generalized prompt, extraction (truncation / malformed / empty), line-diff hunks, per-hunk and per-section checks; judge.py (rewritten: ask, semantic second check, cache); `minimal_edit` is gone |
| Integration | evaluate.py (`judge`, `_compose`, hunks -> `Edit(kind, flags)`, general passages, non-overlap), analyse.py (general changes instead of the "restate" gate, `vague`, `generalize`), spec.py (`general`), discover.py (admission, `Candidate.hunks`), verify.py (`compute_statements`), llm/gateway.py (`get_judge_chat` num_ctx/num_predict, `get_check_chat`), config.py |
| Frontend | src/lib/types.ts, src/components/workspace/{Decisions,PatchView,AdminRequests,ui}.tsx (Added / Removed / Rewritten, validator flags, statements-mode verification) |
| Tests | tests/knowledge/{test_judge,test_generalized_flow}.py (new/rewritten), frontend AiKnowledge.test.tsx |
| Tools / docs | backend/scripts/judge_probe.py (ten-scenario suite for real Qwen), .env.example, docs/PART2_FINAL.md, CHANGES.md |

Behaviour changes: (1) the model is asked the general instruction ("make the corresponding modification wherever it is applicable, otherwise return the section unchanged, preserve everything unrelated") and may add, change, rewrite or remove; (2) it receives the complete logical section within a token budget instead of +-3 lines / 1600 characters; (3) a revision is validated structurally (headings, tables, lists), semantically (optional second check) and deterministically (history, invented figures, duplicates, unrelated edits, deletions, truncation) before it can become a proposal; a model-only change is always `potential`; a disagreement with the rules is a question for a person; invalid output creates no edit; (4) changes that are not value replacements ("add ...", "remove ...", "now also ...") are analysed instead of being sent back; only a description that names no action is still answered with one question; (5) after publication a non-value change gets a statements check that says plainly that it cannot be proven complete.

New environment variables (all optional, see .env.example): `SG_KB_AI_CONTEXT_TOKENS`, `SG_KB_AI_JUDGE_NUM_CTX`, `SG_KB_AI_THINKING_RESERVE_TOKENS`, `SG_KB_AI_CONTEXT_MODE`, `SG_KB_AI_MAX_SECTIONS`, `SG_KB_AI_SEMANTIC_CHECK`, `SG_KB_AI_MAX_CHANGED_FRACTION`, `SG_KB_AI_MAX_REMOVED_FRACTION`. Changed default: `SG_KB_AI_JUDGE_MAX_TOKENS` 2500 -> 4096 (the answer is now the whole section).

Verification status: backend knowledge+rag 661 passed (after the relevance fix; remaining backend 1362 and PostgreSQL 81 were run before it, and the change touches only impact/edit.py and evaluate.py); remaining backend 1362 passed; PostgreSQL 81 passed; frontend 233 passed, tsc and next build clean. Real Qwen3 1.7B/4B was NOT run (no Ollama in the build sandbox): run `scripts/judge_probe.py --suite` (docs/PART2_FINAL.md section 9).

---

# Part 2 FINAL: recall, precision, section judge and verification (document-centric knowledge, no architecture change)

Apply on top of the previous Part 2 zips (the earlier "recall-and-verification" zip is NOT needed: this one contains everything): `unzip -o ../supportgraph-part2-final.zip`. **No migration** (alembic stays at 0012). No new dependency. Report: PART2_RECALL_REPORT.md; design: docs/PART2.md §4c.

| Area | Files |
|---|---|
| Impact analysis | backend/app/knowledge/impact/{aliases,scope,sections,scan,verify}.py (new); {spec,discover,evaluate,analyse}.py (changed) |
| Release / API / audit | backend/app/knowledge/release.py (follow-up check after publish), audit.py, api/routes/knowledge_workspace.py (`POST /api/admin/knowledge/changes/{id}/verify`) |
| Retrieval | backend/app/rag/{hybrid (new),retrieval,faq,answer_cache,chunking,rewrite}.py; app/graph/subgraphs/knowledge.py |
| Config | backend/app/config.py, app/tenants/schema.py, tenants/qspiders_sample/config.yaml (`entity_aliases`) |
| Tests | tests/knowledge/{qs_corpus,test_recall_v2,test_ai_recall_flow}.py, tests/rag/test_hybrid_and_tables.py (new); frontend AiKnowledge.test.tsx |
| Frontend | src/components/workspace/{Decisions,AdminRequests,ui}.tsx, src/lib/{types,api}.ts |
| Section judge (final) | backend/app/knowledge/impact/judge.py (new), evaluate.py (`LlmImpactModel.judge/_compose`), analyse.py (calls it), llm/gateway.py (`get_judge_chat`), config.py, scripts/judge_probe.py, tests/knowledge/test_judge.py |
| Docs | docs/PART2.md §4c, docs/PART2_MODEL_METHOD_ANALYSIS.md (why the terminal test worked and the app did not; decision), PART2_RECALL_REPORT.md |

New environment variables (all optional): `SG_KB_AI_JUDGE=uncertain|all|off` (the section judge; `off` = the previous per-passage structured call), `SG_KB_AI_JUDGE_THINKING=true`, `SG_KB_AI_JUDGE_TIMEOUT_S=300`, `SG_KB_AI_JUDGE_MAX_TOKENS=2500`, `SG_KB_AI_JUDGE_MAX_CALLS=16`, `SG_KB_AI_JUDGE_WINDOW_CHARS=1600`; `SG_KB_AI_DISCOVERY=v2|v1` (v1 = previous discovery, kept for comparison/fallback), `SG_KB_AI_VERIFY=true|false`, `SG_RAG_RETRIEVAL_MODE=hybrid|vector`, `SG_KB_AI_MAX_CANDIDATES` (20), `SG_KB_AI_MAX_PASSAGES` (12).
Behaviour changes: with a real model the analysis now asks it the way you tested it in the terminal (section text, "make the change only where applicable", thinking on) and verifies its answer line by line; also fixed after your first run: pronoun lines ("He has 3 years") and "Previously …" in the same line; analysis now inspects units (lines, list items, table rows with header, answers with question) instead of whole documents; passages carry a class and evidence; a contradicting or shared
line is never "definite"; after publication a follow-up check of the live base is stored on the release (advisory); chat retrieval is hybrid by default (set `SG_RAG_RETRIEVAL_MODE=vector` to restore the old behaviour).

---

# Part 2 final fix - impact analysis for changes that name no old value, and model-output safety

Apply on top of Part 2: `unzip -o ../supportgraph-part2-final-fix.zip`. **No migration** (alembic stays at 0012), no frontend change. Details and root cause: PART2_FINAL_FIX_REPORT.md.

| Area | Files |
|---|---|
| Fix | backend/app/knowledge/impact/{spec,passages,discover,evaluate,values}.py |
| Tests | tests/knowledge/{faculty,test_ai_faculty}.py (new), test_ai_eval.py (s16, s17), test_ai_real_model.py (rewritten, gated), test_impact_units.py (two expectations intentionally changed) |
| Tooling / docs | Makefile (`eval-impact REAL=1 MODELS=...`), docs/PART2.md (4a, 4b), PART2_FINAL_FIX_REPORT.md |

Behaviour changes: a change with only a new value ("X is changed to Y") is now analysed; model output is grounded and cross-checked by the rule engine; a model outage or a "no change" that contradicts hard evidence now yields a low-confidence *potential* with a grounded replacement instead of hiding the document; guard overrides replace the model's explanation.

---

# Part 2 - AI knowledge change intelligence and the integrated admin workflow

Apply on top of Part 1 (this zip contains only new and changed files): `unzip -o ../supportgraph-part2-ai-knowledge-intelligence.zip`, then `make db-migrate` (applies **0012**, additive and reversible). `.env`, data and volumes untouched. Design: docs/PART2.md; results: PART2_IMPLEMENTATION_REPORT.md.

| Area | Files |
|---|---|
| Migration | backend/app/db/migrations/versions/0012_ai_change_workflow.py (new): 15 request states, AI/decision/admin/release columns on `kb_change_requests` and `kb_change_items` |
| Models, config, gateway | backend/app/db/models.py; app/config.py (`kb_ai_*`, `llm_model_impact`, `impact_max_output_tokens`, `kb_release_stale_s`); app/llm/gateway.py (`get_impact_model`) |
| Impact analysis | backend/app/knowledge/impact/{values,spec,passages,discover,evaluate,analyse}.py (new) |
| Workflow services | backend/app/knowledge/changes.py (state machine, decisions, review, admin edit, audit view), release.py (new: process and publish as one unit), audit.py (new actions and safe keys) |
| API | backend/app/api/routes/knowledge_workspace.py (AI start/analyse/decision, start-review, admin edit/review, publish, reopen, cancel, audit) |
| Backend tests | tests/knowledge/{institute,aihelp,test_impact_units,test_ai_flow,test_ai_release,test_ai_eval,test_ai_real_model}.py, tests/db/test_postgres_ai_release.py (new); tests/db/test_migrations_m6.py (head = 0012) |
| Frontend | src/components/workspace/{AiChange,Decisions,PatchView,ui,AdminRequests}.tsx and AiKnowledge.test.tsx (new); KnowledgeWorkspace, Editor, Requests, AdminConsole; lib/api.ts, lib/types.ts; app/knowledge-workspace.css |
| Tooling / docs | Makefile (`test-ai`, `test-ai-pg`, `eval-impact`), backend/scripts/eval_impact_summary.py, docs/PART2.md, docs/ROADMAP.md, docs/TESTING.md, PART2_IMPLEMENTATION_REPORT.md |

Behaviour changes to existing code: the admin **Change requests** tab is now the full review -> approve -> process -> publish screen (it no longer sends you to the Publishing tab; Publishing remains for standalone documents). The Part 1 `apply` route still works. The two Part 1 frontend tests that exercised the old approve-then-apply screen were replaced by tests of the new screen. Everything else in M6 and Part 1 is unchanged.

---

# Part 1 - Knowledge Workspace (folders, read-only reader, manual change requests)

Apply on top of M6.1 (this zip is complete): `unzip -o ../supportgraph-part1-knowledge-workspace.zip`, then `make db-migrate` (applies **0011**, additive: four tables). `.env`, data and volumes untouched. Full design: docs/PART1.md; results: PART1_IMPLEMENTATION_REPORT.md.

| Area | Files |
|---|---|
| Migration | backend/app/db/migrations/versions/0011_knowledge_workspace.py (new): `kb_folders`, `kb_doc_placement`, `kb_change_requests`, `kb_change_items` |
| Models | backend/app/db/models.py: four models + status constants |
| Services | backend/app/knowledge/diffing.py, workspace.py, changes.py (new); store.py (`author`, folder filters, published-only search), audit.py (10 actions, 11 safe keys) |
| API | backend/app/api/routes/knowledge_workspace.py (new, registered in api/main.py); routes/knowledge.py (folder filters, richer staff document view) |
| Backend tests | tests/knowledge/{kwhelp,test_diffing,test_workspace_folders,test_change_requests}.py, tests/db/test_postgres_workspace.py (new); test_migrations_m4_1_m5.py and test_migrations_m6.py (drop lists, head = 0011) |
| Frontend | src/components/workspace/{KnowledgeWorkspace,Editor,Requests,DiffView}.tsx + test (new); AdminConsole.tsx (Workspace / Change requests / Publishing), StaffConsole.tsx (Knowledge tab); lib/api.ts, lib/types.ts; app/layout.tsx (fonts), app/knowledge-workspace.css (new) |
| Docs / tooling | docs/PART1.md (new), docs/ROADMAP.md, docs/TESTING.md, Makefile (`test-kw`, `test-kw-pg`), PART1_IMPLEMENTATION_REPORT.md |

Behaviour changes to existing code (all additive or bug fixes): staff document search now matches **published** text only (it could previously match unpublished draft text); the staff document view gains version/author/location fields; `store.create_version` accepts an optional `author`. Everything else in M6 is unchanged.

---

# M6.1 - Processing ownership (fencing) fix for the knowledge store

Apply on top of the first M6 delivery (or from scratch with this zip, which is complete): `unzip -o ../supportgraph-m6-complete.zip`, then `make db-migrate` (applies 0009 if not yet applied, and **0010**). Additive: one column. `.env` untouched, nothing deleted, no data reset.

**Concern confirmed.** `process_version` guarded completion/failure with `status = 'processing'` only, so a stale worker could not be told apart from the worker that took its claim over. Reproduced with deterministic tests on the old code: a stale worker finishing first committed its result; a stale worker failing turned the new worker's live claim into `failed`; a stale worker failing after the new worker completed deleted the new worker's staged chunks and reported `failed`; archiving mid-processing stranded the version in `processing`.

**Fix.** `kb_versions.processing_attempt` (fencing token, +1 per claim). Every completion/failure is a compare-and-set on `status='processing' AND processing_attempt=<mine>`, first in its transaction; staged-row changes and audit events happen only if it matched. A worker that lost ownership gets 409, writes nothing except one `ingestion_superseded` audit event. Audit events now carry `attempt`. Archive during processing ends the attempt cleanly as `failed`. Full reasoning and alternatives: docs/M6.md section 4a.

| File | Change |
|---|---|
| backend/app/db/migrations/versions/0010_kb_processing_attempt.py (new) | adds `kb_versions.processing_attempt INTEGER NOT NULL DEFAULT 0`; downgrade drops it |
| backend/app/db/models.py | `KbVersion.processing_attempt` |
| backend/app/knowledge/pipeline.py | fenced `process_version` (claim increments, CAS on attempt for complete/fail, owner-only staged-row writes, superseded audit, archive-during-processing) |
| backend/app/knowledge/audit.py | action `ingestion_superseded`; summary keys `attempt`, `current_attempt` |
| backend/tests/knowledge/fencing.py, test_processing_fencing.py (new) | 9 deterministic ownership scenarios (SQLite) |
| backend/tests/db/test_postgres_m6.py | the same 9 scenarios on PostgreSQL 16, plus the 0010 migration test |
| backend/tests/db/test_migrations_m6.py | head is 0010; 0010 up/down/up test (SQLite) |
| docs/M6.md, docs/TESTING.md, M6_IMPLEMENTATION_REPORT.md | fencing section, tests, results |

Backward compatibility: API unchanged except that a superseded process request now returns 409 instead of (in some interleavings) a wrong success/failure; the audit gains one action and two summary keys. Nothing else changed.

---

# M6 - Knowledge store, document versioning and audit (one change set)

Apply (from the project root): `unzip -o ../supportgraph-m6-complete.zip`, then `make db-migrate` (migration 0009, additive). No new dependency, no model/embedding/retrieval change, `.env` untouched, nothing deleted, no data reset. Full write-up: **docs/M6.md** and **M6_IMPLEMENTATION_REPORT.md**. The M4.1 + M5 section further down is kept as history.

## Files created
| File | Purpose |
|---|---|
| backend/app/db/migrations/versions/0009_knowledge_store.py | migration 0009: `documents.kb_version`; tables `kb_documents`, `kb_versions`, `kb_version_chunks`, `kb_proposals`, `kb_events` |
| backend/app/knowledge/__init__.py, errors.py | package; `KbError` family mapped to HTTP statuses |
| backend/app/knowledge/validation.py | ids, titles, categories, tags, change notes, safe file names/MIME, size and chunk limits, checksum |
| backend/app/knowledge/store.py | documents and immutable versions: create, version, restore, metadata, list/search, compare, views, compare-and-set `bump` |
| backend/app/knowledge/pipeline.py | process (three-phase, claim/stale take-over), atomic publish, unpublish, archive, unarchive |
| backend/app/knowledge/proposals.py | approved feedback -> draft proposal -> review -> publish link |
| backend/app/knowledge/importer.py | bring disk articles under the store (idempotent) |
| backend/app/knowledge/audit.py | append-only audit with an allow-listed summary |
| backend/app/api/routes/knowledge.py | admin and staff routers |
| backend/tests/knowledge/*.py (6 files), backend/tests/db/test_migrations_m6.py, backend/tests/db/test_postgres_m6.py | tests (see docs/TESTING.md) |
| frontend/src/components/KnowledgeAdmin.tsx, KnowledgeAdmin.test.tsx | minimal admin Knowledge tab and its tests |
| docs/M6.md, M6_IMPLEMENTATION_REPORT.md | documentation |

## Files modified
| File | Change |
|---|---|
| backend/app/db/models.py | `Document.kb_version`; M6 models, constants, immutability guards |
| backend/app/config.py | `kb_max_bytes`, `kb_max_chunks`, `kb_processing_stale_s`, `kb_require_separate_reviewer` |
| backend/app/api/main.py | includes the two knowledge routers |
| backend/app/rag/retrieval.py | `RetrievedChunk.doc_version` (optional; omitted for disk articles); both search paths select `documents.kb_version` |
| backend/app/rag/ingest.py | disk ingest skips and never withdraws store-managed documents (`IngestReport.managed_skipped`) |
| backend/tests/db/test_migrations_m4_1_m5.py | the 0006 fixture also drops the M6 tables/column it builds from current metadata (it models the 0006 schema; no assertion weakened) |
| frontend/src/components/AdminConsole.tsx | third tab "Knowledge" |
| frontend/src/lib/api.ts, types.ts | `kb*` client functions and `Kb*` types |
| .env.example | four optional `SG_KB_*` settings (commented, defaults shown) |
| Makefile | `test-kb`, `test-kb-pg` |
| docs/ROADMAP.md, docs/TESTING.md | M6 row, M7-M9 re-numbered; M6 test section |

## Migration 0009
Revision `0009`, `down_revision = 0008`. Additive: one nullable column and five new tables with named constraints and indexes. Existing rows are not rewritten; disk-ingested documents keep `kb_version = NULL` and stay disk-managed until `POST /api/admin/knowledge/import-disk`. `downgrade` drops exactly what it adds. Tested up -> down -> up on SQLite (from a schema at 0008) and PostgreSQL 16 with existing documents, chunks and calls preserved. No destructive operation is needed or used.

## Dependency / environment changes
None to dependencies. Four optional settings (all defaulted): `SG_KB_MAX_BYTES`, `SG_KB_MAX_CHUNKS`, `SG_KB_PROCESSING_STALE_S`, `SG_KB_REQUIRE_SEPARATE_REVIEWER`.

## Backward compatibility
- No existing route, schema, response field or test changed meaning. Citations gain an optional `version` key only for store-managed documents.
- Disk ingest (`make ingest`) behaves as before for every document the store does not manage.
- Retrieval, chunking, embeddings and the answer cache are untouched; a published store document is an ordinary `documents` + `chunks` row.
- Trial mode, calls, feedback review: unchanged; the feedback tables are only read.

---

# M4.1 + M5 - Safer human takeover and Trial Verification Mode (one change set)

Apply (from the project root): `unzip -o ../supportgraph-m4.1-m5-complete.zip`, then `make db-migrate` (migrations 0007 and 0008, additive). No new dependency, no model/embedding/RAG change, `.env` untouched, nothing deleted. Full write-up: **docs/M5.md** (and the updated docs/M4.md). The M4 section further down is kept as history.

## 1. M4.1 changes
- **Return to AI removed completely** (button, frontend action `returnCall`, `POST .../return`, hub method, state-machine path). A human-owned call stays human-owned until it ends, fails or the caller disconnects; an owner who stays away past the grace period ends the call as `failed` (`staff_unreachable`). `returning_to_ai` remains only as a legacy value so M4 rows stay valid.
- **Exclusive control** by the intervention owner (`calls.intervention_owner`, persisted, re-checked in the database for end, microphone and feedback). No silent transfer. An admin may take over an AI-owned call, never a human-owned one.
- **Hand-over announcement** ("I want to make sure you receive accurate information. One of our support specialists is joining us to clarify this. I'll hand over the conversation now."): only after ownership is granted, a system entry (never an AI message), never overlaps audio (the microphone waits; it opens even if the announcement fails, and the failure is audited).
- **Full chronological transcript** with timestamps (customer, AI, human staff spans, system), built from the existing conversation history and call events; tenant-isolated, staff/admin only; staff speech is not transcribed and nothing is invented.
- **Four status colours with words**: green AI handling, blue Human handling, grey Ended normally, red Ended after intervention (plus an amber Failed for a call that failed with no human involved). Listening is not taking over.
- **Post-call "Call intervention feedback"**: owner only, after the call; category, "What did the AI say or do that required your intervention?", optional correct information; no duplicates, submit/edit/skip; never written to the knowledge base.

## 2. M5 architecture
Versioned per-tenant trial settings + audit; per-call `TrialCall` (disclosure, consent, recorder); `RecordingProvider` abstraction (`none` default, `local` disk); idempotent retention job; counts-only metrics; admin Trial verification page. Diagram and file map: docs/M5.md B1.

## 3. Database migrations
- **0007 interventions**: `calls.intervention_owner` (nullable); tables `intervention_feedback`, `feedback_reviews`.
- **0008 trial_mode**: `calls.trial_mode` (default false); tables `trial_settings`, `trial_events`, `call_consents`, `call_recordings`.
Both purely additive; downgrades remove exactly what they add; tested up -> down -> up on PostgreSQL 16 and SQLite. Recording audio is never in the database.

## 4. API changes
- Removed: `POST /api/staff/calls/{id}/return`.
- Staff/admin: `GET .../calls/{id}/transcript`, `GET|POST|PUT .../calls/{id}/feedback`, `POST .../feedback/skip`, `GET /api/staff/interventions`, `GET /api/staff/trial` (read-only flag).
- Admin: `GET|PUT /api/admin/trial` (PUT needs `expected_version`), `GET .../events|metrics|feedback|retention|recordings`, `POST .../feedback/{call_id}/review`, `POST .../retention/purge` (dry-run), `POST .../recordings/{id}/hold`, `GET .../recordings/{id}/audio/{track}` (audited).
- Sockets: caller gets `trial_notice` / `consent` / `recording` and sends `consent{decision,policy_version}`; staff get `announcement` and `owner_disconnected`.

## 5. Frontend changes
Live Calls: no Return control, status badges with text, transcript panel, owner-only controls and banners, announcement/owner-disconnect messages, feedback form, "My intervention feedback" tab. Voice page: trial disclosure, explicit consent buttons (Allow recording / No thanks / Stop recording), recording indicator, storage-failure message. Admin console: Trial verification (switch, settings, metrics with date filter, feedback review, retention and recordings with legal hold). A role gate keeps it admin-only.

## 6. Call ownership and transcript changes
See 1. Ownership moves one way: `created -> active_ai -> staff_takeover -> ended|failed`. The transcript merges the LangGraph checkpoint thread with call events; limitations are returned in the response `notes` and shown on the page.

## 7. Intervention feedback workflow
Takeover creates an `awaiting` record for the owner; after the call ends the owner submits or skips (compare-and-set, one record per call), may edit until reviewed. Admin review: `pending_review -> reviewed -> approved_for_update | rejected` (also directly from pending, and `approved_for_update -> rejected`); no self-review; history in `feedback_reviews`. "Approved for knowledge update" is only a label for a later manual step: **nothing is inserted into the knowledge base or index.**

## 8. Consent and privacy controls
Trial disclosure on every trial call (text and voice). Recording is offered only if trial mode, "recording offered" and a healthy provider all hold; consent is a separate explicit button press tied to the policy version shown (changing notice, period or withdrawal rule makes a new version and refuses old answers); decline/ignore never changes the call; withdrawal stops recording at once and the admin-chosen policy deletes or keeps what was recorded. No claim of legal compliance is made.

## 9. Retention and purge
Recordings (default 30 d), consent records (365 d), reviewed/skipped feedback (365 d). Idempotent; legal holds respected; failures kept, counted and retried every run; tombstones remain; audit holds counts only; calls, tickets and knowledge documents are never deleted. Runs in-process every `SG_TRIAL_PURGE_INTERVAL_MIN` (60; 0 = off), from the admin page (with preview) and by `make purge-trial TENANT=... [PREVIEW=1]`.

## 10. Trial metrics
Date-filtered (UTC, default 30 days), trial calls only, counts and rates only (no names, ids, text or audio), explicit empty state, `n/a` for undefined rates, truncation flag. Calls, listening vs takeover, intervention rate, endings, escalations, durations, consent funnel, recordings, feedback reasons and review status.

## 11. Security measures
Admin-only routes and tenant from the token on every query; strict request models (`extra=forbid`, strict bool/int); versioned settings and compare-and-set feedback; consent accepted only from the caller's own connection and current policy version; database owner re-check for every human control; recording fails closed; files 0700/0600 with traversal checks; storage references never in responses or logs; audited, `no-store` audio download; counts-only audit details.

## 12. Tests actually executed (in this sandbox; fakes and synthetic audio only)
- Backend, SQLite, whole suite (`pytest -n 4`): **1586 passed, 41 skipped**.
- Backend with PostgreSQL 16 (`SG_TEST_DATABASE_URL=...`, whole suite, run serially): **1625 passed, 2 skipped**; includes `tests/db/test_postgres_m5.py` and the 0007/0008 up -> down -> up test.
- Frontend: `vitest run` **190 passed** in 9 files; `tsc --noEmit` clean; production `next build` succeeded (routes /, /admin, /agent, /login, /voice).
- Test-only fix found by your local run: the older `tests/voice/test_voice_ws.py` (real socket + REST from different threads) hit SQLite's "cannot commit transaction - SQL statements in progress" on the shared in-memory test database (about 1 run in 3 here). It now uses the file-backed SQLite fixture from `tests/calls/apidb.py`; 10 consecutive runs passed. Application code is unchanged by this.
- `scripts/purge_trial_data.py` smoke-run on a scratch SQLite database (dry-run, exit 0).
- Scenarios A-E (trial off; trial on without recording; consent granted and recorded; declined/withdrawn; failures, retention and purge) in `tests/calls/test_m5_scenarios.py`.

## 13. Skipped tests and reasons
- SQLite run: 39 PostgreSQL-only tests (need `SG_TEST_DATABASE_URL`) and 2 real-Ollama tests (need `SG_TEST_OLLAMA=1`).
- PostgreSQL run: the same 2 real-Ollama tests.
- With numpy hidden the whole `tests/calls` directory is skipped by design (voice tests need numpy); not a failure.
- Not run at all: real microphone/speaker, real recording audio, real Ollama, real browser sessions (nothing in the sandbox can do these).

## 14. Migration instructions
`make db-migrate` (applies 0007, 0008). Do not reset or drop the database. To roll back: `cd backend && ../.venv/bin/alembic downgrade 0006` (removes only the M4.1/M5 additions; trial settings, feedback and recording metadata are lost, so export them first if you need them). Take a backup first (`pg_dump`) if you have data you care about.

## 15. Local environment configuration
Nothing is required: defaults keep trial mode off and recording unavailable. Optional keys (in `.env.example`): `SG_CALLS_HANDOVER_ANNOUNCEMENT`, `SG_CALLS_ANNOUNCEMENT_TIMEOUT_S`, `SG_TRIAL_RECORDING_PROVIDER` (`none`|`local`), `SG_TRIAL_RECORDINGS_DIR`, `SG_TRIAL_RECORDING_MAX_MB`, `SG_TRIAL_PURGE_INTERVAL_MIN`, `SG_TRIAL_METRICS_MAX_ROWS`. `data/recordings/` is added to `.gitignore`.

## 16. Exact local verification commands
```
make db-migrate
make test-fast                 # SQLite backend suite
make test-pg                   # needs make db-up; runs the PostgreSQL tests incl. migration 0007/0008
make test-trial                # M4.1 + M5 backend and frontend tests only
make frontend-test && make frontend-build
cd frontend && npx tsc --noEmit
make purge-trial TENANT=qspiders_sample PREVIEW=1
```
Then the browser checks in docs/M5.md part F.

## 17. Known limitations
Verified only with fake STT/TTS/VAD and synthetic audio. Recording supports the local-disk provider only, with no encryption at rest, no off-machine storage and no in-app playback. One API process (live state and the retention loop are in-process). Staff speech is not transcribed. Transcript completeness depends on the checkpoint history. The announcement is not mirrored to listeners. An admin cannot take over or end a human-owned call. Migrations 0001-0003 remain PostgreSQL-only, so the SQLite migration test starts from a schema at 0006.

## 18. Features requiring real-world validation
Real microphone/speaker behaviour of the announcement and the staff microphone gate (echo, timing); actual recorded audio (quality, size, three-track sync, playback); the consent and withdrawal experience with real callers; retention timing against a real clock and disk; what recording notice and retention periods your organisation and its lawyers need (this software makes no compliance claim); real Ollama latency during a trial call; browser behaviour on your machine.

---

# M4 - Call sessions, staff Live Calls, listening and human takeover (complete, one change set)

Apply (from the project root): `unzip -o ../supportgraph-m4-complete.zip`, then `make db-migrate` (migration 0006, additive). No new dependency, no model/embedding/voice-engine change, `.env` untouched, nothing deleted. Full write-up (state machine, protocols, failure table, manual test): **docs/M4.md**.

## 1. What was implemented
- **Call sessions:** every voice call is a durable `calls` row (unique id, tenant, ticket/caller association, status, audio owner, ownership epoch, timestamps for creation, connection, latest takeover, latest return, end) with an audit trail in `call_events`.
- **Backend lifecycle:** states `created, active_ai, staff_takeover, returning_to_ai, ended, failed`; every move is validated and applied as an atomic compare-and-set; idempotent repeats; one staff member per call; ended/failed calls can never resume.
- **Staff API:** list, get, listen, take over, return, end, events; staff/admin only; tenant-isolated; no ticket ids, no student data for plain staff.
- **Real-time audio:** a staff WebSocket per call (auth in the first message, Origin check, expiry and account re-check). Listening (caller + assistant + other staff audio, short transcript lines), takeover (assistant silenced at once; staff microphone to the caller; caller audio to staff only), return to AI (same session, same conversation), end (everything stopped, state persisted, sockets closed).
- **Ownership multiplexer:** audio is accepted only from the current owner; every ownership change has an epoch; stale or replayed audio is dropped.
- **Live Calls UI** inside the existing staff console: list with short id, status, start time, running duration, owner, caller reference, who is listening; Listen / Stop listening / Take over / Return to AI / End call driven by server state.
- **Events:** `created, connected, listen_started, listen_stopped, takeover_requested, takeover_succeeded, takeover_failed, returned_to_ai, ended, failed, participant_disconnected`, each with call id, tenant id, actor, timestamp and safe metadata.
- **M3.1 fix:** the nomic embedder is now loaded with `keep_alive` (as seconds), so Ollama stops unloading it after 5 idle minutes (your `ollama ps` showed only `qwen3:1.7b`). Note: the first version of this fix (a `"30m"` string) would have crashed the embedder, because `OllamaEmbeddings` takes an integer; the new test with the real `langchain_ollama` caught it before shipping.

## 2. Architecture and design decisions (reasons in docs/M4.md section 2)
Listening is not a state (only ownership is); `returning_to_ai` is a real state so nobody owns the audio during a hand-back; the database compare-and-set is the referee and a per-call lock orders operations in the process; ownership epochs reject stale audio; a grace period returns an abandoned takeover to the assistant; live state is in the one API process (leftover live rows are marked failed at startup); the voice line keeps working if the tables are missing; the assistant's time limit does not count staff time; event details accept only safe scalar keys; the API shows a ticket *reference*, never the ticket id.

## 3. Call lifecycle and ownership state machine
`created -> active_ai <-> staff_takeover (via returning_to_ai)`; any live state -> `ended` or `failed`. Table with owners and epochs: docs/M4.md section 3.

## 4. How listening works
REST `listen` validates; the staff socket authenticates and joins the call's listener set; each listener has its own bounded outbox; leaving never touches the call. docs/M4.md section 4.

## 5. How takeover works
Database compare-and-set, then ONE synchronous switch (owner, epoch, assistant suspended: speech cut, queues dropped, in-flight reply discarded), then the staff microphone (owner + connection + epoch + size + rate checked per message). docs/M4.md section 5.

## 6. How return-to-AI works
Holder or admin only; `staff_takeover -> returning_to_ai` (nobody owns audio), staff routing stopped, `returning_to_ai -> active_ai`, assistant resumed in the same session; nothing replayed; a failed hand-back fails the call safely. docs/M4.md section 6.

## 7. Failures and disconnects
Table of 17 situations (takeover mid-speech, mid-graph, two staff at once, staff tab closed/refresh/token expiry, no microphone, caller disconnect, handler cancelled, return write fails, restart ...): docs/M4.md section 7. During this work a real bug of this class was found and fixed: a cancelled socket handler could leave a dead staff connection attached (now removal and call-end persistence run as tasks that outlive the handler).

## 8. Security and privacy
Staff/admin only; tenant from the token; 404/"not found" across tenants; WebSocket auth in the first message + Origin + role + tenant-scoped lookup + expiry/account re-check + limits; epoch and connection checks on every microphone message; no audio or transcript stored or logged (tested); `ticket_ref` not ticket id; `student_id` admin-only; no call recording, no voice-based identification. docs/M4.md section 8.

## 9. Database changes
Migration `0006_calls`: new tables `calls` and `call_events` only (CHECKs, composite keys with `tenant_id`, indexes). Downgrade drops exactly those two. No earlier migration edited, no data touched. `make db-migrate`. If you forget, the voice line still works and the log tells you.

## 10. Dependencies and environment
No new dependency. Optional `.env` keys (all defaults, in `.env.example`): `SG_CALLS_TAKEOVER_GRACE_S`, `SG_CALLS_MAX_LISTENERS`, `SG_CALLS_LISTENER_QUEUE`, `SG_CALLS_RECHECK_S`, `SG_CALLS_KEEP_ENDED_MINUTES` (and `SG_CALLS_STAFF_FRAME_MAX`). New Make target: `make test-calls`.

## 11. Tests actually executed (sandbox: fake STT/TTS/VAD, synthetic audio, fake browser audio layer)
All run in the sandbox on the final code (Python 3.13, Postgres 16, vitest, tsc):
- Backend, SQLite, full suite (`pytest -n 4`): **1494 passed, 34 skipped, 0 failed** (the skips are Postgres-only tests when no `SG_TEST_DATABASE_URL`).
- Backend, M4 tests alone (`tests/calls`, `tests/voice`): 260 passed, repeated 3 times in a row with 0 failures (after fixing one flaky test, see section 7).
- Backend, Postgres 16 (`SG_TEST_DATABASE_URL=...`, `tests/db` + `tests/calls/test_calls_api.py`): **49 passed** (includes the 4 migration/CAS tests in `tests/db/test_postgres_m4.py`).
- Backend with numpy hidden (simulating a machine without `make voice-setup`): 1234 passed, 34 skipped; the voice/call tests are skipped cleanly, nothing else breaks.
- Frontend: vitest **109 passed** (8 files, incl. 16 in `live.test.ts`, 28 in `LiveCalls.test.tsx`, 4 new in `VoiceCall.test.tsx`); `tsc --noEmit` clean.
- New test counts: state/service 47, hub 37 (incl. a seeded fuzz of "the caller never hears AI and staff at once"), API/WebSocket 17 (incl. the 11-step end-to-end sequence with a log-leak check), Postgres 4, embedder keep-alive 1 file.
Not run: `next build`.

## 12. Tests NOT executable in the sandbox
Real microphone capture and speaker playback (echo, jitter, browser permission prompts, autoplay rules); Whisper/Piper/Silero/Ollama behaviour under a takeover; the 150 ms staff jitter buffer on a real network; a real browser driving the Live Calls tab (the component is tested with a fake audio client); `make test-pg` on your Postgres 16 (the sandbox Postgres 16 was used).

## 13. Exact local commands
```bash
cd ~/supportgraph            # your project root
unzip -o ../supportgraph-m4-complete.zip
make db-migrate              # 0006_calls
make test-calls              # fast, SQLite, no models
make test-fast               # whole backend suite
make test-pg                 # Postgres (needs make db-up)
make frontend-test           # vitest
cd frontend && npx tsc --noEmit
make api                     # terminal 1
make frontend-dev            # terminal 2
```

## 14. Manual browser test
docs/M4.md section 12 (13 steps: caller window + staff window, listen, take over mid-answer, speak both ways, mute, return and follow-up, staff tab closed -> back to the assistant after ~15 s, second staff refused, end, caller hang-up, events via curl).

## 15. Known limitations
Real audio is unverified here; one API process; no adaptive jitter buffer; the human part of a call is not transcribed or recorded, so the assistant does not know what staff said after a return; a reconnecting caller gets a new call record on the same ticket; staff transcript lines cover assistant-handled speech only; no M5 dashboard yet. Details: docs/M4.md section 13.

---

# M3 - Evidence-based voice latency (complete, one change set)

Apply (from the project root): `unzip -o ../supportgraph-m3-complete.zip`. No migration, no dependency, no model/embedding change, `.env` untouched. Every behaviour change has a regression test; everything that could cost accuracy is OFF by default. Full write-up, metric reference, configuration, caching rules, troubleshooting and the verification checklist: **docs/M3.md**.

## Findings (from your two real calls; details in docs/M3.md sections 1-2)
- The graph is 91-93% of the delay; STT is 5-9%; `endpoint_to_reply - turn_ms` equals the STT time, so nothing is hidden between the caller stopping and the graph starting. Node times explain all but 22-142 ms of `turn_ms` (graph/checkpointer overhead), now reported as `unaccounted_ms`.
- QA ~0 ms is correct: citation validation runs inside the knowledge node (now visible as `stage.validate`); the QA node is deterministic checks only.
- Cold start is NOT provably one thing from node timings. Found in the code: the old warm-up preloaded Ollama with no options while real calls send `num_ctx`, which makes Ollama start a new runner (a full reload). Fixed. Other candidates (model not loaded yet, embedding load, prompt-cache effects, lazy init) are now separable with the new Ollama counters. **Your repeat question benefited from llama.cpp's prompt cache, so 6.0 s is the best case, not the typical warm case.**

## Implemented
1. **Instrumentation:** per-run stage recorder (`triage_llm`, `rewrite`, `cache_check`, `embed`, `search`, `context`, `llm`, `llm_retry`, `validate`), `other` per node, `unaccounted_ms`, Ollama load/prompt-eval/decode counters per call, `cold` verdict with reasons. Numbers only, no text.
2. **Four voice latencies:** `endpoint_to_graph_done_ms`, `endpoint_to_first_audio_ms` (any audio), `endpoint_to_answer_audio_ms` (never the holding phrase), `endpoint_to_playback_done_ms`, plus the browser's first-audible ack (`endpoint_to_first_audible_ms`, `endpoint_to_answer_audible_ms`). New `metrics` message before each answer's `audio_end`.
3. **Model lifecycle:** complete, sequential, non-blocking, failure-tolerant warm-up (Ollama models with the real runner options, triage prime, embedding + DB prime, then voice models), status in `/api/voice/status` -> `warm_detail`.
4. **Triage cache** (failures never cached; routing/auth/floor/injection run after it) and an opt-in compact prompt / output cap.
5. **Validated-answer cache** for repeated anonymous FAQ questions (validated, cited, re-validated on every hit, invalidated by document fingerprint, QA and finalize still run).
6. **Holding-phrase audio cache** (fixed phrases only).
7. Opt-in `SG_RAG_CONTEXT_BAND`, `SG_RAG_MAX_CONTEXT_CHARS`, `SG_LLM_NUM_THREAD`.
8. **`make bench-voice`:** scenarios (cold first, warm repeat/varied, unsupported, human, greeting, long utterance, context sizes), stats (mean/median/P90/P95 with sample-size rules), cold vs warm groups, CPU/memory, before/after compare.
9. Evals (`app/evals/run.py`, `scripts/eval_routing.py`) run with the caches off.

## Deliberately not implemented (reasons in docs/M3.md section 4)
Parallel embedding with triage; keyword shortcuts for triage; streaming unvalidated tokens to TTS; concurrency or `OLLAMA_NUM_PARALLEL` changes in code; model/embedding/Whisper changes; caching for signed-in students; pre-rendering answers; lowering `num_ctx` by default.

## Files
New: `app/observability/stages.py`, `app/llm/warmup.py`, `app/rag/answer_cache.py`, `app/bench/latency.py`, `scripts/bench_voice_latency.py`, `docs/M3.md`, tests `tests/graph/test_m3_stages.py`, `tests/rag/test_m3_answer_cache.py`, `tests/rag/test_m3_context_trim.py`, `tests/unit/test_m3_triage_cache.py`, `tests/unit/test_m3_warmup.py`, `tests/voice/test_m3_latency_metrics.py`, `tests/voice/test_m3_ws.py`, `tests/bench/test_latency_bench.py`.
Changed: `app/config.py`, `app/graph/support.py`, `app/graph/subgraphs/knowledge.py`, `app/agents/triage.py`, `app/rag/faq.py`, `app/llm/gateway.py`, `app/llm/preflight.py`, `app/api/main.py`, `app/api/routes/voice.py`, `app/voice/session.py`, `app/evals/run.py`, `scripts/eval_routing.py`, `frontend/src/lib/voice.ts`, `frontend/src/components/VoiceCall.tsx` (+ their tests), `Makefile`, `.env.example`, `docs/ROADMAP.md`, `docs/TESTING.md`, `docs/M2.md`.

## Results (sandbox, fake models; NOT speed measurements)
SQLite 1390 passed / 30 skipped; PostgreSQL 1418 passed / 2 skipped (real-Ollama tests); frontend 60 passed, `tsc` clean; non-voice suites pass with numpy hidden (1019 / 2). `make bench-voice` ran end to end against the sandbox database (labelled FAKE). Cannot be verified here: real Ollama timings, the cold verdict against real load times, real browser audio-clock latency, CPU/RAM.

## Run locally
```
unzip -o ../supportgraph-m3-complete.zip
make test-fast && git diff --check
make test-pg
cd frontend && npm test && npx tsc --noEmit && cd ..
make e2e-local
make bench-voice ARGS="--no-cache --n 10 --label before"
make bench-voice ARGS="--cold"
```
Then restart the API, wait for `/api/voice/status` -> warm "ready", make a call, ask the fee question twice and a different question, and send the `metrics` blocks (docs/M3.md section 10).

---
# M3.0.1 - leak fix and per-node timings (from your first real timing)

Apply: `unzip -o ../supportgraph-m2-2-3.zip` (M2.2.2/M3.0 already applied). No migration, dependency, model or default-setting change.

## Reading your real numbers
`stt_ms 577, turn_ms 6125, endpoint_to_reply_ms 6703` for "what is the fee of the full-stack course" (2.75 s of audio, one segment, no merge):
- Speech recognition 0.6 s (about 0.2x real time) and the end-of-speech wait are NOT the problem. **The graph is 91% of the delay.**
- Inside the graph there are two model calls on your CPU: triage (about 1.4 s in your e2e runs) and the grounded FAQ answer (the rest, about 4-5 s: prompt processing of ~500 tokens plus ~25 tokens of output). Retrieval and the code steps take milliseconds. I cannot split this precisely from one block, so this release makes the split visible instead of guessing.
- The holding phrase should already have played at 1.8 s while waiting (the `audio_end` message of that turn shows `endpoint_to_filler_ms`; please tell me if you did not hear it).

## Fixes
1. **Leaked tasks ("Task was destroyed but it is pending ... Event.wait()" in your API log).** My paced-playback drain wait created two helper tasks per response that were never awaited. All waits now go through one helper that cancels and awaits its tasks; regression test checks no stray tasks remain after several paced responses. Harmless to the call but noisy and a slow leak, so it is fixed now.
2. **Per-node timings.** Every graph run records milliseconds per node (`triage`, `route`, `knowledge`, `qa`, `finalize`, ...; names and numbers only). They arrive in the voice `reply.timing.nodes`, in the `graph_done` event (`grep voice_event` in the API log) and as `node_ms` in the graph result. With one real turn we will know exactly how the 6.1 s splits.

## Next (M3.1, decided after your next timing block)
Candidates, each testable with the new breakdown and none changing models or weakening grounding: (a) smaller context window / fewer retrieved chunks for voice (`SG_LLM_NUM_CTX=2048`, `SG_RAG_TOP_K=3` are existing settings you can try now: change one, ask the same question three times, compare `nodes`), (b) a shorter FAQ prompt, (c) a validated-answer cache for repeated anonymous FAQ questions, (d) answering only after validation stays; text is not streamed unvalidated.
Please send: the `reply.timing` (with `nodes`) and the `audio_end.timing` for two questions (one warm, one repeated).

## Results (sandbox)
SQLite 1304 passed / 30 skipped; PostgreSQL 1332 passed / 2 skipped; frontend unchanged (57, `tsc` clean). Not testable here: real model timings.

## Run locally
```
unzip -o ../supportgraph-m2-2-3.zip
make test-fast && git diff --check && make test-pg
```
Then `make api`, make a call, and confirm the "Task was destroyed" lines are gone.

---
# M2.2.2 + M3.0 - Close M2, first safe latency slice

Apply: `unzip -o ../supportgraph-m2-2-2.zip` (M2.2.1 already applied). No migration, dependency or model change; `.env`, databases, volumes untouched.

## Analysis of your third real call (decision: close M2, start M3)
Good news, verified by the transcript: your speech now prints for every call, one answer per turn, no overlapping voices, "connect me to the support team" escalates, "Let's end the call" ends it, the model abstains instead of guessing (CEO salary, trainers: nothing in the sample docs).
The misses are all of two kinds, and neither is turn management:
| Heard | Meant | Kind |
|---|---|---|
| "am I old of them?" | am I audible | speech recognition (base.en); nothing to match |
| "please set the call", "let's send the conversation" | cut / end | recognition garbled the verb |
| "let's end this session", "cut the call and call them directly", "you may end the call. It's fine." | goodbye | phrase gaps (fixed) |
| "I'm not able to give a reliable answer to that here" | - | the router's honest fallback for unclassified text; correct behaviour |
**Decision:** more per-sentence patching has diminishing returns, so M2.2.2 replaces it with a general deterministic rule and M2 closes.

## M2.2.2 - call-ending rule (all in the shared graph)
A sentence made ONLY of call-ending vocabulary (please/let's/you can/the/this/call/chat/session/conversation/line + end/cut/stop/close/finish/drop/kill/disconnect...) is a goodbye. Recognition-garbled verbs (`set`, `send`, `get`, `cat`...) count only when the same utterance also says goodbye/thanks/"that's it". Any other word (fee, course, how, recording, tomorrow, center, link, ...) breaks the rule, so "stop the call recording", "send me the call link", "how do I end the call", "end the call and tell me the fee", "please set the call" alone are NOT goodbyes. Also: "am I audible / whether I'm audible or not / are we clear" are answered like "can you hear me" with no knowledge-base lookup.

## M3.0 - latency, the safe part (grounding untouched)
Where the time goes (docs/M2.md): end-of-speech wait + Whisper + the text path (4-8 s with the 1.7B model) + first sentence of speech. The answer text cannot be streamed to the caller before it has been validated (citations, grounding, abstention), so M3.0 does not stream unvalidated tokens. What it does:
1. **Measured baseline.** `timing.endpoint_to_first_audio_ms` (caller stopped talking -> first sound), `endpoint_to_reply_ms`, `endpoint_to_filler_ms`, `tts_first_ms`, `turn_ms`, `stt_ms` in every `reply`/`audio_end`, plus a `latency` event. Use these before and after any later change.
2. **Holding phrase.** If the answer is not ready after `SG_VOICE_FILLER_AFTER_MS` (default 1800; 0 = off) one fixed phrase ("One moment please." / "Let me check that for you." / "Just a moment.") is spoken, in the same turn, through the same one-at-a-time playback (it can be interrupted, is never spoken for a stale turn, is never spoken for instant replies such as greetings or goodbyes, and no model or caller text ever goes into it). The browser keeps "Thinking..." during it and queues the answer after it.
3. **Warm-up.** Opening the voice page (`GET /api/voice/status`) loads Whisper, Piper and Silero in a background thread and runs each once, so the first real question no longer pays model-loading time. Status now reports `warm`.
4. `SG_VOICE_STT_BEAM` (default 1, unchanged) lets you try 2-3 for better recognition of unusual words at some CPU cost; not changed by default and not claimed to help: you can test it on your own voice.
Not done (needs your measurements first): lowering the silence window, a faster model, speculative graph runs. Recognition model choice (base.en vs small.en) is a measurement for M3.1 with your numbers.

## Results (sandbox, fakes only)
SQLite 1301 passed / 30 skipped; PostgreSQL 1329 passed / 2 skipped; voice + intents with numpy hidden 186 passed; frontend 57 passed, `tsc` clean. New tests: call-ending groups and look-alikes (`test_intents.py`), three goodbyes over the real WebSocket, holding phrase (in order, none when quick/off/stale, cut by barge-in), latency fields, warm-up no-op, player re-open of the same turn, "Thinking" kept during the phrase.
Your M2.2.1 local run was fully green (SQLite 1270/31, PostgreSQL 1298/3, frontend 55, e2e 22/22).

## Run locally
```
unzip -o ../supportgraph-m2-2-2.zip
make test-fast && git diff --check && make test-pg
cd frontend && npm test && npx tsc --noEmit && cd .. && make e2e-local
```
Real call: open the voice page, wait ~10 s (warm-up), ask the fee question and read `timing` (browser dev tools > WebSocket > `reply`, or `grep voice_event` in the API log for `latency`). Expect a spoken "One moment please." if the answer takes over ~1.8 s. Please send me one `reply`/`audio_end` timing block and I will plan M3.1 from real numbers.

## Not verified
Real microphone/speaker timing, recognition quality, and real model warm-up time (no models in the sandbox).

---
# M2.2.1 - Final M2.2 fixes from your second real call (closes M2)

Apply: `unzip -o ../supportgraph-m2-2-1.zip` over the project (M2.2 already applied). No migration, dependency or model change; `.env`/databases untouched.

## What your transcript showed, and the cause of each
| Symptom | Cause | Fix |
|---|---|---|
| After the first call, your side of the conversation was not printed (you could not tell whether it heard you) | **My M2.2 bug.** Transcript lines were keyed by turn id, and turn ids restart at 1 on every call. Call 2's "turn 1" matched call 1's "turn 1" and rewrote that old line in place, so new speech never appeared (and the chat looked scrambled: replies without "You" lines, or the wrong sentence next to a reply) | line keys now include the call number; test: a second call never overwrites the first call's transcript |
| "Okay, thank you so much. Let me end this. Please cut the call." was answered with the fee | "Let me end this" was not a recognised goodbye, so the whole utterance went to the knowledge flow (and the old context rule treated "this" as a reference to the earlier fee question) | `let me / let's / I want to / we can ... end this / it / here / the call` are goodbyes (whole utterance only); a bare "this"/"it" no longer borrows the previous course, only a pronoun together with something being asked ("Does it have a certificate?") does |
| "Please cut the gore." -> "I couldn't find that" | speech recognition heard "call" as "gore" | after a command verb + "the", `gore/coal/caul/cull` count as the call word (whole utterance only: "cut the core", "what is the cold fee" are not goodbyes); the Whisper prompt now contains "Please end the call. Cut the call. Hang up. Goodbye." to bias recognition |
| "Do you get me and not understanding ...?" -> escalated to "management" | no rule for "do you understand me"; the model classified the complaint as a person-needed case | "do you get/understand/follow me", "are you getting/understanding me", "can you understand what I'm saying" are handled like "can you hear me" (no escalation). The long garbled sentence itself is still the model's call |
| Several "I couldn't find that in our help articles" replies in call 2 | consequence of the above: unrecognised or misheard command-like speech reaches the knowledge flow, which abstains correctly (no guessing) | covered by the fixes above; abstention itself is unchanged |
Note: because of the first bug, the pairing of "You" lines and replies in your pasted chat is not reliable; the replies themselves were produced correctly.

## Results (sandbox)
SQLite 1271 passed / 30 skipped; PostgreSQL 1299 passed / 2 skipped; frontend 55 passed, `tsc` clean. New tests: `test_intents.py` (+2 groups), `test_m22_context.py` (+2), `test_voice_ws.py` (+2 goodbyes over the real socket), `VoiceCall.test.tsx` (second call). Your local results for M2.2 (SQLite 1244/31, PostgreSQL 1272/3, frontend 54, e2e 22/22 with 0 hard failures) were already green.

## Run locally
```
unzip -o ../supportgraph-m2-2-1.zip
make test-fast && git diff --check && make test-pg
cd frontend && npm test && npx tsc --noEmit && cd .. && make e2e-local
```
Then one real call: end it, start a second call, and confirm your lines appear in the transcript; say "Let me end this, please cut the call".

## Still not covered
Real microphone/speaker timing and recognition quality (see docs/M2_2.md). If recognition mishears a command in some other way, say what you saw and I will add it, but phrase lists cannot cover every mishearing: STT quality is the limit.

---
# M2.2 - Voice turn management, utterance aggregation, sequential response playback

Apply: `unzip -o ../supportgraph-m2-2.zip` over the project (M2.1.1 already applied). No migration, no schema change, no new dependency, no model change. `.env`, databases and volumes untouched (new settings are optional, defaults below; `.env.example` lists them commented out).
Full write-up, root cause, protocol, observability and manual test steps: **`docs/M2_2.md`**.

## 1. Root cause (summary)
Two independent causes (plus four contributing defects); VAD was only one.
1. **Browser player overlap.** `createPlayer.start()` reset the clock and never stopped audio still scheduled from the previous response; the server streams far faster than real time, so a whole answer is already scheduled when the next one starts: two answers sound together. Reproduced with a fake AudioContext (R1 0.05-6.05 s, R2 2.05-5.05 s).
2. **Segments became independent turns.** Every Silero utterance (after 700 ms silence) was its own STT -> graph -> TTS turn. Reproduced: a 0.8 s pause gave 2 graph runs, 2 replies, 2 `audio_start`.
3. `speaking` ended when bytes were sent, not when they were heard. 4. The browser stopped playback on every raw `speech_start` (noise, echo). 5. A single shared cancel event, cleared per `_speak`. 6. No staleness check. 7. The knowledge branch saw only the last message, so follow-ups lost the course.

## 2. What changed
| Area | Change |
|---|---|
| `backend/app/voice/session.py` (rewritten) | `Turn` aggregation of segments with adaptive grace; one runner + queue + graph lock; unique turn ids; per-response cancel token; one `_speak` at a time (lock); paced audio and drain wait; browser `playback` ack; stale-reply logic (hand-offs/approvals never stale; ack/thanks do not supersede); barge-in via `interrupted{turn_id}`; framed audio; structured events and counters; safe `close()` |
| `backend/app/voice/turns.py` (new) | completeness (complete/short/incomplete), grace, `Turn` |
| `backend/app/voice/stt.py` | `FakeStt` accepts a function of the audio (tests) |
| `backend/app/api/routes/voice.py` | handles `playback`; passes `intent`/`context`; protocol docs |
| `backend/app/config.py` | `voice_silence_ms` 700 -> 600; new `voice_grace_complete_ms` 250, `voice_grace_short_ms` 700, `voice_grace_incomplete_ms` 1600, `voice_max_turn_s` 30, `voice_max_pending_turns` 3, `voice_pace_audio` true, `voice_pace_lead_ms` 1500 |
| `backend/app/conversation/context.py` (new) | deterministic follow-up / correction resolution (<= 5 added words, from the caller's own earlier question) |
| `backend/app/graph/{support,state}.py`, `nodes/common.py` | `resolved`, `context_kind` (reset per run); triage classifies and knowledge retrieves with the resolved text; injection, floor, RBAC, QA use the original text |
| `backend/app/conversation/intents.py`, `nodes/replies.py` | `affirm` intent; comma-separated pleasantries; "I think that's it"; "proceed to / feel free to ..."; trailing "it's fine/file"; "thanks for having me"; correction topic tails and "why did you mention that" |
| `frontend/src/lib/voice.ts`, `components/VoiceCall.tsx` | turn-aware player (`begin/push/end/stop(turn)`, late and older turns dropped, never two responses), `parseAudioFrame`, stop only on `interrupted{turn_id}`, playback ack, one transcript line per turn (updated in place), stale replies marked |
| `backend/tests/conftest.py` | `SG_VOICE_PACE_AUDIO=false` for tests (pacing tests switch it on) |
| docs | `docs/M2_2.md` (new), ROADMAP, TESTING, `docs/M2.md` protocol, `.env.example` |

## 3. Bugs found by the new tests while building (fixed)
- A sealed (maximum length) turn's timer was cancelled by the next turn: the turn was lost. Turns now keep their own settle task and commit in order.
- A hand-off (`escalated`) reply was marked not stale but `_speak` still skipped it when a newer question existed. `must` outcomes are now always spoken.

## 4. Tests added / sandbox results (actual runs in the sandbox; fakes only)
- `tests/voice/test_turns.py` (43): aggregation, grace, max length, silence, two questions, one graph run at a time, stale/hand-off/light follow-up, sequential playback, simultaneous responses, new turn during TTS, stale chunks after cancel, cancel cannot leak, barge-in by voice, cough ignored, no talking over the caller, pacing + ack + cut, disconnect during playback, timer cleanup, close during graph, events without words, bounded queue.
- `tests/graph/test_m22_context.py` (27): resolver and full graph (follow-up, correction, unrelated, no leakage, injection/escalation/RBAC unchanged).
- `tests/voice/test_voice_ws.py` +7 (real WebSocket route and graph), `tests/unit/test_intents.py` (+ M2.2 phrases and look-alike guards; the three M2.1 tests that listed yes/yeah/yes please as non-intents were updated: they are now `affirm`, a bare "no" is still not an intent), `tests/voice/test_session.py` / `test_voice_ws.py` updated for the partial+final `transcript`.
- Frontend: `voice.test.ts` (fake AudioContext) and `VoiceCall.test.tsx`.
- Results: **SQLite 1245 passed / 30 skipped; PostgreSQL 1273 passed / 2 skipped; no-numpy simulation of the voice/intent/context suites 173 passed; frontend vitest 54 passed, `tsc --noEmit` clean.**

## 5. Run locally
```
unzip -o ../supportgraph-m2-2.zip
make test-fast && git diff --check
make test-pg
cd frontend && npm test && npx tsc --noEmit && cd ..
make e2e-local            # unchanged scenarios; expect 22 messages, 0 hard failures
make api   # and make frontend-dev, open /voice; follow docs/M2_2.md section 9
```

## 6. Remaining limitations (honest)
- **No real microphone, speaker, echo or browser audio timing was tested.** Real Silero/Whisper/Piper were not run here (downloads blocked). The grace periods are tuned by reasoning, not by your voice; if sentences still split, raise `SG_VOICE_GRACE_INCOMPLETE_MS`.
- Completeness is heuristic (trailing words, openers, punctuation Whisper produces). A long pause after a finished-looking sentence ("What is the fee.") will be answered, and the next part becomes a follow-up; that is the intended trade-off against waiting too long.
- A complete sentence waits at most ~250 ms (less, since STT time counts); an unfinished one up to 1.6 s. This is not the M3 latency work.
- Context resolution only handles a named course/batch from the last three usable messages; anything else goes through unchanged.
- Observations deliberately not touched (model behaviour): the ungrounded "check your account settings" reply to "How do you know whether I'm a student?", and abstention on "Are you sure ... somebody is getting some discounts".
