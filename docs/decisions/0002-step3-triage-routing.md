# 0002: Step 3 - triage, routing, knowledge and action agents

Status: accepted (build step 3, 2026-11-02). Roadmap source: PROJECT_BRIEF.md section 7 ("Triage + conditional routing +
Knowledge agent + Action agent (read tools)") and section 4.

## Decisions

1. **The router is code, not a model.** Triage output is a suggestion; `decide_route` applies ordered rules; tools authorise via the
   trusted run config. Reason: "LLMs for judgment, code for rules". A mislabelled or injected message cannot widen access.
2. **Rule order:** injection screen, escalation/urgent, other-person data, chit-chat, low confidence, own-records (signed-in only), knowledge.
   Escalation outranks low confidence: a vague but alarming message should reach a person. Trade-off: over-escalation, tracked as precision.
3. **Corrective RAG-lite.** One capped retry with a reworded query. The step 2 FAQ graph is reused unchanged as the answer unit
   (only an optional `search_query` was added), so every step 2 guarantee carries over. No separate LLM grader yet.
4. **Query rewriting never changes the question the answer model sees.** Only retrieval uses the expanded query.
5. **Action branch reuses the step 0 ReAct graph.** No second agent. Tool-use is recorded in `tools_called` for evaluation.
6. **Fail safe, not fail open:** triage errors become low confidence (ask); a missing tenant raises `ScopeError`; the `anonymous` id is rejected by `require_scope`.
7. **No database change.** Conversation state is in-graph; persistence/checkpointing is step 4.
8. **Offline fakes (RuleTriage, GlossaryRewriter) for tests and demos; LLM versions behind the same interfaces.** No real provider was introduced.
9. **Escalation node is honest and minimal:** sets `needs_human` and says no follow-up can be arranged from chat yet.

## Not done / deferred
Real-model calibration (`SG_TRIAGE_MIN_CONFIDENCE`, `SG_RAG_MIN_SCORE`), LLM relevance grader, authentication, PII masking in traces,
rate limiting, student-only documents, write tools and approvals (step 4).
