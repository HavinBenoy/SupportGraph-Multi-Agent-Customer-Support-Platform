# M1b - real local AI on the text path

Configuration (decided in M0/M1a, not reopened): FAQ `qwen3:1.7b`, triage `qwen3:1.7b`, embeddings `nomic-embed-text`, provider Ollama, CPU.

## What is in the code
| Concern | Where |
|---|---|
| Provider/config | `app/llm/gateway.py` is the only module that knows the provider; per-role models via `SG_LLM_MODEL_<ROLE>`. Unchanged. |
| Safe activation | `app/llm/preflight.py`: at API start, `SG_LLM_PREFLIGHT=off\|warn\|strict` checks the server is reachable and every model is pulled (never contacts a non-local endpoint unless `SG_ALLOW_EXTERNAL_LLM=true`); `SG_LLM_WARMUP=true` loads models in a background thread. Fake providers: nothing happens. |
| Nomic embeddings | `effective_embedding_prefixes`: `search_query: ` / `search_document: ` added automatically for `nomic-embed-text` on a real provider (`SG_EMBEDDING_PREFIX_MODE=auto`). The prefix choice is part of the stored embedding name (`...@px-xxxxxx`), so vectors made with a different setting are never compared. |
| Reasoning | `visible_answer` (FAQ, rewriter, QA reviser) and `strip_message` (action agent) remove `<think>` text before anything is validated or shown; an unfinished block means no answer (abstain). |
| Citations/abstention | Unchanged FAQ validator: truncation, unfinished reasoning, sentinel, own-words decline, uncited, unknown citation. |
| Escalation/RBAC | Unchanged: the code-level escalation floor can only add escalations; own-records need sign-in; tenant/student scope is injected by code. |
| Unmeasured extras | LLM critic and LLM rewriter stay OFF (`SG_QA_LLM_CRITIC`, `SG_REWRITER_MODE`) until measured on your machine. |

## Turning it on (your `.env`; nothing here edits it)
```
SG_LLM_PROVIDER=ollama
SG_EMBEDDING_PROVIDER=ollama
```
Then `make ingest` (adds vectors under the new embedding name; deletes nothing), `make llm-smoke`, `make e2e-local`.
Rolling back = set both back to `fake`. No migration, no new dependency.

## Fixes after the first real-model e2e run (batches 1-3)
| Finding | Cause | Fix |
|---|---|---|
| `human` clarified | triage `other` 0.5; no floor rule | floor rule `human_request` |
| out-of-scope / unanswerable clarified | triage `other` 0.5 < 0.6 | router: `other` at 0.4..<0.6 -> grounded FAQ (`SG_TRIAGE_OTHER_FLOOR`) |
| `faq-courses` abstained | correct answer, no citation (`uncited_answer`) | one validated citation retry (`SG_FAQ_CITATION_RETRIES`) |
| `faq-fee` escalated once | 1.7B triage unstable between runs; prompt left `concession_request` undefined | prompt definitions; `make e2e-local` now reports triage stability |
| `write` | test used an action that does not exist | e2e uses labelled ticket T038 (make-up class) |
Known limit: the 1.7B triage can still vary between runs; the escalation floor, router rules and the validated FAQ path keep the outcome safe whatever it returns.
