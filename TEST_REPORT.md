# Test report: M6 acceptance refinements + M7 (2026-11-12)

Every result below was run **in Claude's sandbox** on the delivered tree. None of it ran on your machine.

**Sandbox environment**
- Python 3.13
- a throwaway PostgreSQL 16 started inside the sandbox (not your database)
- Node with Next.js 15
- the offline hashing embedder and scripted or rule-based models; no Ollama

## 1. Commands and results (sandbox)

| # | Command (from the project root unless noted) | Result |
|---|---|---|
| 1 | `cd backend && ../.venv/bin/python -m pytest -q -p no:cacheprovider -n 4 tests/knowledge tests/rag` | **747 passed, 3 skipped** (5 min 30 s) |
| 2 | `cd backend && ../.venv/bin/python -m pytest -q -p no:cacheprovider -n 4 tests --ignore=tests/knowledge --ignore=tests/rag` | **1363 passed, 80 skipped** |
| 3 | `cd backend && SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/db` | **87 passed, 0 skipped** (PostgreSQL 16) |
| 4 | `make eval-impact` | 18 scenarios · precision 1.000 · recall 1.000 · exact replacements 16/16 · wrong edits 0 |
| 5 | `cd frontend && npx tsc --noEmit -p .` | clean (0 errors) |
| 6 | `cd frontend && npx vitest run` | **16 files, 270 passed** |
| 7 | `cd frontend && npm run build` | built; 0 warnings |
| 8 | Playwright on the production build (`next start`, API mocked) | Two checks: the 2.1 repro and the M7 layout (details below) |

**Run 8, Playwright details.**
- **2.1 repro:** open a document → Change requests → Workspace (the document is restored) → Back. This lands on the document list (`?tab=knowledge`), not on
  Change requests. In-app Back from a list-opened document also returns to the list.
- **M7 admin console and the permanent-delete dialog:** checked at 1440 px and 390 px. `scrollWidth == clientWidth` in both, so there is no horizontal overflow.
  The screenshots are in `docs/screenshots/m7-*.png`.

**Why tests were skipped.**
- Run 2, 80 skips: 66 need `SG_TEST_DATABASE_URL`; those are the PostgreSQL tests, and they ran in run 3. 2 need `SG_TEST_OLLAMA=1`, a real Ollama. The rest need
  other external services (Langfuse, real audio or voice models), as in earlier rounds.
- Run 1, 3 skips: the optional real-model checks.

**Tests that did not run.** Real Ollama/Qwen, real `nomic-embed-text` vectors, Langfuse, and anything on your machine. This round does not change the LLM path.

## 2. New tests in this delivery

| File | Tests | Covers |
|---|---|---|
| `backend/tests/knowledge/test_m6_attribution.py` | 4 | 2.3. The admin sees the staff member who edited, never themselves; a different staff member is named; an admin edit becomes the latest while the staff edit stays attributed; reverting to the AI text clears the attribution; tenant isolation. |
| `backend/tests/knowledge/test_m6_purge.py` | 10 | 2.4 (details below). |
| `backend/tests/knowledge/test_m7_admin_api.py` | 5 | M7 server contract. `/meta` actions and rules; filters + pagination + 422 + tenant + RBAC; compare; audit `before_id` cursor with no overlap; the tombstone in the audit browser with no text; the review queue and import are admin-only. |
| `backend/tests/db/test_postgres_m6_final.py` (+3) | 3 new | PostgreSQL. 4 racing purges delete exactly once (one tombstone); a purge racing a new version leaves one consistent outcome; a failure at the last step rolls back completely. |
| `backend/tests/db/test_migrations_m6.py` (+1, 1 updated) | | 0013 is the head; 0013 upgrade keeps existing rows (no hold); downgrade drops only the new columns; re-upgrade. |
| `frontend/src/components/KnowledgeAdminM7.test.tsx` | 12 | M7 UI and 2.4 UI (details below). |
| `frontend/src/components/workspace/KnowledgeWorkspace.test.tsx` (+5) | | Live default + reload + every status; staff have no status filter; three Back-navigation scenarios. |
| `frontend/src/components/workspace/AiKnowledge.test.tsx` (+2) | | "Edited by staff7" / "Final wording edited by you"; no editor metadata shows "Edited", never "you". |

**`test_m6_purge.py` covers:**
- a live document is never purged, and Archive is unchanged
- a full purge removes every text and vector table, takes the document out of retrieval, keeps the audit trail and adds one content-free tombstone
- the request survives, with the purged id marked
- idempotent, and the id can't be reused
- exact id, exact title and row_version are all required
- legal hold (a reason is required), retention, and the feature switch (403)
- an open change request blocks the purge
- a never-published draft is purged together with its staged passages
- an unpublished, previously published document must be archived first
- RBAC (401/403) and tenant isolation (404)
- a disk-imported document isn't brought back by `ingest_tenant` or `import-disk`

**`KnowledgeAdminM7.test.tsx` covers:**
- server-side filters and paging, empty and error states
- any-two-version compare
- Archive and Permanently delete kept separate, with no purge for a live document
- typed id + exact title + acknowledgement + row_version
- the reasons a purge is refused, and a stale page
- a legal hold needs a reason and disables purge
- the proposal queue with its filter and links
- the audit browser with filters, the cursor, and purged ids marked
- the import wizard (list → confirm → result) and its empty state

**Existing tests that changed.**
- `test_0012_is_the_head…` became `test_0013_is_the_head…`, because the migration head moved.
- The KnowledgeAdmin feedback test now opens the *Review queue* tab first.
- The "drafts and archived" workspace test was rewritten for the Live default.

No test was removed, skipped or weakened.

## 3. Run these on your machine
```bash
unzip -o supportgraph-m6r-m7.zip          # from the project root; it overwrites code only
make db-migrate                            # upgrades your database to 0013 (additive; back up first if you like: pg_dump)
cd backend
../.venv/bin/python -m pytest -q -p no:cacheprovider tests/knowledge tests/rag
../.venv/bin/python -m pytest -q -p no:cacheprovider tests --ignore=tests/knowledge --ignore=tests/rag
SG_TEST_DATABASE_URL=postgresql+psycopg://<user>:<pw>@localhost:5432/<a scratch database> ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/db
cd ../frontend && npx tsc --noEmit -p . && npx vitest run && npm run build
cd .. && make eval-impact
```
`SG_TEST_DATABASE_URL` must point to a **scratch** database. The PostgreSQL tests create and drop their own schema, so never point them at your real data.

## 4. Manual checks (your machine)
1. **Back navigation.** As admin: Knowledge → open a document → *Change requests* → *Workspace* (the document reopens) → ← Back. You should be on the document
   list. Open a document from *My change requests* and press Back: you should be back in My change requests. Paste a document URL into a new tab and press Back:
   you should get the list.
2. **Default filter.** As admin, open Knowledge: Status shows **Live**. Choose *Archived*, reload, and the choice is kept. Choose *Live* again and the URL has no `st=`.
3. **Attribution.** As staff7, make an AI change, *Edit manually* and submit it. As admin1, the Documents tab shows "Edited by staff7". Edit the final wording as
   admin1: "Final wording edited by you". Sign in as another admin and you see "… by admin1".
4. **Permanent deletion.**
   1. Try it on a live document. *Permanently delete* isn't offered; *Request deletion* is.
   2. Request deletion, approve it and archive. Open the document in Drafts & document administration → *Permanently delete…* → check the counts → type the id and
      title → tick the acknowledgement → *Delete permanently*.
   3. The document is gone from the list, from chat answers and from search. The Audit log shows `document purged`. Creating a document with that id is refused.
   4. Place a legal hold on another archived document: *Permanently delete…* is disabled.
5. **M7.**
   - **Documents table:** filters and paging work.
   - **Compare:** pick two versions and press Compare.
   - **Review queue:** the proposal states are listed.
   - **Audit log:** apply filters, then *Load older events*.
   - **Import from disk:** only if `unmanaged_disk_documents` isn't empty.

## 5. Assumptions and known issues
- **M7 scope.** It was taken from `docs/M6.md` §13, as the roadmap points there. The roadmap's "superseded by Parts 1-3" note was only partly true (see docs/M7.md).
  Nothing beyond §13 was added.
- **Permanent deletion requires the document to be archived first** (or never published). This keeps the two-approval rule in front of removing live knowledge.
  There is deliberately no hard delete of a live document.
- **Change-request items for a purged document are deleted, not redacted.** Their foreign keys reference the document and its versions, and removing those keys
  would be a destructive schema change. The requests themselves remain.
- **No project ZIP was attached to this request.** The work started from the tree delivered as `supportgraph-m6-final.zip`. Apply this delta over that version.
