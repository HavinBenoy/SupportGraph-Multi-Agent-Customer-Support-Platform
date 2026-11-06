# CHANGES: M1a-5 (refusals in the model's own words now take the abstention path)

Small delta on top of m1a ... m1a4. No `.env`, schema, migration, prompt or retrieval change; default model stays `qwen3:1.7b`. Previous notes: `docs/history/CHANGES_M1a4.md`.

## Finding (your T129 output)
"Can I get an education loan through you?" -> 4B answered "The sources do not mention anything about education loans or financial assistance options. [about-the-institute#5]".
That is an honest refusal, not an invented policy, but it is not the exact `INSUFFICIENT_CONTEXT` marker. The benchmark scorer counted it as `answered_unanswerable`, and the product
validator would have shown it as a cited answer (no standard abstention wording, no handoff, logged as answered). The pre-stated revert rule stays honoured (FAQ default = 1.7b).

## Changed
- `backend/app/rag/faq.py`: `declares_no_information()` + `validate` reason **`model_declined`**: a reply that says, in its own words, that the sources do not contain the answer becomes the standard abstention, even with a valid citation. Narrow patterns; only ever moves a reply TOWARDS abstention. Known cost: a mixed reply ("the sources don't mention X, but Y") also abstains (fail closed).
- `backend/app/bench/llm_bench.py`: each FAQ row records `declines_in_own_words` (the product's own check; informational only). **Scoring criteria unchanged.**
- `backend/app/bench/report.py`: `REVIEW.md` also lists `answered_unanswerable` rows.
- Tests: 11 new in `tests/rag/test_faq_truncation.py` (6 refusals detected, 5 ordinary/negative answers not flagged, validator path), 1 extended in `tests/bench/test_bench_kit.py`.
- `docs/MODEL_SELECTION.md`: T129 detail; what re-qualifying the 4B needs. `docs/history/CHANGES_M1a4.md`: previous notes.

## Executed (sandbox) / not executed
| Check | Result | Where |
|---|---|---|
| Full SQLite backend suite | **696 passed, 28 skipped** (was 684; +12) | my sandbox |
| Postgres + pgvector suite | **724 passed** (was 712) | my sandbox |
| The false-positive snippet below | runs on synthetic mock output only (0 flags); **not run on your real answers** | your laptop |
| Real model behaviour with the guard | **not run** | your laptop |

## Run on your laptop
```bash
unzip -o supportgraph-m1a5.zip
make test-fast                                       # expect: 696 passed, 28 skipped
# Free false-positive check of the new guard on EVERY answer you already have (no model needed):
cd backend && ../.venv/bin/python - <<'PY'
import json, glob, collections
from app.rag.faq import declares_no_information
n = collections.Counter(); hits = []
for f in glob.glob("../evals/**/faq-*.json", recursive=True):
    for r in json.load(open(f))["rows"]:
        if "text" not in r: continue
        flag = declares_no_information(r["text"]); n[(r["label"], flag)] += 1
        if flag and r["label"] not in ("answered_unanswerable",) and not r.get("must_abstain"): hits.append((f.split("/")[-1], r["id"], r["label"], r["text"][:140]))
print(dict(n)); print("answerable questions the guard would turn into abstentions:"); [print(" ", h) for h in hits]
PY
```
Send me that output: it shows how many currently-correct answers the guard would have abstained on (the cost) and how many unanswerable ones it catches (the benefit).
