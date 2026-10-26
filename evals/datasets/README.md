# Synthetic tickets

`tickets_acme_v1.jsonl`: one labelled ticket per line for the `acme_store` tenant, written to match the seeded
mock store (`backend/app/db/seed_data.py`) and the policy docs (`tenants/acme_store/docs/`).
Used from build step 3 (triage accuracy, routing) and step 6 (full evals). The file is the source of truth; edit it directly.

| Field | Meaning |
|---|---|
| `id`, `tenant_id`, `customer_id`, `channel`, `language`, `message` | the input |
| `category` | `order_status` `returns` `refund` `shipping` `billing` `account` `product_question` `security` `safety` `other` |
| `priority`, `sentiment` | `low/medium/high/urgent`, `positive/neutral/negative/angry` |
| `expected_route` | `knowledge` (answer from docs), `action` (use tools), `escalate` (human), `clarify` (ask / refuse politely) |
| `expected_tools` | read tools a good run calls |
| `expected_write_action` | `null`, `create_return` or `refund`: what a write step would propose (build step 4) |
| `needs_human` | should end with a human involved (escalation precision/recall) |
| `must_abstain` | answer is NOT in the docs; the bot must say it does not know |
| `expected_facts` / `must_not_contain` | strings a correct reply must / must not contain (groundedness, leakage) |
| `tags` | `adversarial`, `injection`, `cross_customer`, `cross_tenant`, `boundary`, ... for slicing metrics |

Labels are my best judgment of correct behaviour, not ground truth from real customers. Review them; disagreements are useful design discussions.
