# Part 2 — recall, precision and verification: implementation and testing report

Scope: the document-centric knowledge base stays. No structured fact store, graph, new service or migration (alembic stays at 0012). Everything below is additive and switchable.

## 1. What was wrong (inspection)

| Finding | Effect |
|---|---|
| Discovery judged whole documents: old value present, title words, lexical overlap, one similarity score | A line like "Jyothi sir handles ML sessions" was missed (no "machine learning" word); a line with the right person for another subject was nominated |
| "ML" and "Machine Learning" were different words | abbreviations broke every subject check |
| Names matched only in the exact spelling | "Mr. Jyothi", "Jyothi" (no "sir"), "Jyoti" were not found |
| Table rows were read without their header; answers without their question | "| Machine Learning | Jyothi sir |" and "A: Jyothi sir." had no subject |
| New-value-only changes depended on a rigid "<subject> <attribute> is <value>" line | other shapes were missed; a contradicting line (ML faculty is Ramesh sir) became a **definite** edit |
| No look at the published base after release | missed references stayed unnoticed |
| Retrieval was vector-only; long tables were cut without their header | rare words (names, codes, abbreviations) lost; table chunks lost their meaning |

## 2. What was built

See `docs/PART2.md` §4c. In short: unit-level evidence scan (`impact/sections.py`, `scan.py`), person and subject aliases (`aliases.py`, tenant `entity_aliases`), learn-the-old-value rounds,
six passage classes with a stored evidence string, deterministic guards after any model, post-publication verification (`verify.py`, admin "Check again" endpoint), hybrid BM25 + vector
retrieval with rank fusion, alias expansion in query rewriting, table chunks that repeat their header. Frontend: per-passage class and evidence in the decision cards, learned-value and
ambiguity notes in the analysis banner, a follow-up-check panel for administrators.

## 3. Measured results (sandbox, deterministic rule engine, 15-document synthetic corpus, 8 passages that must change, 9 look-alikes that must not)

| Change text | Engine | Passages found (of 8) | Wrong definite edits | Definite edits proposed | Passages left for a person |
|---|---|---|---|---|---|
| "…Machine Learning faculty is Santhosh sir instead of Jyothi sir." | v1 | 4 | 1 | 5 | 0 |
| | **v2** | **8** | **0** | 8 | 3 |
| "…Machine Learning faculty is Santhosh sir." (old value unknown) | v1 | 1 | 1 | 2 | 0 |
| | **v2** | **8** | **0** | 8 | 3 |
| "From today ML trainer is Santhosh sir." (abbreviation) | v1 | 0 | 0 | 0 | 0 |
| | **v2** | **8** | **0** | 8 | 3 |

The three v2 review passages are intentional: a line that shares the subject with another ("Jyothi sir teaches Statistics and Machine Learning."), a contradicting value ("Machine Learning
faculty is Ramesh sir.") and a respelling ("Jyoti sir …"). They are shown as potential/ambiguous with evidence; nothing is changed without a person's decision. Historical lines (dated, "until …",
"(2024)"), another subject for the same person (Statistics) and subject-only descriptions are classified `historical` / `related` and never edited.

## 4. Tests

Run in the sandbox (SQLite, deterministic rule engine, fake embedder; **no Ollama, no real model**): see §6 for the totals. New tests: `tests/knowledge/test_recall_v2.py` (corpus, v1-vs-v2, classes,
rounds, evidence, person and alias building blocks, table and Q/A context, change phrasings), `tests/knowledge/test_ai_recall_flow.py` (HTTP end to end: v1 vs v2, several edits in one document,
publish then follow-up check with a reviewer-discarded item, verify permissions and state, a crashing check not undoing a release), `tests/rag/test_hybrid_and_tables.py` (BM25, fusion, keyword lane rescue,
tenant/uniqueness, alias rewrite, table header chunks), Vitest (follow-up check panel). Existing tests for stale versions, concurrent processing, publication failure/rollback and RBAC are unchanged and still run.

**Not run by me (need your machine):** the PostgreSQL suite (`make test-ai-pg` / tests/db — no database was running in the sandbox), anything with real Ollama models
(`make eval-impact REAL=1 …`), and a visual check of the Next.js screens in a browser.

## 5. Which model does what

* Deterministic (always): parsing of changes, value and person matching, alias expansion, section/table/Q&A unit scan, candidate selection, classification guards, patch construction, verification, BM25.
* LLM (optional, `kb_ai_mode=auto` with Ollama): judging the passages the scan could not settle, and parsing a description the rules cannot read. It cannot create evidence, cannot promote similar/shared/
  contradicting lines to definite and cannot bypass guards. Cost: one call per candidate document in an offline admin job.
* Qwen3 1.7B is enough for the deterministic-first design (it only chooses among numbered passages). Qwen3 4B Instruct is the better choice for the ambiguous passages and for free-text parsing when you accept slower
  analysis (admin job, a few seconds per document on CPU); switch with `SG_LLM_MODEL_IMPACT=<model>`. I have not benchmarked either in this sandbox, so this is a design recommendation, not a measurement.
* External GPT APIs: not integrated and not needed for correctness. If wanted later they would be optional, admin-side only, on public knowledge text (never student or staff data), enabled only by your explicit decision.

## 6. Test totals (sandbox, final code)
| Suite | Result |
|---|---|
| backend/tests/knowledge (full, includes the pre-existing M6/Part 2 tests) | 354 passed, 3 skipped (the skips are the gated real-model tests) — run before the last two small edits; the three files touched afterwards were rerun |
| New Part 2 tests (test_recall_v2, test_ai_recall_flow, test_hybrid_and_tables) | 25 passed after the last edit |
| backend/tests/rag + graph + unit | 981 passed, 2 skipped |
| backend/tests/db on a real PostgreSQL 16 (started locally in the sandbox, throwaway databases) | 81 passed |
| backend/tests/api, security, step4, calls, voice, eval regression | 510 passed, 1 failed on the first run: the offline evaluation gate (handled-correctly 87.7% -> 86.0%). Cause found by A/B: the query alias expansion added words to chat questions in BOTH directions. Fixed (abbreviation -> long name only); the gate then passes with the stored reference unchanged (87.7%, no gate loosened, no reference refreshed) |
| frontend: vitest | 230 passed; `tsc --noEmit` clean; `next build` succeeds |

## 7. Honest notes
* The offline evaluation uses the fake hash embedder, so it cannot show whether hybrid retrieval helps real questions: in that evaluation hybrid and vector gave identical numbers. Hybrid is on by default because it is a recall
  safeguard for rare words, but `SG_RAG_RETRIEVAL_MODE=vector` restores the old behaviour. Please run `make eval` / your own questions with real nomic-embed-text and compare before keeping it for the demo.
* Impact-analysis numbers in §3 come from a synthetic corpus I wrote while building the scanner; treat them as a regression benchmark, not as proof about your real documents.

## 8. Commands to run locally
```
unzip -o ../supportgraph-part2-recall-and-verification.zip     # from the repository root; no migration, no new dependency
cd backend && ../.venv/bin/python -m pytest -q tests/knowledge tests/rag tests/test_eval_regression.py
make test-ai-pg                                                   # PostgreSQL (needs make db-up)
cd ../frontend && npx tsc --noEmit && npx vitest run && npm run build
make eval-impact REAL=1 MODELS="qwen3:1.7b,qwen3:4b-instruct-2507-q4_K_M"   # needs Ollama; compares the two models on the gated real-model checks
```
Then restart the API. Try it in the app: Knowledge -> AI change -> "From today onwards, Machine Learning faculty is <new name> instead of <old name>." on your own documents; look at "How each passage was judged",
publish, and read the "Follow-up check" panel on the published request.

## 9. Known limits
Two rounds; no pronoun continuation across lines; English name heuristics; heuristic ambiguity rules; replacement is whole-line (cell rows replace the row line); the corpus is synthetic and small — real documents will
find cases it does not, which is why uncertain cases go to a person.
