# M6 implementation report - Knowledge store, versioning and audit

## 1. Features delivered
Document store (create, metadata, list/search/filter/paginate, get, archive); immutable versions (create, history, compare, restore-as-new, published pointer); lifecycle Draft -> Processing -> Ready for review -> Published, plus Failed, Superseded, Archived; atomic publish into the existing RAG tables; failure-safe, idempotent processing; unpublish/archive/unarchive; feedback -> proposal -> review -> publish chain; append-only audit; disk-article import; admin-only API, read-only staff API for published text; minimal admin Knowledge tab. Details: docs/M6.md.

## 2. Key decisions
- **One index.** The store sits in front of `documents` + `chunks`; a non-searched staging table (`kb_version_chunks`) holds processed passages so "processed before publish" is real, and publish copies them live in one transaction. No second vector DB, no embedding change.
- **Compare-and-set on `kb_documents.row_version`** for every state change: identical behaviour on SQLite and PostgreSQL, no advisory locks, concurrent publishes cannot both win.
- **Embedding happens outside any transaction**; only the short claim and the final write are transactional. A crash leaves a `processing` claim that expires (`SG_KB_PROCESSING_STALE_S`).
- **Immutability in the ORM** (guards on update/delete) plus no mutating routes; Core UPDATEs are used only for bookkeeping columns (status, processing fields, publication stamps).
- **Feedback never auto-publishes**: approved feedback only unlocks creating a draft; a proposal-linked version cannot be published until a human approves the proposal.
- **Reviewer separation is configurable** (default off) so a single-administrator development setup is not blocked.
- **JSON-only API**, PUT for metadata (the browser CORS rule allows GET/POST/PUT only); title changes are versioned content.
- **Audit allow-list** instead of a deny-list: unknown keys are dropped.

## 3. Schema (migration 0009)
`documents.kb_version`; `kb_documents` (PK tenant, doc), `kb_versions` (PK tenant, doc, version; FK to document), `kb_version_chunks` (FK to version, cascade), `kb_proposals` (PK tenant, call; FKs to feedback and version), `kb_events` (id). CHECKs: document status/category/origin and `(status='published') = (published_version IS NOT NULL)`; version status and positive number; proposal status; non-negative sizes/chunk index.

## 4. Endpoints
See the table in docs/M6.md section 9 (17 admin routes, 2 staff routes).

## 5. RAG integration
Publish writes `documents` (with `kb_version`, `content_hash` = version checksum) and `chunks` using the staged vectors; retrieval is unchanged and returns `doc_version`. Drafts, ready, failed, superseded, archived content is absent from `chunks`. Re-processing and re-publishing never duplicate rows. Disk ingest skips and never withdraws store-managed documents.

## 6. Security
Admin-only writes, staff published-read-only, students/anonymous excluded, tenant from token, 404 across tenants, strict request models, immutable versions and audit, allow-listed audit summaries, no text in errors/logs/audit. Details: docs/M6.md section 10.

## 7. Tests executed in the sandbox
| Run | Result |
|---|---|
| New M6 backend tests (`tests/knowledge`, SQLite) | 94 passed |
| M6 + M4.1/M5 migration tests (SQLite) | 6 passed |
| M6 PostgreSQL 16 tests (`tests/db/test_postgres_m6.py`) | 9 passed |
| Full backend suite, SQLite (`-n 4`) | 1680 passed, 50 skipped (before 3 late additions; the final M6 re-run is 100 passed) |
| Full backend suite, PostgreSQL 16 | 1728 passed, 2 skipped |
| Frontend `vitest run` (all) | 201 passed (190 existing + 11 new) |
| `tsc --noEmit` | clean |
| `next build` | succeeded |
| Final re-run after the last edits: M6 + migration tests on SQLite / M6 on PostgreSQL | 100 passed / 103 passed |

The full-suite runs started before three small late additions (two behavioural tests and one audit test) and an unused-import removal; those files were re-run as shown in the last row. The skip counts are the existing environment skips (real Ollama, espeak, PostgreSQL-only tests on SQLite).

## 8. Not executed here (please run locally)
`make db-migrate` against your real development database; `make test-fast`, `make test-pg`, `make frontend-test`, `make frontend-build`, `make test-kb`, `make test-kb-pg` on your machine; a real browser session on Admin -> Knowledge; real `nomic-embed-text` embeddings through process/publish (the sandbox uses the offline hashing embedder; the code path is the same function retrieval uses); a document near the size limits on your CPU.

## 9. Known limitations
Text only; processing runs inside the request; no scheduled or section-level publish; reviewer separation off by default; unpublish/unarchive require re-processing. See docs/M6.md section 14.

## 10. Exact local commands
```
cd ~/"SupportGraph Project"/supportgraph
unzip -o ../supportgraph-m6-complete.zip
make db-migrate
make test-kb
make test-kb-pg
make test-fast
make test-pg
make frontend-test
make frontend-build
cd frontend && npx tsc --noEmit
```
None of these delete data, volumes, users, tickets, calls or knowledge, and none overwrite `.env`.


---

# Addendum M6.1 - processing ownership (fencing) review

1. **Was the concern valid?** Yes, in three of four interleavings; the fourth was safe only by accident.
2. **Code path.** `pipeline.process_version`: claim = `UPDATE ... SET status='processing'`; embedding outside any transaction; completion and failure were `UPDATE ... WHERE status='processing'` with no attempt identity, and the failure path ran `_drop_staged` unconditionally and returned `failed` regardless of the UPDATE's rowcount. Safe by accident: stale A finishing *after* B completed (status already `ready`, rowcount 0 -> Conflict). Unsafe (reproduced): A finishing while B is still processing (A commits); A failing while B is processing (B's claim becomes `failed`); A failing after B completed (B's staged chunks deleted, version `ready` with none staged, A reports `failed`, misleading audit); archive during processing (version stuck `processing`, because the post-embedding Conflict rolled back its own completion).
3. **Root cause.** Ownership was inferred from the status value, which is the same for every attempt, while the long step ran outside a transaction.
4. **Solution.** Fencing token `kb_versions.processing_attempt` incremented at each claim; all attempt-ending writes are compare-and-set on (status, attempt), first in their transaction; side effects only on a matched row; losers get 409 and an `ingestion_superseded` audit event. Chosen over random tokens (unordered), leases/heartbeats (need the fence anyway, add a thread and a failure mode), and long-held row locks (minutes-long transactions blocking publish). Crash recovery is unchanged: a dead worker's claim goes stale, the next claim bumps the number. Docs/M6.md 4a.
5. **Database change.** Migration 0010: one `NOT NULL DEFAULT 0` column on `kb_versions`; additive; downgrade drops it; no backfill; live `documents`/`chunks` untouched.
6. **New concurrency tests** (`tests/knowledge/fencing.py`, gated fake embedders and an aged claim, no sleeps): A) old worker finishes after takeover completed; A2) old worker finishes while the new one is still embedding; B) old worker fails after takeover completed; B2) old worker fails while the new one is embedding; C) current worker fails, version `failed`, retry succeeds; D) six workers take over one stale claim (exactly one owner, the original stale worker also refused); E) normal processing, failure, retry, forced rebuild: no duplicate staged rows, sibling version untouched, nothing leaks into live chunks; F) stale worker after publish changes nothing; G) archive during processing does not strand the version. Each asserts final status, staged rows, model, and audit events. Before the fix 7 of these 9 failed on the old code (A2, B, B2, G on state; the rest on the missing attempt/superseded audit).
7. **SQLite results.** Fencing scenarios 9 passed (repeated 6 times, stable). Full backend suite (`-n 4`): **1693 passed, 60 skipped**. Migration tests (0009 + 0010) included.
8. **PostgreSQL 16 results.** `tests/db/test_postgres_m6.py`: **19 passed** (incl. the 9 fencing scenarios and the 0010 migration test). Full backend suite: **1751 passed, 2 skipped**.
9. **Regression.** All existing M4.1/M5/M6 tests pass in the two full runs above; no existing test was edited except the head-revision assertion in `test_migrations_m6.py` (0009 -> 0010).
10. **Not executed.** Frontend tests/build were not re-run (no frontend file changed in this fix; the earlier result was 201 passed, build OK). Nothing was run against your local PostgreSQL, Docker, or a real Ollama/`nomic-embed-text`; the tests use the offline hashing embedder with gated waits.
11. **Remaining limitations.** A worker that outlives `SG_KB_PROCESSING_STALE_S` still burns its embedding time and then discards the result (correct but wasteful; raise the threshold above your slowest realistic run). There is no heartbeat, so a genuinely long job is indistinguishable from a dead one until the threshold passes. Publish itself is protected by the document-level compare-and-set (unchanged).

Local commands: `unzip -o ../supportgraph-m6-complete.zip && make db-migrate && make test-kb && make test-kb-pg && make test-fast && make test-pg` (none deletes data or touches `.env`).
