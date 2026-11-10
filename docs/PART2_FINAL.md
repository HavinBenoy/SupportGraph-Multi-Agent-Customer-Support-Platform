# Part 2 — Generalized AI knowledge editing, adaptive context and closure

Final delivery of M6 Part 2. It replaces the value-replacement-only section judge by a general, validated, section-level editor, gives the model the complete logical section a change belongs
to, and carries both through the whole workflow. No migration, no new dependency, no new infrastructure; the document-centric architecture, hybrid retrieval, multi-round discovery,
immutable versions, staff request + admin approval, atomic publication, audit and RBAC are untouched.

## 1. Where the value-only assumptions were (all removed)

| Layer | Assumption | Now |
|---|---|---|
| Prompt (`judge.prompt`) | "Change nothing except the value the change names … do not add or remove lines" | `edit.prompt`: *understand the requested change and make the corresponding modification wherever it is applicable; if not applicable return the original section completely unchanged; preserve all unrelated information.* The model may add, change, rewrite or remove; no operation types are named. |
| Edit extraction (`judge.compare/minimal_edit`) | one line ↔ one line, only `old → new` token spans | `edit.revise`: line diff of the whole revised section → hunks (add / remove / modify), each a precise patch. |
| Section cutting (`judge.windows`) | ±3 lines, 1600 characters | `context.sections_for`: complete logical section within a token budget (§3). |
| Verification (`verify.py`) | scans for the old value only | value changes: unchanged scan; other changes: `compute_statements` (new text is live; removed/rewritten statements searched word for word). |
| Discovery gate (`analyse`) | no old/new value ⇒ "restate it" | a change that names an action is a **general** change; only a description with no action/target is still sent back for a question. |
| Judge entry (`evaluate.judge`) | returned early without a `new` value | removed; hints (`news`/`olds`) are optional. |
| Combination (`_compose`) | a line was either the rules' one-line replacement or nothing | validated hunks: same as rules ⇒ stays definite; richer ⇒ potential; model-only ⇒ potential; disagreement ⇒ potential with both stated. |
| Persistence | a suggestion edit = one passage | an edit is any span; insertions/deletions are anchored on a neighbouring unchanged line so spans are never empty (`apply_edits`, `final_edits`, `changes._lines_edits` need no change). |
| Frontend | one orange/blue pair per passage | kind label (Added / Removed / Rewritten), per-passage "Proposed: …", validator flags, statements-mode verification text. |

## 2. How a change is processed now

1. **Understand** (`spec.py`, `analyse.generalize`): value changes are parsed exactly as before. Anything else with an action word ("add", "remove", "now also", "no longer", "instead" …) or a target ("to", "from", "as") becomes a *general* spec carrying the subject words. A description that only says something changed ("We changed the fee.") asks one question and calls no model.
2. **Discover** (`discover.rank`): unchanged for value changes. General changes admit documents by lexical overlap (≥ 50 % of the subject words) or semantic similarity; per document, the lines that share the subject words (counting heading and title) are the focus (`evaluate._general_passages`). Alias expansion, table-aware chunking, hybrid retrieval and multi-round learning are unchanged.
3. **Context** (`context.py`, §3): the complete logical section around the focus lines.
4. **Ask** (`judge.ask`): the generalized prompt, thinking on, the section as text, the whole revised section back in one ```text block. Cached by prompt version, model, request and section text.
5. **Validate** (`edit.py`, §4) → hunks, or *unchanged*, or *invalid* (no edit).
6. **Optional second opinion** (`judge.semantic`, `SG_KB_AI_SEMANTIC_CHECK`): a short non-thinking PASS / FAIL / UNSURE call. It can only add a review flag; it never approves and never silently removes.
7. **Combine** with the rule evidence (`evaluate._compose`) and the existing guards (`validate`: historical, scope, already-new, uncertainty, confidence).
8. **Persist** as `KbChangeItem` with `ai_suggestion.edits` (+ `kind`, `flags`) and `ai_suggestion.passages` (+ `hunk`, `change`), diff built server-side.
9. **Staff decide** (accept / edit per passage / discard) → **admin approves** → **atomic publication** → **post-publication check** (§6).

## 3. Adaptive context (`impact/context.py`)

Three different limits, never confused:

| Limit | Setting | Meaning |
|---|---|---|
| Input capacity | `SG_KB_AI_CONTEXT_TOKENS` (1800) | document text per request |
| Output limit | `SG_KB_AI_JUDGE_MAX_TOKENS` (4096) | Ollama `num_predict`: thinking **and** the whole revised section |
| Total window | `SG_KB_AI_JUDGE_NUM_CTX` (8192) | Ollama `num_ctx`: prompt + thinking + answer; also the KV-cache RAM |

`Budget.from_settings` computes `section_tokens = max(150, min(input, (output − thinking reserve)/1.25, window − 450 − output))` and says which limit bound it, so a large input setting can never produce an answer that is cut off (the answer repeats the section) or overflow `num_ctx`. Tokens are estimated at 3.2 characters/token (conservative).

Section choice, largest first that fits: **whole document** → **sub-tree of the nearest heading** (heading, paragraphs, lists, tables, sub-sections) → **the section** (heading to next heading) → **blocks**. Blocks are paragraph, list (with its lead-in sentence and indented continuation), table (with its header), or question + answer; a block is never split unless it alone exceeds the budget, and then it is cut around the line (a table row is passed its header as read-only context if the header did not fit). Pronoun continuations stay with their paragraph. Windows of the same section are merged; windows that would overlap but are too large together are cut so no text is sent (or edited) twice. Sections beyond `SG_KB_AI_MAX_SECTIONS` per document and calls beyond `SG_KB_AI_JUDGE_MAX_CALLS` per analysis are *reported* (never silently skipped); answers are cached, so a repeat costs nothing.

CPU/RAM: the default section is ≤ 1800 tokens, the window 8192 tokens (≈ 1 GB KV cache for Qwen3 1.7B, ≈ 1.2 GB for 4B). Raise them together for experiments (`SG_KB_AI_CONTEXT_TOKENS=3000 SG_KB_AI_JUDGE_MAX_TOKENS=6144 SG_KB_AI_JUDGE_NUM_CTX=12288`); the budget lowers the section size by itself if you raise only one.

## 4. Validation (`impact/edit.py`) — replaces `minimal_edit`

Order: **extraction → comparison → per-hunk checks → section checks.**

* **Extraction** (`extract`): last complete fenced block; reasoning ignored. *No block, cut-off (finish reason `length` or unclosed fence/think), empty, or prompt-echo ⇒ a failed response*, never "unchanged".
* **Comparison**: `difflib` over lines (trailing whitespace and blank-line noise are not changes). Identical ⇒ **unchanged** (= "not applicable"; the original is preserved). Much shorter than the original ⇒ invalid ("looks incomplete, not a deliberate removal").
* **Per hunk** (a hunk that fails is *dropped*, the original lines stay, the reason is shown):
  headings are not changed/removed (a new heading may be added next to new content); a table keeps its header and its column count; **historical** — a changed line under a historical heading, or a historical/dated sentence (`previously`, `until`, `was`, an older year) that is not carried over verbatim, is rejected; **invented figures** — a number in added text that is in neither the section nor the request is rejected; **relevance** — the words that differ must occur in the request (or the new value must be there), otherwise it is "rewrites/deletes text the request does not concern"; **duplicates** of existing lines are rejected; names not in the document or request are flagged for review.
* **Per section**: more than `SG_KB_AI_MAX_REMOVED_FRACTION` of the text removed ⇒ rejected; more than `SG_KB_AI_MAX_CHANGED_FRACTION` of the lines changed ⇒ the whole revision is flagged; some hunks dropped ⇒ the accepted ones are kept and flagged ("part of the model's revision was dropped"); the old value still present in unchanged lines is noted.
* Nothing the model returns is executed; nothing is stored without these checks.

Valid add / remove / rewrite edits pass because nothing requires the change to be a substitution: relevance is judged on the *words that differ*, structure on headings/tables/history, safety on figures/duplicates/deletions.

### 4b. Relevance is judged by meaning, not only by shared words

A request is rarely worded like the text it concerns. If no run of the revision shares a word with the request, runs within 3 rows of a candidate line (or under the same small heading) are accepted **flagged for review** and trigger the second check; if some run matches by words, extra unrelated runs are dropped as before; far-away runs are always dropped. Focus lines for a general change count pronoun continuations, headings and request words, never action words.

## 5. Decisions

| Situation | Result |
|---|---|
| Applicable, validated | proposed edit |
| Model returned the section unchanged | original preserved; if the rules were certain this line is a **question** (`potential`, "judged NOT to apply"), neither side silently wins |
| Rules and model both made the same one-line replacement | stays `definite` (confidence blended) unless a flag exists |
| Model's revision differs from / is richer than the rules' | `potential`, shows both |
| Model-only change (no rule evidence; every general change) | `potential` — never auto-approved |
| Incomplete, ambiguous, malformed, invalid output, model unreachable | **no proposed edit**; the rules' result stands |
| Model asked but answer unusable / returned unchanged | the reviewer is told which (never silent) |
| Second check FAIL / UNSURE | review flag, edit stays `potential` |
| Historical, stale base version, overlapping request | unchanged safeguards (409 / left unchanged) |

Humans still decide every document (accept / edit / discard), an administrator still approves, and publication is still atomic with rollback.

## 6. Post-publication verification (`impact/verify.py`)

Value changes: the existing scan for the old value. Other changes: `compute_statements` confirms the added text is in the live document and searches the live base, word for word, for statements the edit **removed or rewrote**; historical lines are counted not listed. The report says `mode: "statements"`, `verifiable: false` and explains that a non-value change **cannot be proven complete automatically**. It never changes anything and never undoes a publication.

## 7. Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `SG_KB_AI_JUDGE` | `uncertain` | `uncertain` / `all` / `off`. General changes are always sent (there is no rule evidence to be sure about). |
| `SG_KB_AI_JUDGE_THINKING` | `true` | qwen3 thinking for this job only |
| `SG_KB_AI_JUDGE_TIMEOUT_S` | `300` | one model call |
| `SG_KB_AI_JUDGE_MAX_CALLS` | `16` | model calls per analysis |
| `SG_KB_AI_MAX_SECTIONS` | `6` | sections (calls) per document |
| `SG_KB_AI_CONTEXT_TOKENS` | `1800` | **input** capacity per request |
| `SG_KB_AI_JUDGE_MAX_TOKENS` | `4096` | **output** limit (`num_predict`) |
| `SG_KB_AI_JUDGE_NUM_CTX` | `8192` | Ollama **total** window (`num_ctx`) |
| `SG_KB_AI_THINKING_RESERVE_TOKENS` | `1500` | output tokens reserved for reasoning when sizing a section |
| `SG_KB_AI_CONTEXT_MODE` | `adaptive` | `fixed` restores `SG_KB_AI_JUDGE_WINDOW_CHARS` characters |
| `SG_KB_AI_JUDGE_WINDOW_CHARS` | `1600` | only for `fixed` |
| `SG_KB_AI_SEMANTIC_CHECK` | `uncertain` | `off` / `uncertain` (when a safeguard flagged something) / `all` |
| `SG_KB_AI_MAX_CHANGED_FRACTION` | `0.6` | more lines changed ⇒ whole revision to a person |
| `SG_KB_AI_MAX_REMOVED_FRACTION` | `0.5` | more text deleted ⇒ rejected |
| `SG_LLM_MODEL_IMPACT` | model of `SG_LLM_MODEL` | `qwen3:1.7b` or `qwen3:4b` |

Unchanged from earlier parts: `SG_KB_AI_DISCOVERY`, `SG_KB_AI_VERIFY`, `SG_KB_AI_MAX_CANDIDATES`, `SG_KB_AI_MAX_PASSAGES`, `SG_KB_AI_MODE`, `SG_RAG_RETRIEVAL_MODE`.

## 8. Tests — what was run and what was not

**Run in this sandbox (Linux, Python 3.13, scripted model, SQLite API fixtures + a local PostgreSQL 16):**

| Suite | Result |
|---|---|
| backend `tests/knowledge` + `tests/rag` (includes the new `test_judge.py` 32 tests and `test_generalized_flow.py` 12 HTTP end-to-end tests) | 643 passed, 3 skipped |
| backend everything else (api, security, step4, calls, voice, graph, db, unit, eval regression) | 1362 passed, 75 skipped (skips need external services/PostgreSQL env) |
| PostgreSQL (`tests/db`, with `SG_TEST_DATABASE_URL`) | 81 passed |
| new editing tests re-run on PostgreSQL | 44 passed |
| frontend `tsc --noEmit`, `vitest run`, `next build` | clean; 233 tests passed; build succeeded |

**Mocked (scripted model):** all of the above. The stub returns the revised section a good or a deliberately bad model would return, so these tests prove the context building, validation, decision handling, persistence, approval, publication, stale/concurrent/failure handling and RBAC, not Qwen's quality. Scenarios covered: replace a value; add an absent qualification; online -> hybrid; remove a restriction; add a table row/course; several related statements; history preserved / historical section protected; unrelated section unchanged; invented figures, heading/table-header edits, deletions, duplicates, truncated/empty/echoed output; conflicting rules-vs-model; vague request; large sections and context boundaries (budget clamps, block splitting, Q&A and table-header preservation, merge/non-overlap); stale version (409); two overlapping requests; publication failure with rollback and retry; staff edit of a multi-line revision.

**NOT run: real Qwen3 1.7B / 4B.** The sandbox has no Ollama and no model weights, so no real-model result exists and none is claimed. `backend/scripts/judge_probe.py --suite` runs the ten scenarios against your Ollama with the same prompt, budget and validator; please run the commands in section 9 and send me the output. Expect Qwen3 1.7B to miss some scenarios (those become "no proposal" or a flagged proposal, not wrong edits).

## 9. Exact local verification

```bash
cd supportgraph
unzip -o ../supportgraph-part2-closure.zip            # no migration, no new dependency; .env and your data are not in the zip

# 1. backend (offline; scripted model)
cd backend
../.venv/bin/python -m pytest tests/knowledge tests/rag -q
../.venv/bin/python -m pytest tests/knowledge/test_judge.py tests/knowledge/test_generalized_flow.py -q      # the new generalized-editing tests only
../.venv/bin/python -m pytest tests/api tests/security tests/step4 tests/calls tests/voice tests/graph -q

# 2. PostgreSQL (a throw-away database; nothing of yours is touched)
SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres ../.venv/bin/python -m pytest tests -q -k "postgres or pg" 

# 3. frontend
cd ../frontend && npx tsc --noEmit && npx vitest run && npx next build

# 4. REAL model (needs `ollama serve`; the ten scenarios, real Qwen, real validator; reads/writes no database)
cd ../backend
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:1.7b ../.venv/bin/python scripts/judge_probe.py --suite --runs 2
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b   ../.venv/bin/python scripts/judge_probe.py --suite --runs 2
SG_LLM_PROVIDER=ollama SG_LLM_MODEL_IMPACT=qwen3:4b SG_KB_AI_CONTEXT_TOKENS=3000 SG_KB_AI_JUDGE_MAX_TOKENS=6144 SG_KB_AI_JUDGE_NUM_CTX=12288 ../.venv/bin/python scripts/judge_probe.py --suite

# 5. through the application (staff console): start the stack as usual, sign in as staff, "AI-assisted change", e.g.
#    "Santhosh sir now also holds an MTech in AI"  /  "Remove the age restriction from the eligibility criteria"  /  "change online classes to hybrid classes"
#    -> review the Added / Removed / Rewritten hunks and the "Check:" flags, accept, submit; as admin approve and publish; read the follow-up check.
```

## 10. Known limitations (honest)

* A small model can produce a wrong revision. The validator drops or flags it rather than applying it; the cost is a missed suggestion, not a wrong publication. Qwen3 1.7B will miss more than 4B; the ten-scenario probe tells you by how much on your machine.
* Historical protection is word-based (`previously`, `until`, `was`, dated years): an ordinary sentence containing "was" is treated as history and protected.
* A request to *correct* the historical record itself is deliberately refused by the guard.
* A non-value change cannot be proven complete after publication (see §6); the check is advisory.
* A section containing a ``` code fence cannot be edited by the model (the answer format would be ambiguous); it is reported as unavailable.
* A request that is both vague and value-less is answered with a question, not a guess.
