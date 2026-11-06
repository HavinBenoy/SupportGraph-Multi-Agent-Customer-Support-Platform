# M1b fix batch 2 of 3 - FAQ citation reliability (delta on top of batch 1)

Models, Nomic prefixes, abstention, escalation, RBAC, tenant isolation, approval flow, `.env`, database, schema and volumes are untouched.

## Hotfix included: tests no longer depend on your `.env` (found by your batch-1 `make test-fast` run)
`test_faq_workflow_expects_...` failed on your machine only: your `.env` now has `SG_EMBEDDING_PROVIDER=ollama`, and code reading `get_settings()` looked for vectors under the real embedding name while the test had ingested with the fakes. `tests/conftest.py` already forced `SG_LLM_PROVIDER=fake`; it now forces `SG_EMBEDDING_PROVIDER=fake` too (environment outranks `.env`). I reproduced the failure in the sandbox with `SG_EMBEDDING_PROVIDER=ollama` and verified the fix under both providers set to ollama. Your `.env` is not read or changed.

## Finding addressed
`faq-courses`: the 1.7B model wrote a correct answer ("Python Full Stack and Data Science with Python") with no citation. The validator correctly rejected it (`uncited_answer`), but the student got an abstention.

## Change (`app/rag/faq.py`, `app/config.py`)
- After `validate`, ONLY `reason == "uncited_answer"` triggers one `regenerate`: same messages plus one reminder ("your previous reply contained no source citation... end every factual sentence with an id such as [<a real retrieved id>]... or reply exactly INSUFFICIENT_CONTEXT").
- The retry output goes through the identical `visible_answer` (reasoning strip) and `validate` (truncation, unfinished reasoning, sentinel, own-words decline, uncited, unknown citation). If it fails again the bot abstains with the standard message. At most one retry (`SG_FAQ_CITATION_RETRIES`, default 1, 0 = off).
- No other failure is retried (truncated, unfinished reasoning, sentinel, decline, unknown citation). Code never adds a citation: the shown text is exactly what the model wrote.
- Cost: one extra model call (~2-3 s on your CPU) only when the model forgot to cite.
- State field `generation_attempts` (1 or 2) is available for diagnostics (used by e2e in batch 3).

## Tests actually run (sandbox)
| Run | Result |
|---|---|
| `tests/rag/test_faq_citation_retry.py` (new, 17 cases) | pass |
| 2 new mock-Ollama cases in `tests/graph/test_local_stack_mock.py` (real langchain-ollama client: forgets once -> cited on retry; never cites -> abstains after exactly one retry) | pass |
| Full SQLite suite, with `SG_LLM_PROVIDER=ollama SG_EMBEDDING_PROVIDER=ollama` exported to prove env-independence | 779 passed, 30 skipped |
| PostgreSQL suite, real Ollama | not in this batch (PostgreSQL in batch 3; real models are your run) |

## Verify locally
```
make test-fast
git diff --check
cd backend && ../.venv/bin/python -m pytest -q tests/rag/test_faq_citation_retry.py
make e2e-local      # faq-courses should now answer with a citation (watch its latency: ~2 calls when the model forgets)
```
Files modified: `app/rag/faq.py`, `app/config.py`, `tests/conftest.py`, `tests/bench/mock_ollama.py` (two test-only modes), `tests/graph/test_local_stack_mock.py`, `.env.example` (comments). Added: `tests/rag/test_faq_citation_retry.py`. Deleted: none.
Note: `app/config.py` also contains batch 1's `triage_other_floor` (already applied by you).
