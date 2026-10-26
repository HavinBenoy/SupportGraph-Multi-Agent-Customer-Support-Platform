# SupportGraph: changes since the Step 0 ZIP

This ZIP contains **only the files that differ** from the Step 0 ZIP (`supportgraph.zip`, the version you verified
locally). Every file is complete and final, copied byte-for-byte from the workspace. Paths are relative to the
project root, so you can extract over your checkout.

**Totals:** 41 added, 14 modified, 1 deleted. 31 files from Step 0 are unchanged and are not included.

## How to apply

```bash
cd supportgraph                      # your project root
git status                           # make sure your tree is clean first
unzip -o ../supportgraph-changes.zip # overlays added + modified files
rm backend/app/db/mock_data.py       # the one deleted file (see "Deleted")
cd backend && ../.venv/bin/pip install -e ".[dev]"   # new dependencies, see pyproject.toml
cd .. && make test                   # expect: 94 passed, 7 skipped
make db-up db-setup && make test-pg  # real Postgres; see "Not verified"
```

Review with `git diff` before committing. Nothing in this ZIP is a secret; `.env` and `infra/langfuse/.env` are
never included (and are gitignored).

## Provenance, stated plainly

When I started this session's last task, the workspace already contained the Langfuse files and the Step 1 files
(dated 2026-10-25 15:53 to 16:04 UTC). I did not write those in this conversation and do not know what produced them.
I read them, ran them, and tested them, but the authorship is not mine to claim. The changes I made myself in this
session are marked **[mine]** below. Everything else is "found in the workspace, reviewed and tested".

---

## Deleted

| File | Why |
|---|---|
| `backend/app/db/mock_data.py` | The Step 0 in-memory dict store. Replaced by Postgres tables (`models.py`, `seed_data.py`). Nothing imports it any more. |

## Modified (14)

| File | What changed |
|---|---|
| `.env.example` | Adds tracing settings: `SG_TRACING_ENABLED=false`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`. Placeholders only. |
| `.gitignore` | Also ignores `infra/langfuse/.env` and `*.env.local` so generated Langfuse secrets are never committed. |
| `Makefile` | Langfuse targets (`langfuse-init/up/wait/down/logs/reset`, `verify-tracing`). **[mine]** `db-migrate`, `db-seed`, `db-setup`, `test-pg`, and a comment on `demo`. |
| `README.md` | Langfuse section (setup, start/stop, UI, verify a trace, env vars, troubleshooting, security). **[mine]** Step 1 section, new quick-start order, and a "What is verified" section. |
| `backend/app/config.py` | Adds `database_url` and the Langfuse/tracing settings. The secret key is a `SecretStr`, so it is masked in logs and reprs. |
| `backend/app/llm/fake.py` | The scripted model can now call `list_my_orders`. It also handles list and empty-list tool results. |
| `backend/app/tools/read_tools.py` | Tools read from Postgres through `app.db.repo` instead of the dict. Adds `list_my_orders`. Return window comes from the tenant row. Money is returned as exact strings. |
| `backend/app/tools/registry.py` | Registers the third tool, `list_my_orders`. |
| `backend/app/tools/scope.py` | `Scope` now lives in `app/tenants/scope.py` and is re-exported here. `require_scope` is unchanged. |
| `backend/pyproject.toml` | Adds `langfuse>=5,<6`, `sqlalchemy`, `alembic`, `psycopg[binary]`; adds a `postgres` pytest marker; `pyyaml` to dev. **[mine]** `pyyaml` also moved into core dependencies, because `app/tenants/loader.py` imports it at runtime. |
| `backend/scripts/run_agent.py` | Adds `--ticket`, wraps the run with tracing, and flushes before exit. **[mine]** Catches database connection errors and prints "make db-up db-setup" (exit code 2) instead of a traceback. |
| `backend/tests/conftest.py` | Forces tracing off and the fake model on for every test. Each test gets a fresh in-memory SQLite store seeded with both tenants. |
| `backend/tests/security/test_tenant_isolation.py` | One assertion loosened from `==` to `<=` (`list_my_orders` takes no arguments, so its schema has no `order_id`). The check still fails if any tool exposes tenant or customer fields. |
| `backend/tests/unit/test_read_tools.py` | Updated to seeded data. Adds tests for `list_my_orders`, the day-30/day-31 return boundary, returned and cancelled orders, per-tenant return windows, and unknown tenants failing closed. |

(`backend/tests/graph/test_react_agent.py` is unchanged and still passes.)

## Added (41)

### Database and tenants (Step 1)
| File | Purpose |
|---|---|
| `backend/alembic.ini` | Alembic config. The database URL is read from settings, not stored here. |
| `backend/app/db/base.py` | SQLAlchemy base with deterministic constraint names. |
| `backend/app/db/models.py` | Tables: tenants, customers, orders, order_items. Every key and foreign key includes `tenant_id`, so the database itself rejects cross-tenant references. |
| `backend/app/db/migrations/env.py`, `script.py.mako`, `versions/.gitkeep` | Alembic environment and template. |
| `backend/app/db/migrations/versions/0001_create_mock_store_tables.py` | The first migration (creates the four tables). No pgvector yet; that arrives with RAG in Step 2. |
| `backend/app/db/repo.py` | Store queries. Every function requires a `Scope`, so a query without a tenant cannot be written. |
| `backend/app/db/seed.py`, `seed_data.py` | Idempotent seeding. Dates are offsets from an `as_of` date. Two tenants on purpose: `acme_store` plus a test-only `other_store` that reuses order id `A1001` and customer `cust_alice`. |
| `backend/app/db/session.py` | Engine and session handling. |
| `backend/app/tenants/loader.py`, `schema.py`, `scope.py` | Loads and validates `tenants/<id>/config.yaml`. The tenant id is validated so `../x` cannot escape the folder. |
| `backend/scripts/seed_store.py` | CLI to load the mock store. |
| `tenants/acme_store/config.yaml` | Tenant settings (currency, return window). |

### Policy documents and synthetic tickets (Step 1)
| File | Purpose |
|---|---|
| `tenants/acme_store/docs/` (6 files: account-and-privacy, order-changes-and-cancellations, payments-and-billing, returns-and-refunds, shipping-and-delivery, warranty-and-repairs) | Customer-facing policy for the knowledge agent to cite. |
| `tenants/acme_store/internal/refund-approval-policy.md` | Internal-only thresholds, kept in a separate folder and never to be ingested for customers. |
| `evals/datasets/tickets_acme_v1.jsonl` | 44 labelled tickets (route, tools, abstain, leakage and injection cases). Labels are my best judgment, not ground truth. |
| `evals/datasets/README.md` | Field definitions for the dataset. |

### Tracing (Langfuse)
| File | Purpose |
|---|---|
| `backend/app/observability/tracing.py` | Opt-in, fail-safe tracing. Needs the flag and both keys; any failure degrades to an untraced run plus one warning. Keys are never logged. |
| `infra/langfuse/docker-compose.yml` | Isolated self-hosted Langfuse (v4 images): own Postgres, ClickHouse, Redis, MinIO and volumes. UI on `127.0.0.1:3100`, no host port for its Postgres, all secrets required. |
| `backend/scripts/langfuse_init.py` | Generates secrets and API keys into gitignored files (mode 600). Refuses to overwrite existing secrets. |
| `backend/scripts/verify_tracing.py` | End-to-end check against a real Langfuse: runs the agent, then asks the REST API for the trace. |

### Tests (all added)
| File | Covers |
|---|---|
| `backend/tests/data/__init__.py`, `tests/db/__init__.py` | Package markers. |
| `backend/tests/data/test_policy_docs.py` | Doc front matter; return window in docs matches tenant config; internal docs kept out of `docs/`. |
| `backend/tests/data/test_ticket_dataset.py` | Ticket schema, label consistency, references match the seed, and expected facts appear in the docs. |
| `backend/tests/db/test_postgres.py` | Real-Postgres: migrations up/down, constraints, tools and graph on the real schema, pgvector availability. Skipped unless `SG_TEST_DATABASE_URL` is set. |
| `backend/tests/graph/test_tracing_integration.py` | Real Langfuse SDK and graph against a local stand-in server: spans emitted, run unaffected when the server is unreachable. |
| `backend/tests/unit/test_db_store.py` | Seed idempotency, relative dates, tenant-aware foreign keys, check constraints. |
| `backend/tests/unit/test_langfuse_compose.py` | Static checks: localhost-only ports, no clash with the app DB or port 3000, separate volumes, no default secrets. |
| `backend/tests/unit/test_langfuse_init.py` | Secrets generator: strong, unique, never printed, never silently rotated. |
| `backend/tests/unit/test_run_agent_cli.py` | **[mine]** The demo gives an actionable message when the database is unreachable. |
| `backend/tests/unit/test_tenant_config.py` | Tenant config loading, path-traversal rejection, unknown fields rejected. |
| `backend/tests/unit/test_tracing.py` | Tracing on/off logic, secret masking, failure degradation. |

## Verification (what was actually run)

| Check | Result |
|---|---|
| Fast suite (SQLite, no Docker, no LLM key) | 94 passed, 7 skipped |
| Full suite incl. Postgres tests, against a throwaway Postgres 16 | 100 passed, 1 failed (the pgvector availability check; that Postgres had no pgvector) |
| Alembic upgrade, seed, and `scripts/run_agent.py` on that Postgres | Worked; the agent answered from the database |
| Demo with tracing on and Langfuse unreachable | Completed with the correct answer; retry errors printed |
| Demo with the database down | Clear message, exit code 2, no traceback |
| Langfuse compose rendered with generated secrets | Renders; secret files mode 600 |
| Secret scan of project files | No keys or passwords found |

## Not verified

- **The real Langfuse stack was never started** (no Docker in the workspace). A trace has not been seen in the UI. Run `make langfuse-init langfuse-up langfuse-wait verify-tracing` on your machine.
- **The Langfuse image tags** (`docker.langfuse.com/langfuse/langfuse:4`, `langfuse-worker:4`) could not be confirmed against upstream; the docs I could reach confirm v4 but not the registry or tags. If `make langfuse-up` fails to pull, check upstream's compose file.
- **pgvector** was not available in the workspace's Postgres. `make test-pg` on your `pgvector/pgvector` container is the check.
