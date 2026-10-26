# SupportGraph

Multi-tenant customer support system run by LangGraph agents. See `PROJECT_BRIEF.md` for the full plan.

**Status:** build step 0 (skeleton, Postgres+pgvector, ReAct agent, Langfuse tracing) and the code for build step 1
(mock store in Postgres, policy docs, synthetic tickets) are in place. Step 1 is verified on Postgres 16 without
pgvector; see "What is verified" below.

## Quick start

```bash
cp .env.example .env        # defaults to the scripted fake model: no LLM, no network
make venv                   # create .venv and install backend + dev deps
make test                   # fast suite on in-memory SQLite: no LLM, no network, no Docker
make db-up                  # start Postgres + pgvector (needs Docker running)
make db-setup               # wait for Postgres, run Alembic migrations, seed the mock store
make demo                   # run the agent on "Where is my order A1001?" (reads the order from Postgres)
make test-pg                # also run the real-Postgres tests (throwaway databases; your data is untouched)
```

Verify pgvector after `make db-up`:

```bash
make db-psql
supportgraph=# SELECT extname FROM pg_extension WHERE extname = 'vector';
```

## Build step 1: mock store, policy docs, synthetic tickets

| What | Where |
|---|---|
| Schema (tenants, customers, orders, order_items) and Alembic migration | `backend/app/db/models.py`, `backend/app/db/migrations/` |
| Seed data: two tenants on purpose, so isolation is tested from day one | `backend/app/db/seed_data.py`, `make db-seed` |
| Store queries; every function requires a trusted tenant + customer scope | `backend/app/db/repo.py` |
| Read tools: `get_order_status`, `list_my_orders`, `check_return_eligibility` | `backend/app/tools/read_tools.py` |
| Customer-facing policy docs (for RAG in step 2) | `tenants/acme_store/docs/` |
| Internal-only policy, kept in a separate folder and never to be ingested for customers | `tenants/acme_store/internal/` |
| 44 labelled synthetic tickets, with schema and label-consistency tests | `evals/datasets/tickets_acme_v1.jsonl` |

Why Postgres for the mock store instead of the earlier Python dict: tenant and customer scoping now happens in the
SQL `WHERE` clause, the same place it will happen in production, so the isolation tests exercise the real mechanism.
Example: `get_customer_order` filters on `tenant_id`, `customer_id` and `id` together, so Alice asking for Bob's `A1003`
gets the same "not found" as for an order that does not exist.

## What is verified

Last full run, in a sandbox without Docker, against a throwaway Postgres 16 (no pgvector):

- Fast suite (SQLite): passes with no LLM key, no network and no Docker.
- Real-Postgres tests, migration, seed and `make demo`: pass. One test, the pgvector availability check, can only pass on
  the `pgvector/pgvector` image from `docker-compose.yml`.
- Langfuse: the SDK integration is tested against a local stand-in server. The real Langfuse stack (`make langfuse-up`,
  `make verify-tracing`) has not been run in that sandbox; run it on your machine as described below.

## Using a real model

Edit `.env`:

- Ollama: `SG_LLM_PROVIDER=ollama`, `SG_LLM_MODEL=qwen2.5:7b`, then `ollama pull qwen2.5:7b` and `pip install -e "backend[ollama]"`.
- Any OpenAI-compatible API: `SG_LLM_PROVIDER=openai_compat`, set `SG_LLM_BASE_URL`, `SG_LLM_API_KEY`, `SG_LLM_MODEL`, and `pip install -e "backend[openai]"`.

## Layout rules

- Tenant-specific logic lives only in `tenants/` and the database.
- `backend/app/llm/gateway.py` is the only place that knows the LLM provider.
- `backend/app/guardrails/` is deterministic code and never imports `graph/`.

## Tracing with Langfuse (self-hosted)

**Why:** an agent is a loop of LLM calls and tool calls. When it answers wrongly you need to see *which step*
went wrong. Example: a customer says "where is order A1001?" and gets a bad answer. The trace shows
`agent` (decided to call `get_order_status`) -> `tools` (what the tool actually returned) -> `agent` (final
answer), with inputs, outputs and timing at each step, so you can tell "bad tool data" from "bad reasoning".
Later it also gives cost per ticket and the failing cases that feed evals (build step 6).

**Why self-hosted Langfuse:** runs entirely on your machine (free, customer text never leaves it), open source,
and has first-class LangChain/LangGraph support (the SDK's `CallbackHandler`).

The Langfuse stack (`infra/langfuse/`) is a **separate Compose project** with its own Postgres, ClickHouse, Redis,
MinIO and volumes. It never touches the SupportGraph `supportgraph-db`. It needs roughly 4 GB of free RAM.

### Setup (once)

```bash
make venv                # installs the langfuse SDK too
make langfuse-init       # generates secrets + API keys; writes infra/langfuse/.env and your .env (both gitignored)
make langfuse-up         # starts the stack (first boot: pulling images + migrations, 1-2 min)
make langfuse-wait       # blocks until http://localhost:3100 is healthy
make demo                # runs the agent; this run is traced
```

### Start / stop

| Command | What it does |
|---|---|
| `make langfuse-up` / `make langfuse-down` | start / stop (data kept in Docker volumes) |
| `make langfuse-logs` | follow web + worker logs |
| `make langfuse-reset` | **delete all Langfuse data and secrets** (then `make langfuse-init` again) |

### UI access

Open <http://localhost:3100>. Email `admin@supportgraph.local`; the password is `LANGFUSE_INIT_USER_PASSWORD` in
`infra/langfuse/.env` (never printed). The project `supportgraph-dev` and its API keys are created automatically on first boot.

### Verify a trace

1. Run `make demo`. It prints a session id such as `acme_store:T-1a2b3c4d`.
2. In the UI: **Tracing -> Traces**, filter by that session (or tag `tenant:acme_store`). Open the trace. You should see
   `supportgraph.ticket` containing `agent`, `tools` -> `get_order_status`, then `agent` again, with the tool's JSON output.
3. Or automatically: `make verify-tracing` runs the agent, then asks Langfuse for the run's observations and checks for
   the `agent`, `tools` and `get_order_status` spans (waits up to 90 s; ingestion is asynchronous). Exit code 0 = verified.
   It uses the SDK's `client.api.observations.get_many(session_id=...)`, the observations v2 API, because Langfuse v4 no
   longer serves the legacy `/api/public/traces` endpoints (they return HTTP 404). It finds the run's trace by session id,
   then reads every span of that trace, so child spans count even if they do not carry the session id themselves.

Traces carry: session = `<tenant>:<ticket>`, user = `customer_id`, tag = `tenant:<tenant>`.

### Environment variables

| Variable | Where | Meaning |
|---|---|---|
| `SG_TRACING_ENABLED` | `.env` | `true` to trace. Default `false`. `make langfuse-init` sets it to `true`. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `.env` | Project API keys (standard Langfuse names). Both required, otherwise tracing stays off. |
| `LANGFUSE_BASE_URL` | `.env` | Default `http://localhost:3100`. |
| `LANGFUSE_WEB_PORT` / `LANGFUSE_MINIO_PORT` | `infra/langfuse/.env` | Host ports (defaults 3100 / 9090). If you change the web port, also change `LANGFUSE_BASE_URL`. |
| `SALT`, `ENCRYPTION_KEY`, `NEXTAUTH_SECRET`, DB/Redis/MinIO passwords | `infra/langfuse/.env` | Generated by `make langfuse-init`. Compose refuses to start without them. |

### What happens when tracing is unavailable

Tracing is optional and must never break a ticket. If it is off, misconfigured (missing key), or the server is down,
the agent runs normally. Missing keys and init failures log one warning. With the server down the SDK logs export
errors and `make demo` takes a few extra seconds at exit while it retries. Tests never trace, even if your `.env` enables it.

### Troubleshooting

- **`Missing infra/langfuse/.env`** -> run `make langfuse-init` first.
- **`required variable ... is missing`** from compose -> same cause; run it through `make`, not bare `docker compose`.
- **`make langfuse-init` says the file already exists** -> intentional: changing secrets under existing volumes locks Langfuse out of its data. Use `make langfuse-reset` first if you really want to start over.
- **Port already in use** -> set `LANGFUSE_WEB_PORT` in `infra/langfuse/.env` (and `LANGFUSE_BASE_URL` in `.env`), then `make langfuse-up`. Or on a fresh install, `python backend/scripts/langfuse_init.py --web-port 3200`.
- **Cannot log in / keys rejected after editing `infra/langfuse/.env`** -> the admin user, project and keys are only created on the *first* boot. Run `make langfuse-reset && make langfuse-init && make langfuse-up`.
- **No trace in the UI** -> wait ~30 s (async ingestion); check `LANGFUSE_BASE_URL` matches the UI port; confirm `SG_TRACING_ENABLED=true` and both keys are in `.env`; look at `make langfuse-logs` (worker errors); run `make verify-tracing` for a precise failure.
- **`make verify-tracing` says "rejected the API keys (HTTP 401/403)"** -> the keys in `.env` do not match the project in the UI. If you ran `make langfuse-reset`, re-run `make langfuse-init` so both files get the new keys.
- **`make verify-tracing` says "returned 404 for the observations endpoint"** -> `LANGFUSE_BASE_URL` does not point at a Langfuse v4 server (default `http://localhost:3100`).
- **`make verify-tracing` times out and lists "root observations" with a different session** -> spans arrive but the session id is not propagated onto them; the printed list shows what Langfuse actually received.
- **Log line "Langfuse was not able to parse the LLM model"** -> harmless with the scripted fake model (it has no model name). Real providers fill this in.
- **Containers restart / out of memory** -> ClickHouse and the web/worker need a few GB; close other heavy apps or raise Docker's memory limit.
- **Image pull fails** -> images come from `docker.langfuse.com`, `docker.io` and `cgr.dev`; check your network/proxy.

### Security notes

- Traces contain full customer messages and tool outputs. That is why the stack is self-hosted and bound to `127.0.0.1`.
  A PII-masking hook (the SDK's `mask`) belongs with the guardrails work in build step 4.
- Never commit `.env` or `infra/langfuse/.env` (both gitignored, mode 600). Rotate by resetting, not by editing.
