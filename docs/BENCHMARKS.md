# How to read the benchmark results

`make bench-llm`, `make bench-embed` and `make bench-report` write JSON files to `evals/bench/` and a summary to `evals/bench/SUMMARY.md`.
Everything here is measured on your machine with synthetic questions and documents, using deterministic checks (no second model judges the first).

## Splits
Dev and held-out are always separate rows and separate numbers; nothing is merged. A split has about 25-30 answerable FAQ cases, so
a 95% interval is roughly +/- 15-18 points (the JSON has `correct_ci95`). **Differences smaller than that are noise.**

## FAQ columns
| Column | Meaning |
|---|---|
| answerable (ctx-sufficient / gap) | answerable questions, split into those whose required facts were in the context given to the model and those where they were not (a retrieval gap of the benchmark's cheap lexical retrieval) |
| correct, all answerable | the original pass rule: answered, cited, every cited id was in the context, every expected fact appears, **and not truncated** |
| correct, ctx-sufficient | the same rule, only where the context held the facts: the fairest measure of the model itself |
| over-abstain raw | the original definition: any refusal / all answerable. Includes refusals that were right because the context lacked the answer |
| over-abstain corrected | refusals (or answer-plus-refusal mixes) where the context DID hold the answer / ctx-sufficient cases |
| truncated | replies stopped by the token limit (`done_reason == "length"`). Always a failure, for answerable and must-abstain alike |
| bad citations | answers citing an id that was not in the context |
| must-abstain correct | refused correctly (and not truncated). Correct refusals are never counted as retrieval gaps |

### Failure taxonomy (one label per case, checked in this order)
`truncated` -> `answered_unanswerable` (must-abstain question answered) -> `retrieval_gap` (answerable, required facts absent from the context)
-> `over_abstain` -> `mixed_output` (answer and sentinel in one reply) -> `bad_citation` -> `no_citation` -> `missing_fact` -> `other`.
Failure classes in the JSON: retrieval = `retrieval_gap`; generation = `truncated`, `over_abstain`, `mixed_output`; citation = `bad_citation`,
`no_citation`; incorrect = `missing_fact`, `answered_unanswerable`, `other`. **The pass criteria are unchanged from the first run except that
truncated replies fail**, so numbers from the first run (schema 1) are not comparable and are refused by `bench-report`.

### Investigating a failure
Each FAQ row in `faq-<model>.json` has `question`, the full `text`, `done_reason`, `context_ids`, `facts_missing_from_context`,
`facts_missing` (from the answer), token counts and the provider's timing counters. Example:
`python3 -c "import json;[print(r['id'],r['label'],r['done_reason'],r['out_tokens'],repr(r['text'][:200])) for r in json.load(open('evals/bench/faq-qwen3_4b.json'))['rows'] if r['label']!='ok']"`.
`cold_start` also keeps the reply to "Reply with the single word: ready", with its `done_reason` and token count.

## Triage columns
Routes are computed by the support graph's **actual `route` node** (decide_route + the model-independent escalation floor), with your
`SG_TRIAGE_MIN_CONFIDENCE`. No copy of the logic is used; if that node cannot be reached the benchmark stops instead of approximating.

| Column | Meaning |
|---|---|
| route accuracy | the route the router would take equals the dataset's `expected_route`, over every ticket in the split. **This is the headline, not category.** |
| false escalations | tickets that should NOT escalate but would (a person gets a ticket they should not): n / non-escalation tickets |
| missed escalations | tickets that SHOULD escalate but would not: n / escalation tickets. Usually the costlier error |
| own-records flag | `wants_own_records` correct, only on tickets where the dataset implies the answer (see below) |
| category (secondary) | the old exact-category accuracy; it counts distinctions the router never acts on |
| unusable | the model's output could not be parsed or validated (becomes a low-confidence "ask for detail") |
| confidence p50 / below min conf | the model's stated confidence and how often it is under the clarify threshold |

`wants_own_records` expectation, derived from existing labels (no dataset edit): True for `expected_route == action` and for anonymous
"what is my attendance" requests tagged `needs_sign_in` (these were wrongly scored as errors before); False for record-topic policy questions
("what attendance do I need"); not scored for everything else, e.g. vague signed-in requests and make-up requests.
Triage rows keep `confidence`, `wants_own_records`, `priority`, the chosen route and reason. The raw JSON the model produced is not available
(structured output is parsed inside LangChain), so the parsed result is what is stored.

## Run manifest (top of the summary, in each JSON)
Run id, command line, splits, tickets per split, context mode, SHA-256 of both ticket files, of the tenant documents and of the FAQ and
triage prompt text (so a prompt change is visible), effective settings and role-specific model overrides, Ollama version, model digests and
package versions. `bench_code_sha256` hashes every file that produces or scores a result: `app/bench/{metrics,llm_bench,corpus,report,stats,embed_bench}.py`
and `scripts/bench.py`.

### Comparability check
`bench-report` compares the manifests of all files it summarises and, if anything differs, puts a **WARNING: ... Do not compare or combine them.** block at
the top of `SUMMARY.md`, grouped as:
- **Model builds, runtime versions or settings**: Ollama version; digest of the same model tag (same tag, different build); any effective setting
  (e.g. `llm_num_ctx`, `llm_max_output_tokens`, thinking mode); any role-specific model override; relevant package versions.
- **Benchmark configuration**: dataset hashes, tenant documents, FAQ/triage prompt, context mode, tickets per split, splits run, and which benchmark code files differ.
- **Hardware**: CPU model, cores, threads, RAM, architecture when results from different machines are combined.

Different model tags having different digests is expected and is not flagged. Files without a manifest (stt/tts) are not compared. A difference
means the numbers were produced under different experimental conditions: re-run with identical conditions before drawing a conclusion.

## Controlled variants (M1)
`bench.py llm` also accepts `--suites faq|triage`, `--no-cold-start`, `--tag NAME` (adds `--NAME` to the result file name, so a variant never overwrites the
baseline), `--set KEY=VALUE` (generation settings only: `llm_temperature`, `llm_top_p`, `llm_top_k`, `llm_repeat_penalty`, `llm_seed`, `llm_max_output_tokens`,
`llm_num_ctx`, `llm_thinking`; anything else is refused so an experiment cannot silently change the dataset, prompt or retrieval) and, before the sub-command,
`--out-dir`. The chosen settings are in the manifest, and the report's WARNING block fires if variants and baseline are summarised together. Use
`make bench-quick`, `make bench-full`, `make bench-experiment` (see `docs/TESTING.md`).

## Known limits
- The FAQ benchmark gives every model the same lexical top-k context, so it tests the model, not the product's embedding retrieval.
  Cases where that context lacks the facts are labelled `retrieval_gap`, not model failures.
- Fact checking is exact phrase matching (after normalising case and punctuation). A correct paraphrase can fail; read the stored text to judge.
- One run, temperature 0, no variance estimate. Run on a quiet machine; the system load is recorded.
- `done_reason == "length"` is also what the product would see; see the M1 prerequisites in `docs/ROADMAP.md`.
