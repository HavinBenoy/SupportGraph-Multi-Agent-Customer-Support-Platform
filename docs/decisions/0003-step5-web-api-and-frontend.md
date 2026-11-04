# 0003: Web API, authentication and frontend (build step 5)

Status: accepted. Date: 2026-11-03.

## Decisions

1. **Identity is never taken from the request body.** Signed-in calls get tenant, role and student id from an HMAC-signed, expiring
   token. Anonymous visitors get the placeholder student id `anonymous` (which `require_scope` already refuses for personal data), and
   their tenant comes from the request only because it is public configuration.
2. **Conversation ids are minted by the server and signed.** `a-…` (visitor, bound to tenant) and `s-…` (student, bound to tenant AND
   student). A client cannot invent an id, reuse another person's, or cross tenants: a forged or foreign id is a 404 that looks the same as
   an unknown one. The LangGraph thread id still includes the student id, so this is a second lock on the same door.
3. **A valid signature is not enough.** Every authenticated request re-reads the account: disabling an account or changing its role takes
   effect immediately, not at token expiry. Cost: one primary-key lookup per request.
4. **No public sign-up.** Accounts are created by an operator script (`scripts/create_account.py`, `make demo-accounts` for the sample
   tenant only). Passwords: scrypt with per-password salt; the same work is done for unknown users to blunt user enumeration.
5. **Staff decisions resume the paused graph.** `POST /api/staff/approvals/{id}/decision` calls the existing `resume_ticket` with
   `Reviewer(tenant, <signed-in username>, "staff")`. The approver recorded in the audit log is the authenticated account.
6. **Frontend = Next.js (App Router, TypeScript, plain CSS), same-origin `/api` proxy.** The browser never learns the API origin, so CORS
   is off by default. Token in `sessionStorage` (gone when the tab closes). Trade-off: any XSS could read it; httpOnly cookies would need
   CSRF protection and are the next hardening step. React escapes all rendered text; no `dangerouslySetInnerHTML` anywhere.
7. **Analytics measure what the system did** (turns, routes, abstentions, escalations, approvals, latency) from `chat_events` plus the
   existing tables. They do not measure answer quality: that is `make eval-routing` and human review.
8. **Role checks are enforced by the server; the frontend gates are usability only.**

## Consequences

- Pre-existing known gaps stay open and are listed in `docs/STEP5.md` (rate limiter is per process, no refresh tokens/MFA/SSO, polling
  instead of push, single shared checkpointer connection).
