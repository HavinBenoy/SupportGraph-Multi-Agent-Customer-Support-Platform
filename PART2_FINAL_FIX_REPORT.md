# Part 2 final fix: "related, no change" shown for a document that must be updated

## 1. Root cause
Reproduced in the sandbox before changing any code, through the full HTTP workflow with a stub chat model that returns the answer shapes a small model gives. The description "The machine learning faculty is changed to santhosh sir from today" produced exactly your screenshot: `related_no_change`, flag `scope: 1`, and the reason "The passage is about a different subject than the change. The passage states ... must be updated."

Three independent defects combined:
1. **Phrase entity terms defeated the scope guard.** A model returns the subject as a phrase (`"machine learning faculty"`). `out_of_scope()` compares entity terms with the single words of the passage (`scope_fraction`), so a phrase never matches and every passage looked like "a different subject". `evaluate.validate` then downgraded the model's definite/potential verdict to no_change.
2. **The guard kept the model's explanation.** `validate` prefixed its own message to the model's `reason`, so the screen showed a verdict of "no change" next to text arguing for a change, with a 0.9 confidence that belonged to the rejected verdict. This is the contradiction you saw.
3. **The engine could not represent a change with no old value.** `ChangeSpec.actionable()` required `old_value` and `new_value`. "X is changed to Y" states only the new value, so the rule parser found nothing and the engine depended on the model to supply an old value. Nothing checked that a model-supplied value occurs in the description.

Why the synthetic evaluation missed it: all 15 scenarios used descriptions of the form "from A to B", read by the rule parser, with clean single-word entity terms. No test used a phrase-shaped or ungrounded model answer, and the stub models in the unit tests returned clean specs. The perfect scores were true for the cases tested and said nothing about this input shape.

Supporting evidence from your own real-model run: in `make eval-impact REAL=1` every fee-scenario passage came back as `potential`, confidence `0.2`, no edits, in 3.3 s. That is the old "the model gave no usable answer" fallback, which means qwen3:1.7b often did not produce a valid structured `Evaluation` at all, and the old code then showed no proposal. I cannot confirm this from here (see section 5).

## 2. Fix (backend only; no migration, no frontend change)
| File | Change |
|---|---|
| `impact/spec.py` | `ChangeSpec` normalises entity terms to single words and removes the words of the new/old value; slot changes ("X is changed/updated/replaced to Y", "change X to Y", "X is now Y", "new X is Y") parsed with attribute + subject + new value and no old value; `from today/now/immediately` = current fact, `from tomorrow` = calendar date |
| `impact/passages.py` | `slot_passages`: finds "<subject> <attribute> is/are/:/= <value>" lines and resolves each line's current value as the old value; value must be the right kind (name, amount, time) |
| `impact/discover.py` | counts slot occurrences on lines about the subject when nominating documents |
| `impact/evaluate.py` | `ground_spec` (model values must occur in the description); rule parser first, model only when the rules cannot read the sentence; rule engine cross-checks every model verdict (affected + model says no change = potential with the grounded replacement and the disagreement stated; unusable model rewrite = rule rewrite; model outage = potential, never definite); guard overrides now replace the explanation (model text kept as `model_note`); self-contradicting "no change" becomes potential for review; "already says the new value" = no change; prompt v2 |
| `impact/values.py` | a lower-case new name takes the document's capital ("Santhosh sir") |
| Tests/tooling | see section 4; `make eval-impact REAL=1 MODELS="a,b"` |

Safety is not weakened: historical, different-scope, already-new and unsupported-replacement guards are unchanged or stricter, a definite still needs confidence ≥ 0.75, human decision and admin approval are untouched.

## 3. Behaviour now (verified)
Your scenario: document `Data-Science` "Machine Learning faculty is Jyothi sir" → **definite**, confidence 0.91 (rules), proposed replacement `Machine Learning faculty is Santhosh sir`, no "(effective today)" suffix, explanation consistent. With a model that says "no change" or is down: **potential**, confidence ≤ 0.5, same grounded replacement, explanation says the model was unsure. The unrelated course document is untouched.

## 4. Regression coverage and tests actually run (sandbox)
New `tests/knowledge/test_ai_faculty.py` (26 tests, HTTP end to end): the exact failure under six model behaviours (rules, model says definite, model says no-change-while-explaining-update, irrelevant, potential without replacement, model down); same old value in an unrelated subject; different existing faculty; historical documents; current / future / "tomorrow" dates; document already holding the new value; same fact in several documents (list and colon forms); similar courses; ambiguity (asks, does not guess); mentions that need no update; unsupported model rewrite; re-analysis and retry; stale document; staff manual edit + discard, admin approval and publish; admin rejection; multi-document publication failure and recovery. Plus two evaluation scenarios (s16, s17) and the optional real-model test file.

| Check | Before fix (your machine, Part 2 delivery) | After fix (sandbox) |
|---|---|---|
| `make test-ai` equivalent (knowledge + migration tests) | 331 passed, 1 skipped | included in the full runs below |
| Backend SQLite whole suite | 1916 passed, 77 skipped | **1945 passed, 78 skipped** |
| Backend PostgreSQL 16 whole suite | 1989 passed, 4 skipped | **2018 passed, 5 skipped** (first attempt errored because the sandbox Postgres service had stopped; restarted and rerun in full) |
| `test_postgres_ai_release.py` | 9 passed | 9 passed |
| Evaluation | 16 passed, 15/15 exact | 18 passed (incl. s16, s17), 16/16 exact, 0 wrong edits |
| Frontend vitest / tsc / build | 229 / clean / ok | 229 passed / clean / ok (no frontend files changed) |

One existing test changed on purpose: a model outage used to produce "potential, no edit"; it now produces "potential, low confidence, the rule engine's grounded proposal" (still never definite, still needs the staff decision), and a test of the model's spec was updated because an invented value is now rejected.

## 5. Real-model results: none from the sandbox
Ollama and the Qwen models are not available here and the sandbox cannot download them. **Every test above uses stub models or the rule engine; none exercised Qwen.** I wrote real-model tests for you to run: `tests/knowledge/test_ai_real_model.py` (your failing scenario, the fee scenario with history and another course, same value in another subject). They assert safety properties that hold for any model and print each model's own answer. They were collected and skipped here, so they are untested against a model.

## 6. Apply locally
```
cd "~/SupportGraph Project/supportgraph"
unzip -o ../supportgraph-part2-final-fix.zip      # no migration; alembic stays at 0012
make test-ai && make test-ai-pg && make eval-impact
make eval-impact REAL=1 MODELS="qwen3:1.7b,qwen3:4b-instruct-2507-q4_K_M"
```
Then re-run your change in the UI (re-run analysis on the existing request).

## 7. Limitations
* Real Qwen behaviour is unverified by me; please send me the `REAL=1` output. The 1.7B model may well give invalid structured output often; the fix makes that case safe and useful (rule-engine proposal marked potential) rather than good.
* Slot changes need the current value on a line like "<subject> <attribute> is/are/:/= <value>"; sentences without that shape need the old value stated.
* The rule parser reads a limited set of English phrasings; others go to the model, whose values must then occur in the description.
* The attribute word list (faculty, trainer, fee, timing, duration ...) is fixed; unknown attributes still ask you to restate.
* Re-running analysis discards earlier decisions (unchanged).
