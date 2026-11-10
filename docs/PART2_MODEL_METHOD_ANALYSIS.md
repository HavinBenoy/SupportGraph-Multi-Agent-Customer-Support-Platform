# Why your terminal test worked and the application did not — analysis and decision

Input: your `ollama run qwen3:1.7b` session (two prompts) and the screenshots of the application on the same text.

## 1. What your method does (read from the transcript)

| Aspect | Your terminal prompt | The application (before this change) |
|---|---|---|
| Task shape | "In the given sentence make the change {…} **only if it is applicable**; return it unchanged if not." A *conditional edit* of text, which is what instruction-tuned models are trained on | Classification into verdict classes plus a replacement, per numbered passage |
| Output | Free text; the answer in a fenced block | JSON forced by a schema (`with_structured_output`) |
| Thinking | **On** (qwen3 reasoned for about 60 lines) | **Off** (`llm_thinking=False`, chosen for latency) |
| Context | Both sentences together ("Machine Learning faculty is Santhosh sir. He has …") | One passage at a time, without the line above it |
| Who decides | The model alone | The rules decide first. In your screenshot the rule guard said "historical" because of "Previously …" and **the model was never asked**; a model could only downgrade a rule's "affected", never rescue a rule's "no change" |

So you were right: the weak result was architectural, not a lack of model capability. Three of the four differences cost quality (no thinking, schema-forced output, no neighbouring context) and the fourth (rules as gatekeeper) made the model irrelevant for exactly the lines that were hard.

## 2. What the transcript also shows (the risks of copying the method as-is)

1. **Test 1 (Vinu) was correct**: the model refused because the name differs. Good sign for precision.
2. **Test 2 reached the right answer only after long, wavering reasoning** (several "but maybe the 3 years are past experience" loops). A 1.7B model's decision on a borderline sentence is not stable; another run can flip. A single positive example is not evidence of reliability.
3. **It returned the whole sentence rewritten.** For a section or a document that is how silent damage happens (a heading reworded, a table bar dropped, a neighbouring sentence "improved"). The output must be compared with the original and only a value-sized difference accepted.
4. **It was tested on 2 sentences.** A knowledge base has thousands of lines: the model cannot read everything, so something still has to choose what to show it (retrieval and evidence scan stay).
5. **Latency**: thinking costs hundreds of tokens per answer. On CPU that is tens of seconds per call, acceptable in the background analysis job, not in a chat turn. (This is an estimate; I could not measure Ollama here.)

## 3. Decision

Keep the deterministic retrieval, unit scan, aliases and guards (they find candidates across thousands of lines in milliseconds and never hallucinate), and add **your method as the judging stage**, with a verifier around it:

1. **Section window** around the candidate lines (a short document is given whole; a table row comes with its header; never across a heading; at most `SG_KB_AI_JUDGE_WINDOW_CHARS`, default 1600 characters).
2. **Your prompt**, nearly verbatim ("make the change {…} only where it is applicable … return the text … within a ```text block"), thinking ON for this role, free-text answer.
3. **Line-by-line verification** (`impact/judge.py`): the answer is aligned with the window; a changed line is accepted only if every changed character belongs to the new value (and, when the old value is known, replaces the old value). Rewrites, deleted/added lines, changed headings or tables are ignored and reported.
4. **Combination with the rules** (no single party decides):
   * rules sure + model agrees: definite, higher confidence;
   * rules sure + model says "not applicable": **potential** (a question to the reviewer, with both views), never a silent drop;
   * rules say "unrelated / no link to the subject" + model says it applies (verified): **potential** — this is the rescue path your screenshot needed;
   * a line only the model found: potential;
   * the model alone never makes a line definite; historical/dated lines stay protected by the deterministic guard; a model that fails or answers invalidly leaves the rules' result, flagged;
   * nothing is published without a person.
5. **Cost control**: sections where every candidate line is rule-certain are not sent (`SG_KB_AI_JUDGE=uncertain`, default); `all` asks about every section; `off` restores the previous behaviour. At most `SG_KB_AI_JUDGE_MAX_CALLS` (16) calls per analysis, identical windows answered from a cache.

Which model: qwen3:1.7b with thinking is what you tested and is the default. qwen3:4b is the better choice if you accept slower analysis (`SG_LLM_MODEL_IMPACT=qwen3:4b`); the verifier makes either safe. External APIs: not needed, not integrated; only a future option with your explicit approval and never with student/staff data.

## 4. How to check this on your machine (I could not run Ollama)

```
cd backend
../.venv/bin/python scripts/judge_probe.py -d "change the work experience of santhosh sir from 3 years to 5 years" \
   --text $'Machine Learning faculty is Santhosh sir\nHe has a work experience of 3 years. Previously he worked in IT.'
SG_KB_AI_JUDGE_THINKING=false ../.venv/bin/python scripts/judge_probe.py ...     # compare without thinking
SG_LLM_MODEL_IMPACT=qwen3:4b ../.venv/bin/python scripts/judge_probe.py ...      # compare models
```
It prints the prompt, the raw answer, the extracted block, and for each line whether the change would be ACCEPTED or REJECTED and why, plus the seconds. Then run the same request in the app with `SG_KB_AI_JUDGE=all` to see the model's opinion on every section, and `uncertain` for the normal, faster mode.

If `qwen3:1.7b` times out with thinking on, raise `SG_KB_AI_JUDGE_TIMEOUT_S` (default 300) or `SG_KB_AI_JUDGE_MAX_TOKENS` (default 2500; the thinking counts, and a cut-off answer is treated as "no usable answer", never as an edit).
