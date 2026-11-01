# 0001: Change the domain from a retail store to a training institute (QSpiders / PySpiders)

Date: 2026-11-01. Status: accepted (requested by the project owner).

**Decision.** SupportGraph now targets a training institute. Prospects ask about courses, eligibility, fees, admissions and
careers; enrolled students ask about schedule, attendance, assessments and administration. Tenant `qspiders_sample` replaces
`acme_store`; `other_institute` replaces `other_store` as the isolation fixture.

**What changed in the architecture (and what did not).**
- Same: multi-tenant keys, trusted scope in config (never in tool arguments), LangGraph, Postgres + pgvector, Langfuse, fakes by default.
- Scope field `customer_id` became `student_id`; prospects use `anonymous` and have no personal records.
- Documents' audience `customer` became `public` (it was never only about customers: it means "safe for any outside asker").
- Orders/returns tools became enrolment tools: batches, schedule, attendance (threshold from tenant data), assessments.
- Migration `0003` carries the change; it deletes only the two known retail demo tenants.

**Data honesty rule.** Only public facts that were actually read may be marked `provenance: public` (with URL and date). Everything
else is invented and marked `provenance: sample`, with "(SAMPLE)" in the title that is shown beside answers. Real fees,
eligibility and policies must come from the institute before real use.

**Consequences.** Retail ZIPs delivered earlier no longer match the schema. The original brief still describes the retail store; update it.
