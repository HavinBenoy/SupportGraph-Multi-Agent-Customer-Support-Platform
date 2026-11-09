# Part 1: Knowledge Workspace (folders, read-only reader, manual change requests)

Part 1 of 3. Builds on M6 (versioning, processing, publish, audit). Part 2 (AI change propagation) and Part 3 (landing page, application-wide redesign) are not included.

## 1. Principles
* **PostgreSQL stays the single source of truth.** Folders are rows; nothing is read from or written to the filesystem. The M6 disk import is unchanged.
* **Published knowledge never changes because someone edits or submits.** Staff edits live in `kb_change_items`. The only code that writes the live `documents`/`chunks` tables is still M6 `publish_version`.
* **Admin approval is a gate, publishing is a separate act.** *Apply* turns an approved request into ordinary DRAFT versions (authored by the staff member). The administrator then runs the existing *process* and *publish*.
* **The server is the only source of diffs.** The UI sends text to a preview endpoint and renders the returned segments.

## 2. Data model (migration 0011, additive: four new tables, no existing table altered)
| Table | Key | Purpose |
|---|---|---|
| `kb_folders` | (tenant_id, folder_id) | `parent_id` (composite FK, same tenant), `name`, `name_key` (lower-case), `parent_key` (`''` = top level). Unique (tenant, parent_key, name_key); CHECK `parent_key = COALESCE(parent_id,'')`; CHECK `folder_id <> parent_id`; `row_version`. |
| `kb_doc_placement` | (tenant_id, doc_id) | Which folder a document is in. No row = top level. Composite FKs to `kb_documents` and `kb_folders`. A separate table keeps the migration additive (SQLite cannot add a composite FK with ALTER). |
| `kb_change_requests` | (tenant_id, request_id) | status `draft/submitted/approved/rejected/applied/cancelled`, `source` (`manual`, later `ai_assisted`), description, created/submitted/reviewed/applied by+at, `review_note`, `row_version`. |
| `kb_change_items` | (tenant_id, request_id, doc_id) | one row per affected document: `base_version` (FK to the immutable `kb_versions` row), `base_checksum`, `proposed_title/content/checksum`, `diff` (JSON `{"title": segs, "body": segs}`), `stats`, `origin` (`manual`/`ai`), `decision` (`accepted/manual/discarded`), `rationale`, `row_version`, `result_version`. |

Segments are `{"op": "equal"|"delete"|"insert", "text": "…"}`. `equal`+`delete` reproduces the original exactly, `equal`+`insert` the proposal; `diffing.verify` enforces it and falls back to a coarse (still exact) diff otherwise. No HTML is stored.

Folders: names are trimmed and whitespace-collapsed, 1-80 chars, no slashes/control characters, unique among siblings (case-insensitive, decided by the database so concurrent creators get exactly one winner). Depth ≤ 6, ≤ 500 folders per tenant. Paths are computed from the parent chain, so a rename is one row update.

## 3. Lifecycle
`draft` (private to its author; admins cannot see it) → `submitted` → `approved` | `rejected` → `applied`. A draft or submitted request can be `cancelled`.
* **start** is idempotent per (author, document): pressing "Make changes" twice reopens the same draft.
* **save item** recomputes the diff from the immutable base version; compare-and-set on the item's `row_version` (409 for a stale second window).
* **submit** needs a description, drops unchanged items, needs ≥ 1 real change, and refuses (409) if any document's published version moved since the edit began.
* **review** honours `SG_KB_REQUIRE_SEPARATE_REVIEWER` (existing setting) and the request `row_version`.
* **apply** re-checks staleness for every item and creates one DRAFT version per item (`created_by` = the staff author, change note = the description) in one transaction; any failure rolls everything back.

## 4. API (all tenant-scoped from the token; extra fields rejected)
Admin `/api/admin/knowledge`: `GET /tree`, `POST /folders`, `PUT /folders/{id}`, `PUT /documents/{doc}/folder`, `GET /documents?folder_id&unfiled&include_subfolders` (extended), `POST /diff-preview`, `GET /changes`, `GET /changes/{id}`, `POST /changes/{id}/review`, `POST /changes/{id}/apply`.
Staff `/api/staff/knowledge` (administrators pass too): `GET /tree` (published only), `GET /documents` (+`folder_id`, `unfiled`; search sees published text only), `GET /documents/{id}` (now with version, authors, location), `GET /documents/{id}/versions[/n]`, `GET /documents/{id}/compare?from&to` (published history only), `POST /diff-preview`, `POST|GET /changes`, `GET|PUT /changes/{id}`, `PUT /changes/{id}/items/{doc}`, `POST /changes/{id}/items/{doc}/remove`, `POST /changes/{id}/submit|cancel`. Staff see only their own requests (others answer 404).

## 5. Audit
New actions: `folder_created`, `folder_renamed`, `document_moved`, `change_request_created`, `change_draft_saved`, `change_request_submitted|cancelled|approved|rejected|applied`. Folder names are recorded; document text, descriptions and review notes never are (allow-list unchanged in spirit; test with a sentinel).

## 6. Frontend
`frontend/src/components/workspace/`: `KnowledgeWorkspace` (folder tree, search, breadcrumbs, folder page, read-only reader, history/compare), `Editor` (edit tab, server-diff review tab, save draft / submit with description / cancel, 409 handling), `Requests` (`MyRequests`, admin `ChangeRequests`), `DiffView`. Styling is scoped under `.kw` in `src/app/knowledge-workspace.css`; fonts (Inter, Instrument Serif) load from Google Fonts with system fallbacks. Admin Knowledge tab now has Workspace / Change requests / Publishing (the M6 screen, unchanged); staff gain a Knowledge tab. Deleted text is red **and** struck through with a screen-reader "Deleted:" prefix; added text is green **and** underlined with "Added:". The editor has no decorative animation; motion elsewhere is short hover transitions and is disabled under `prefers-reduced-motion`.

## 7. Apply locally
```
cd supportgraph && unzip -o ../supportgraph-part1-knowledge-workspace.zip
make db-migrate          # applies 0011 only (additive); .env, data and volumes untouched
make test-kw && make test-kw-pg
make frontend-test && make frontend-build
```
Rollback of the schema, if ever needed: `alembic downgrade 0010` drops only the four new tables (the folder layout and change requests are lost; documents and versions are not).

## 8. Known limitations
* No three-way merge. If the published version changes after a staff member started editing, submit/apply return 409 and the edit must be redone on the current text.
* Folders cannot be moved or deleted yet; documents move individually. Existing documents start at the top level until an admin moves them.
* Admins cannot edit a staff request before approving it (they can reject it, or edit via the normal Publishing flow after apply).
* A request is created from one document in the UI; the API and tables support many documents (Part 2 uses this).
* New documents are still created in the Publishing view; the workspace moves them afterwards.
* The editor is a plain text area (Markdown source), with a review diff on demand; there is no live inline red/green overlay while typing.
* Fonts need internet access to load; offline the UI falls back to system fonts.

## 9. How Part 2 builds on this
* `kb_change_requests.source = 'ai_assisted'` and items with `origin = 'ai'`, `decision`, `rationale` already exist; Part 2 inserts AI-proposed items next to the staff item in the same request (multi-document by design).
* The diff engine and `DiffView` take any two texts, so the orange/blue "remove/replace" highlights are a new `op` styling over the same segments.
* Submit/review/apply already operate on all items, so accept / edit manually / discard becomes setting `decision` per item (discarded items are skipped by apply).
* Related-document discovery can reuse the published-only retrieval index and the embedding client; nothing in Part 1 needs to be rewritten.
