# Part 1 implementation report: Knowledge Workspace

Deliverable: `supportgraph-part1-knowledge-workspace.zip` (project-relative paths; contains every file added or changed since `supportgraph-m6-complete.zip`, which already included M6.1). Design details: `docs/PART1.md`. Change list: `CHANGES.md`.

## 1. What was built
* A database-backed folder workspace (PostgreSQL is the source of truth; no filesystem source of truth). Admins create and rename folders and move documents; staff browse and search.
* A read-only reader: title, breadcrumbs, category, published version, last-updated/published-by, content, version history, version compare. The canonical text is never an input element. A clear **Make changes** button.
* A manual change workflow: editing copy, "Review changes" diff (deleted = red + struck through with the original visible, added = green + underlined, unchanged = plain), Save draft, Submit change request (description required), Cancel changes. The diff is stored as structured segments, not HTML.
* A multi-document change-request model with status, description, creator, timestamps, original versions, proposed text + structured diff, and audit events; admin review (approve/reject) and apply (creates ordinary draft versions; the existing process + publish makes them live).
* Optimistic concurrency on folders, documents (moves), change-request rows and individual edit items.
* A dark workspace UI scoped under `.kw` (violet/cyan accents, Inter + Instrument Serif, glass panels, responsive, reduced-motion aware).

## 2. Architecture decisions
1. **Placement table instead of a `folder_id` column on `kb_documents`**: keeps migration 0011 purely additive (SQLite cannot add a composite FK via ALTER) and leaves M6 tables untouched.
2. **Computed paths** from the parent chain: rename is one row update with a compare-and-set; no descendant rewrites, no races.
3. **Sibling uniqueness enforced by the database** (tenant, parent_key, lower-cased name) so racing creators get exactly one winner.
4. **Edits live in `kb_change_items`, never in versions**, so saving or submitting cannot alter published knowledge. Apply creates DRAFT versions through the existing `store.create_version`; only M6 `publish_version` writes the live RAG tables.
5. **Server-computed diffs** (line-level, then word-level inside changed blocks, bounded cost; verified to reproduce both texts exactly).
6. **Drafts are private**: a request is invisible (404) to everyone but its author until submitted; admins never see staff drafts.
7. **Stale-base protection instead of silent overwrite**: submit and apply return 409 if the published version moved after the edit began.
8. **Part 2 hooks already in the schema**: `source`, per-item `origin`, `decision`, `rationale`.

## 3. Database changes (migration 0011, additive)
Four new tables: `kb_folders`, `kb_doc_placement`, `kb_change_requests`, `kb_change_items`, with composite tenant FKs, named CHECK/UNIQUE constraints and indexes. No existing table is altered; downgrade drops only these four tables. Existing documents start at the top level.

## 4. API changes
New router `knowledge_workspace.py` (admin and staff; listed in `docs/PART1.md` §4). Extended M6 routes: admin document list gains `folder_id`, `unfiled`, `include_subfolders`; staff list gains `folder_id`, `unfiled`; the staff document view adds version/author/checksum/size/row_version/folder/breadcrumbs. **Bug fixed in passing:** the staff document search could match text of unpublished draft versions; it now matches the published version only.

## 5. Frontend changes
New `components/workspace/*` (+ tests), Admin Knowledge area split into Workspace / Change requests / Publishing (existing `KnowledgeAdmin` unchanged), new staff Knowledge tab, typed API client functions, scoped stylesheet, font links in the layout.

## 6. Tests actually executed (in the sandbox; none on your machine)
| Run | Result |
|---|---|
| Full backend, SQLite: `pytest -q -n 4` | **1782 passed, 66 skipped** (the skips are the PostgreSQL tests, which need `SG_TEST_DATABASE_URL`) |
| PostgreSQL 16 (local cluster in the sandbox): `SG_TEST_DATABASE_URL=… pytest -m postgres tests/db` | **64 passed** (7 non-PostgreSQL tests deselected), including 6 new Part 1 tests: 0011 up/down/up with data kept, constraints, racing folder creators / moves / saves / submit+apply |
| New backend tests (SQLite) | diffing 13, folders/placement/RBAC/isolation/import 42, change requests 34 (all included in the 1782) |
| M6 regression (`tests/knowledge`, `test_migrations_m6`) before the new tests were added | 107 passed; the rest of M6 and M4.1/M5 suites are part of the full run above |
| Frontend: `npx vitest run` | **216 passed in 11 files** (15 new) |
| `npx tsc --noEmit` | clean |
| `npm run build` (production) | succeeded (run once, after all non-test frontend code was final; later edits touched only a test file's type annotation) |
Not run: Docker, your local PostgreSQL container, a real browser, a real LLM/embedding model (tests use the fake embedder), the Playwright e2e script. The visual design has **not been looked at in a browser** by me; it is verified only through DOM-level tests, so expect to adjust spacing/colours after viewing it.

## 7. Commands to apply locally
```
cd supportgraph && unzip -o ../supportgraph-part1-knowledge-workspace.zip
make db-migrate
make test-kw && make test-kw-pg
make frontend-test && make frontend-build
```
Then sign in as admin → Knowledge → Workspace; as staff → Knowledge.

## 8. Known limitations
No three-way merge (stale base → 409, redo the edit); folders cannot be moved or deleted; admins cannot edit a staff request before approving; the UI starts requests from one document (API supports many); new documents are still created in the Publishing view and then moved; the editor is a plain Markdown text area with an on-demand review diff; fonts need internet access; visual polish unverified in a browser.

## 9. How Part 2 builds on this
See `docs/PART1.md` §9: AI-proposed items are added to the same request (`origin='ai'`, `source='ai_assisted'`), staff choice is stored in `decision`/`rationale` (accept / manual / discard), the diff engine and `DiffView` are reused with orange/blue styling, and submit/review/apply already work across all items.
