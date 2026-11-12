# M6 acceptance refinements (2.1 – 2.4)

These are targeted fixes on top of the accepted M6 foundation. Discovery, decomposition, sequential tasks, the review and approval gates, versioning and publication,
RBAC and analytics are unchanged.

## 2.1 Back from a knowledge document

**How documents are opened.** The Knowledge Base is a client-side workspace inside `/admin` and `/agent`. Its view is kept in the URL query (`?kb=doc&d=<id>`,
`lib/nav.ts`), so the browser's Back and Forward move between views. A document can be opened from:
- the document list
- search hits
- *My change requests* (resume)
- the Editor's back path
- the admin "Manage versions" view
- a restored URL: a reload, a bookmark, or a sub-tab switch that keeps `kb=doc` in the query

**The defect.** The in-app back arrow called `window.history.back()` unconditionally, so it went to whatever history entry came before the document. A typical case
for an administrator:
1. Open a document.
2. Switch to the *Change requests* sub-tab. `view=requests` is pushed, and `kb=doc` stays in the URL.
3. Switch back to *Workspace*. The open document is restored straight away.
4. Press Back. The entry before is Change requests, so that is where you land.

The same thing happens with any document URL opened directly while earlier history exists. Staff are affected the same way, through the Staff console tabs.

**The fix.** It doesn't hardcode a route.
- Every view change made by the workspace marks its history entry with the view it was opened *from*: `history.state.sgOrigin = "kb:<view>"`
  (`setUrlParams(..., origin)`).
- The back arrow (`goBack("kb:", fallback)`) uses `history.back()` only for an entry that carries such a mark. That entry is guaranteed to sit on top of the view it
  came from, so a document opened from the list returns to the list, and one opened from *My change requests* returns there.
- An entry without the mark falls back to the document list. That covers direct URLs, restored URLs, and entries pushed by other parts of the app. The fallback
  *replaces* the current entry, so the earlier history is not disturbed.
- Browser Back and Forward behave as before.

## 2.2 Live by default
For administrators, the Knowledge Base status filter now defaults to **Live**. The URL keeps only a non-default choice (`st=all|not_live|archived`), so a reload or
Back restores the choice and a clean URL means Live. "Every status" is an explicit option. Staff have no status filter; they only ever see live documents. Lifecycle
and status definitions are unchanged.

## 2.3 Who edited a proposal
**The defect.** The decision chip's label for `decision = manual` was the fixed string "Edited by you", whoever was looking.

**The data.**
- The item rows persisted `decided_by` (the last staff decision) and `admin_by` (the last administrator action, which a later *confirm* or *exclude* overwrites).
- The append-only audit trail records every edit with its actor: `change_decision_recorded` with `decision=manual`, `change_draft_saved`, and
  `change_item_edited`, the administrator's final wording.

**The fix.** No migration was needed. The item view now derives the attribution from that persisted audit history (`changes.edit_history`):
- `staff_edited_by`: the latest edit by the request's author side
- `admin_edited_by`: the latest administrator edit
- `last_edited_by`, `last_edited_at`, `last_edit_role`: the most recent edit of either kind
- `editors`: everyone who edited

If the text was reverted to the AI proposal, the attribution is cleared. The UI shows "Edited by &lt;name&gt;" and "Final wording edited by &lt;name&gt;", and
says "you" only when that persisted editor is the viewer. It shows a plain "Edited" when no editor is recorded. The proposer ("Requested by …") and the latest editor
are shown separately. The data is visible only to someone who can already open the request (the author, or an administrator of the same tenant). It contains account
names and times, never text.

## 2.4 Permanent deletion
**What already existed, unchanged.**
- **Unpublish:** answers stop at once; the document returns to draft.
- **Archive:** the document leaves retrieval; every version and the audit trail are kept; it can be unarchived.
- **Deletion request → administrator approval → publish:** the approval-based way to archive a *published* document.
- The one-click archive of a published document stays refused (409) unless `SG_KB_DIRECT_ARCHIVE=true`.

**New: Permanently delete.** This is a separate administrator operation (`POST /api/admin/knowledge/documents/{id}/purge`, `app/knowledge/purge.py`). It is never
reachable through Archive.

**When it is allowed.** Only for a document that was never published, or one that is **archived**. A live document must first go through the approval-based deletion
request. Permanent deletion is refused:
- while a **legal hold** is set (new; migration 0013)
- before the configured **retention** period after archiving has passed (`SG_KB_PURGE_MIN_ARCHIVED_DAYS`)
- while any **open change request** references the document
- while a **feedback proposal** for it awaits review
- while a **linked call recording** is under legal hold (the M5 holds)
- while a version is being **processed**
- when the feature is switched off (`SG_KB_PURGE_ENABLED=false`)

**Confirmation.** The administrator sees what will be removed (`GET …/purge-preview`: counts only, plus the blockers), then types the exact document id **and** title
and acknowledges that it cannot be undone. The request carries the page's `row_version`. A stale page, a different document or a concurrent change is refused: it is a
compare-and-set on `kb_documents.row_version`, and on PostgreSQL that UPDATE also locks the row.

**What is removed, in one transaction:**
- live passages and their embeddings (`chunks`) and the live `documents` row
- staged passages and embeddings (`kb_version_chunks`)
- every version (`kb_versions`)
- the document's items in closed change requests (`kb_change_items`, which hold its text, the AI suggestion and diffs; the FKs require this). The requests themselves
  are kept and marked `analysis.purged_docs`. References in analyses ("not relevant" lists, processing-log events) are scrubbed, and a deletion request's description,
  which quoted the title, is replaced.
- finished feedback proposals for it (`kb_proposals`; the feedback records themselves stay)
- the legacy folder placement
- the document row

Bulk SQL deletes are used on purpose: the ORM guard that forbids deleting versions still protects every other code path.

**What is kept.** The audit trail (`kb_events`), which by design holds only ids, counts and field names, plus one `document_purged` event (id, actor, time, counts).
That event is the tombstone:
- `make ingest` skips the id (`IngestReport.purged_skipped`)
- the import wizard does not list it or import it
- creating a document with that id is refused

So a file still lying in the tenant's docs folder cannot bring the deleted knowledge back. A repeated request answers `already_deleted`.

**Limits (stated rather than worked around).**
- Chat transcripts (`chat_events`) that once quoted the document are customer conversation records and are not modified.
- The source file of a disk-imported document stays on disk: the server never deletes repository files. Its id is blocked instead.
- An in-memory validated-answer cache entry built from the document becomes unreachable immediately, because the key includes the document fingerprint, and expires
  with `SG_ANSWER_CACHE_TTL_SECONDS`.
- Langfuse traces, if you enabled them, are outside the application database.

**Legal hold.** `PUT …/legal-hold {hold, reason, expected_row_version}` places or releases a hold; placing one needs a reason. Both actions are audited
(`document_legal_hold_set` / `document_legal_hold_released`). A held document works normally in every other respect.
