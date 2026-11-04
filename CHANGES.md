# CHANGES: Build step 5 (web API, authentication, frontend)

Extract this ZIP over your project folder. It is the **complete tree** minus `.venv`, `node_modules`, `.next`, caches, generated files and
secrets. Your `.env` and `infra/langfuse/.env` are not included and are untouched; nothing resets or deletes database data.

## Please read first: files that were already in the tree

When I opened the project for step 5, partial step-5 backend files were already present (`backend/app/auth/*`, `app/api/{deps,ratelimit,
schemas,service,analytics}.py`, the `Account`/`ChatEvent` models, migration `0005_accounts_and_chat_events.py`, the step-5 settings in
`config.py`). They were not created in the conversation I could see. I **reviewed and built on them** (I deleted my own conflicting
duplicates rather than overwrite them). If you did not put them there, check where they came from.

## Apply and verify on your Ubuntu machine

```bash
cd ~/your-project && unzip -o supportgraph-step5.zip     # does not touch .env or .venv
.venv/bin/pip install -e "backend[dev]"                  # fastapi, uvicorn, httpx
make auth-init                                           # random SG_AUTH_SECRET into .env (not printed). The API refuses to start without it.
make test                                                # fast suite, SQLite
make db-up && make db-migrate                            # migration 0005 (additive: accounts, chat_events). No data is touched.
make test-pg                                             # real Postgres + pgvector
SG_DEMO_PASSWORD='choose-a-password-12' make demo-accounts
make api                                                 # terminal 1
make frontend-install && make frontend-test && make frontend-build
make frontend-dev                                        # terminal 2: http://localhost:3000
pip install playwright && playwright install chromium    # optional: browser walk-through of all three screens
E2E_PASSWORD='choose-a-password-12' make e2e
```

## What changed

**Backend**
- `app/main.py`, `app/api/main.py`: FastAPI app factory (requires `SG_AUTH_SECRET` >= 32 chars; CORS off unless `SG_CORS_ORIGINS`; generic 500 with a log reference).
- `app/api/routes/{auth,chat,staff,admin}.py`: login/me/config, visitor + student chat and history, escalation queue (list/detail/claim/resolve), make-up approvals (decision resumes the paused LangGraph run), analytics.
- `app/auth/tokens.py`: added student-bound signed ticket ids (`new_student_ticket`, `check_student_ticket`).
- `app/api/deps.py`: every authenticated request re-checks the account (disabled account or changed role cuts off a live token).
- `scripts/create_account.py` (operator-only, password from env or hidden prompt), `scripts/demo_accounts.py` (refuses non-sample tenants).
- `Makefile`: new targets `auth-init demo-accounts api frontend-* e2e`; the duplicate-target defect from the step-4 ZIP (`verify-tracing-support review:`) is gone. `make -n` shows no "overriding recipe" warning. Behaviour of `make review` is unchanged.
- `pyproject.toml`, `.env.example`: step-5 entries.

**Frontend** (`frontend/`, Next.js 15 App Router, TypeScript, plain CSS, no UI/chart library)
- Chat widget, staff console, analytics dashboard, login, role gates, typed API client, auth context. `package-lock.json` included.
- Tests: Vitest + React Testing Library (26 tests). `e2e/run.py`: Playwright walk-through.

**Docs**: `docs/STEP5.md` (setup, API, security model, limitations), `docs/decisions/0003-…`, `docs/ROADMAP.md`, README section.

## Results (sandbox, executed by me)

| Check | Result |
|---|---|
| Backend suite, SQLite (`pytest backend`) | **542 passed, 28 skipped** (the 28 are the Postgres tests, skipped without `SG_TEST_DATABASE_URL`). 31 new tests in `tests/api/`; steps 0-4 unchanged and green. |
| Backend suite, throwaway Postgres 16 + pgvector 0.6 | **570 passed, 0 skipped** |
| Frontend `vitest run` | **26 passed** (4 files); `tsc --noEmit` clean; `next build` succeeded (5 routes) |
| Live browser run (Chromium via Playwright) against real Postgres: migrations 0001-0005, `SG_CHECKPOINTER=postgres`, API + built frontend | **9/9 checks**: guest answer with sources; guest cannot read personal data; student make-up request pauses; staff (separate browser) approves; student's open page shows the outcome by polling; payment dispute escalates with phone number masked in the brief, then claimed and resolved; admin analytics show real counts; students/staff blocked from pages above their role; no sideways scroll at 390 px |
| ZIP hygiene | clean extraction into an empty directory: `npm ci` + `tsc` + 26 frontend tests pass; backend suite from the extracted copy: 542 passed, 28 skipped. No `.env`, `.venv`, `node_modules`, caches or DB files inside; scan for key patterns finds only the obvious fake keys used in the tracing tests. |

API tests cover: login (success, wrong password, unknown user identical, lockout 429, disabled account, expired/garbage/foreign-secret
tokens, role mismatch), forged and foreign conversation ids, student A vs student B, staff blocked from the student chat, body cannot
smuggle identity (422), chat rate limit, guest cannot read personal data, make-up pause -> staff approve/reject -> student history,
double decision conflict, escalation claim/resolve lifecycle and conflicts, another tenant's staff sees and decides nothing,
role gates on every staff/admin route, analytics scoping and bounds.

## Not verified here (needs you)

- `make test`, `make test-pg`, `make db-migrate` against **your** Docker Postgres and your existing data (migration 0005 was applied only to a fresh sandbox database).
- The UI in your browsers and screen sizes beyond Chromium at 1100 and 390 px; dark mode was styled but not visually inspected.
- Real LLM / embedding models, real Langfuse: unchanged from step 4 (fakes only). The UI exercises real plumbing, not real answer quality.
- Behaviour under several API workers (rate limits are per process), HTTPS deployment, load.

## Known limitations

Listed in full in `docs/STEP5.md`. Headlines: **authentication is basic and the platform is not production-ready** (no refresh/MFA/SSO/reset,
tokens in `sessionStorage`, per-process rate limiter); the PostgreSQL checkpointer still uses one shared connection (pool needed before
concurrent traffic); students are not push-notified; visitors who escalate cannot be contacted.
