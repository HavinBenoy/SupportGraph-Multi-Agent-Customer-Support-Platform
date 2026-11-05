# Build step 5: web API and frontend

Three working screens on top of the existing LangGraph workflows:

| Screen | URL | Who | What it does |
|---|---|---|---|
| Support chat | `/` | visitors, students | Asks the support graph. Visitors get public answers; signed-in students also get their own batches, attendance and scores, and can request make-up classes. Shows sources, a "waiting for staff" state (input locked, page polls) and the escalation reference. |
| Staff console | `/agent` | staff, admin | Escalation queue (open / claimed / resolved) with the masked brief, claim and resolve; make-up approval queue with Approve / Reject, which resumes the paused run. |
| Analytics | `/admin` | admin | Turns, self-service rate, abstain rate, latency, routes, escalations by team/reason/priority, SLA breaches, make-up approvals, tickets by status. |

## Run it (Ubuntu, from the project root)

```bash
.venv/bin/pip install -e "backend[dev]"       # adds fastapi, uvicorn, httpx
make auth-init                                 # writes a random SG_AUTH_SECRET into .env (never printed). Required.
make db-up && make db-migrate                  # migration 0005 (accounts, chat_events): additive, no data is touched
make db-seed && make ingest                    # only if you have not already
SG_DEMO_PASSWORD='choose-a-password-12' make demo-accounts   # SAMPLE tenant only: asha, meera (students), staff1, admin1
make api                                       # terminal 1: http://localhost:8000  (docs: /api/docs)
make frontend-install && make frontend-dev     # terminal 2: http://localhost:3000   (needs Node 20+)
```

Try it: ask a question as a visitor; sign in as `meera` and send "Please arrange a make-up class for the session I missed in PYD-301."
(dates in the sample data are relative to the day you ran `make db-seed`); in a second browser window sign in as `staff1`, open
*Make-up approvals* and approve; watch the student window update. `make review ARGS="list"` still works and sees the same queue.

Checks you can run: `make test`, `make test-pg`, `make frontend-test`, `make frontend-build`, and with both servers up
`pip install playwright && playwright install chromium && E2E_PASSWORD=... make e2e` (walks all three screens; screenshots in `./e2e-shots`).

Creating real accounts: `SG_NEW_PASSWORD=... .venv/bin/python backend/scripts/create_account.py --tenant T --username U --role student --student-id ID`
(roles: student, staff, admin; `--disable` turns an account off at once; password >= 10 chars, never passed on the command line).

## API (all under `/api`)

| Method + path | Auth | Notes |
|---|---|---|
| `GET /health`, `GET /config?tenant=` | none | tenant name and `is_sample` |
| `POST /auth/login` | none | `{username, password, tenant?}` -> signed token. 5 failures per user+IP lock that key for 5 minutes (429). Same 401 for unknown user, wrong password, disabled account. |
| `GET /auth/me` | any | |
| `POST /chat`, `GET /chat/{ticket}` | visitor or student | server-minted signed ticket ids; `SG_CHAT_MAX_MESSAGES_PER_MINUTE` per student or visitor IP (429; code default 30, `.env.example` sets 20) |
| `GET /staff/escalations?status=`, `GET/POST /staff/escalations/{id}[/claim\|/resolve]` | staff, admin | own tenant only |
| `GET /staff/approvals`, `POST /staff/approvals/{id}/decision` | staff, admin | `{approve: bool}`; approver = signed-in account |
| `GET /admin/analytics?days=1..90` | admin | |

Responses expose what a UI needs. They never contain prompts, triage output, QA codes or other tenants' data. Unexpected errors return
`{"detail": "something went wrong (reference ab12cd34)"}` and the stack trace goes to the server log only.

## Settings (`.env`)

`SG_AUTH_SECRET` (required, >= 32 chars), `SG_TOKEN_TTL_MINUTES` (480), `SG_LOGIN_MAX_FAILURES` (5), `SG_LOGIN_LOCKOUT_SECONDS` (300),
`SG_CHAT_MAX_MESSAGES_PER_MINUTE` (30), `SG_CORS_ORIGINS` (empty: the frontend proxies `/api`). Frontend: `SUPPORTGRAPH_API_URL`
(default `http://127.0.0.1:8000`) tells Next where to forward `/api`.

## Security model in one paragraph

Tenant, role and student id come from the signed token; a body that tries to supply them is rejected (422). Conversation ids are signed and
bound to tenant and person. Staff and admin only ever see their own tenant (the tenant is part of every key). Staff cannot use the student
chat, students cannot open staff routes, and the account is re-checked on every request so disabling it cuts off live tokens.
Escalation briefs show contact details masked. Rate limits and lockouts exist but live in process memory.

## Known limitations (read before calling this production-ready)

- **Not production-ready.** No refresh tokens, MFA, SSO, password reset or email verification; accounts are operator-created.
- Rate limiting and login lockout are **per API process** (in memory): behind several workers or replicas, limits multiply. Use a shared store.
- Tokens are stateless and live in `sessionStorage`; XSS would expose them. Move to httpOnly cookies + CSRF protection before real deployment. Always serve over HTTPS.
- The PostgreSQL checkpointer still uses **one shared connection**; add a pool before concurrent production traffic.
- Students are **not notified** when staff decide; they see the outcome when they have the chat open (polls every 4 s) or when they return.
- Visitors who escalate cannot be contacted (no contact capture); the reply says so.
- Analytics read the `chat_events` table written by the API: turns before step 5 are not included, and event-write failures are swallowed on purpose (analytics must never break a chat).
- Only the sample tenant has a `config.yaml`; another tenant's staff can authenticate by token but the login page and analytics need its config.
- All models are still the offline fakes: the UI exercises real plumbing, but answer quality with a real LLM and embeddings has not been evaluated.
