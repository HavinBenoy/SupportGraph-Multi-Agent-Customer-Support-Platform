# Synthetic tickets

`tickets_qspiders_v1.jsonl`: 57 labelled messages, one per line, for the `qspiders_sample` tenant. They are written
to match the seeded sample data (`backend/app/db/seed_data.py`) and the help documents
(`tenants/qspiders_sample/docs/`). Used by the FAQ baseline evaluation now (build step 2), and by triage/routing
accuracy (step 3) and the full evals (step 6) later. The file is the source of truth; edit it directly.

**Everything here is synthetic.** The messages are invented, not collected from real students or prospects, and the
facts they expect come from clearly-labelled sample documents (plus one page of public website facts).

| Field | Meaning |
|---|---|
| `id`, `tenant_id`, `student_id`, `channel`, `language`, `message` | the input. `student_id` is `anonymous` for a prospective student (not signed in, no personal records) |
| `persona` | `prospect` (asks about courses, fees, admissions) or `student` (signed in; schedule, attendance, assessments) |
| `category` | `courses` `eligibility` `fees` `admissions` `placement` `attendance` `assessment` `schedule` `privacy` `other` |
| `priority`, `sentiment` | `low/medium/high/urgent`, `positive/neutral/negative/angry` |
| `expected_route` | `knowledge` (answer from docs), `action` (use the student's own records via tools), `escalate` (human team, with a brief), `write` (a validated action that pauses for approval, build step 4), `clarify` (ask, or politely decline) |
| `expected_tools` | read tools a good run calls |
| `expected_write_action` | `null`, `request_makeup_class`, `update_contact_phone` or `raise_support_ticket`: the action a write/escalation step takes (build step 4). Tag `needs_clarification` = the bot must ask a question instead of pausing |
| `needs_human` | should end with a human involved (escalation precision/recall) |
| `must_abstain` | the answer is NOT in the docs; the bot must say it does not know |
| `expected_facts` | strings a correct reply must contain (answer relevance) |
| `expected_docs` | the document(s) that hold the answer (retrieval quality: did the right document come back?) |
| `must_not_contain` | text that must never appear (leakage of internal policy or other people's data) |
| `tags` | `adversarial`, `injection`, `cross_student`, `cross_tenant`, `pii_fishing`, `internal_probe`, `boundary`, `non_english`, ... for slicing metrics |

Labels are one person's judgment of correct behaviour, not ground truth from real conversations. Review them;
disagreements are useful design discussions.
