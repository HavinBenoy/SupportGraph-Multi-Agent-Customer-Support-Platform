# CHANGES: M1a-6 (reasoning never shown; refusal guard refined after replaying your stored answers)

Small delta on top of m1a ... m1a5. No `.env`, schema, migration, prompt, retrieval or model-default change (default stays `qwen3:1.7b`). Previous notes: `docs/history/CHANGES_M1a5.md`.

## What your replay output showed
1. The thinking-only `qwen3:4b` writes its chain of thought into the reply and ends it with `</think>`. The product had no handling for that: the student would have seen the reasoning, and
   `[source#id]` markers inside it would have counted as citations. All 16 "ok" guard hits were this (the guard fired on the reasoning, not the answer; the final answers were correct).
2. One genuine false positive from the instruct 4B (T161, a two-part question): "duration is not mentioned [..]" + a correct cited instalments answer. The first guard would have abstained on all of it.
3. The remaining flags were `INSUFFICIENT_CONTEXT` replies, which already abstain.

## Changed
- `backend/app/llm/reasoning.py` (new): `visible_answer()` removes `<think>...</think>` blocks, or everything up to a lone `</think>`; an unclosed `<think>` means no answer.
- `backend/app/rag/faq.py`: `generate` keeps only the visible answer; `validate` adds reason `unfinished_reasoning`; the own-words guard is now `declines_without_answer()` (a decline with NO other cited sentence; a trailing citation stays with its sentence). `declares_no_information()` is kept for the benchmark's informational field.
- `backend/app/qa/critic.py`: the QA reviser also uses only the visible answer (an unfinished one is discarded).
- Tests (`tests/rag/test_faq_truncation.py`): +8 (reasoning stripping, citations inside reasoning do not count, unfinished reasoning, two-part answers, bare refusals, trailing citations).
- `docs/MODEL_SELECTION.md`, `docs/history/CHANGES_M1a5.md`.
- Not changed: benchmark scoring (it still judges the raw text, so a thinking model's reasoning would be in what it scores; that model is excluded anyway).

## Executed (sandbox) / not executed
| Check | Result | Where |
|---|---|---|
| Full SQLite backend suite | **702 passed, 28 skipped** (was 696; +6 net) | my sandbox |
| Postgres + pgvector suite | **730 passed** (was 724) | my sandbox |
| The replay snippet | ran on synthetic mock output only (shape check); **not on your real answers** | your laptop |
| Real model behaviour | **not run** | your laptop |

## Run on your laptop
```bash
unzip -o supportgraph-m1a6.zip
make test-fast                                      # expect: 702 passed, 28 skipped
# Replay EVERY stored answer through the product's real validation logic (no model needed):
cd backend && ../.venv/bin/python - <<'PY'
import json, glob, collections
from app.llm.reasoning import visible_answer
from app.rag.faq import declines_without_answer
from app.llm.fake_faq import SENTINEL
c = collections.Counter(); odd = []
for f in sorted(glob.glob("../evals/**/faq-*.json", recursive=True)):
    for r in json.load(open(f))["rows"]:
        if "text" not in r: continue
        vis, unfinished = visible_answer(r["text"])
        shown = not (r.get("truncated") or unfinished or not vis or SENTINEL in vis or declines_without_answer(vis))
        c[(f.split("/")[-1], "must_abstain" if r.get("must_abstain") else "answerable", "shown" if shown else "abstained")] += 1
        if r.get("must_abstain") and shown: odd.append(("UNANSWERABLE SHOWN", f.split("/")[-1], r["id"], vis[:160]))
        if not r.get("must_abstain") and not shown and r["label"] == "ok": odd.append(("CORRECT ANSWER LOST", f.split("/")[-1], r["id"], vis[:160]))
for k, v in sorted(c.items()): print(v, k)
print("\nPROBLEMS:"); [print(" ", o) for o in odd] or print("  none")
PY
```
Send me that output. "UNANSWERABLE SHOWN" is the dangerous direction; "CORRECT ANSWER LOST" is the cost.
