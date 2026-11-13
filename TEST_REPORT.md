# Test report: M7 completion, frontend refinements 2.1–2.4, M8 telephone line (2026-11-12)

Every result below was run **in Claude's sandbox** on the delivered tree. **None of it ran on your machine.**

**Sandbox environment:**
- Python 3.13;
- a throwaway PostgreSQL 16 started inside the sandbox (not your database);
- Node with Next.js 15;
- the offline hashing embedder, scripted or rule-based models, and fake STT/TTS/VAD.

**Not used:** Ollama, Edesy, a telephone number, or any paid service.

## 1. Commands and results (sandbox)
| # | Command (from the project root unless noted) | Result |
|---|---|---|
| 1 | `cd backend && ../.venv/bin/python -m pytest -q -p no:cacheprovider -n 4 tests/knowledge tests/rag` | **751 passed, 3 skipped** (6 min 22 s) |
| 2 | `cd backend && ../.venv/bin/python -m pytest -q -p no:cacheprovider -n 4 tests --ignore=tests/knowledge --ignore=tests/rag` | **1406 passed, 83 skipped** (2 min 33 s) |
| 3 | `cd backend && SG_TEST_DATABASE_URL=postgresql+psycopg://supportgraph:supportgraph@localhost:5432/postgres ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/db` | **91 passed, 0 skipped** (PostgreSQL 16) |
| 4 | `make test-phone` (`tests/telephony`), run 3 times in a row to look for flakiness | 41 passed each time (a 42nd unit test was added afterwards; it ran in run 2) |
| 5 | `make eval-impact` | 18 scenarios · precision 1.000 · recall 1.000 · exact replacements 16/16 · wrong edits 0 (unchanged) |
| 6 | `cd frontend && npx tsc --noEmit -p .` | clean (0 errors) |
| 7 | `cd frontend && npx vitest run` | **20 files, 296 passed** |
| 8 | `cd frontend && npm run build` | built; 0 warnings |
| 9 | Playwright on the production build (`next start`, API mocked) at 1440, 1180 and 390 px | Details below |
| 10 | **Integration:** real `uvicorn` + a scratch PostgreSQL database (migrated to 0014, seeded) + `scripts/phone_sim.py` at 8 kHz | Details below |

**Run 1 came before the last small fix.** It ran before the `SG_TELEPHONY_SAMPLE_RATE` parsing fix in `app/config.py`, which the knowledge and RAG code does not use.
Runs 2, 3, 7 and 8 ran on the final tree.

**Run 9, Playwright:**
- **2.1 split pane, on desktop and laptop:**
  - dragging changed the width from 380 to 638 px;
  - `user-select` was `none` during the drag and `auto` after it;
  - End gave the maximum (724) and Home the minimum (280); → gave 296;
  - the selected row stayed in view, and the width survived a reload (sessionStorage).
- **2.1 at 390 px:** the divider is hidden, so the existing one-pane layout is used.
- **2.4:** the document list and details are side by side on desktop and laptop, and the details are in view after selecting. At 390 px they are stacked, and the
  details are scrolled into view.
- **No horizontal overflow** (`scrollWidth == clientWidth`) on the requests, documents and Telephone line pages, at every width. There were no page errors.
- The screenshots are in `docs/screenshots/m8-*.png`.

**Run 10, the simulator against a real API process.** The simulated call:
- dialled from a student's number;
- heard the greeting;
- sent a spoken question and got an answer;
- typed `*482913#`.

The call row ended as `channel=phone, caller_status=verified, caller_role=student, caller_masked=+91 ******2345`, with the events
`created → caller_identified{claimed} → connected → caller_verified{student} → participant_disconnected → ended`. The scratch database was dropped afterwards.
This run found a real bug: the sample rate from the environment was rejected. It was fixed and covered by a unit test.

**Why tests were skipped.**
- **Run 2, 83 skips** (counted with `-rs`):
  - 81 need `SG_TEST_DATABASE_URL`: these are the PostgreSQL tests, and they ran in run 3;
  - 2 need `SG_TEST_OLLAMA=1`, a real Ollama.
- **Run 1, 3 skips:** the optional real-model checks.

**Not executed:**
- any call to Edesy or a real telephone line;
- real Ollama/Qwen, real speech models, Langfuse;
- anything on your machine.

## 2. New tests in this delivery
| File | Tests | Covers |
|---|---|---|
| `backend/tests/telephony/test_m8_phone.py` | 14 | Details below. |
| `backend/tests/telephony/test_m8_api.py` | 6 | Details below. |
| `backend/tests/telephony/test_m8_units.py` | 18 | Normalisation (11 formats), masking and the keyed hash, PIN rules, Edesy start-frame and event parsing (bad JSON, rates, DTMF injection), resampling, the bridge (framing, padding, marks, flush on barge-in and takeover, hang-up, nothing after it, browser-only messages never sent down the line), and the env sample-rate check with off-by-default. |
| `backend/tests/telephony/test_m8_scripts.py` | 5 | The Edesy setup script builds the documented requests; the key is not printed; `ws://`, wrong paths and short tokens are refused before any request; no key means no request. No network. |
| `backend/tests/db/test_postgres_m8.py` | 3 | PostgreSQL. 0014 up/down/up with existing calls; 8 racing wrong PINs never exceed the limit, the lock is set once, and the right PIN is refused while locked; the summary query. |
| `backend/tests/db/test_migrations_m6.py` (+1, 1 updated) | | 0014 on SQLite: an existing call reads as `web`; the new tables appear; downgrade removes exactly them. 0014 is the head. |
| `backend/tests/knowledge/test_manual_revision.py` | 4 | Details below. |
| `frontend/src/components/workspace/Splitter.test.tsx` | 6 | The separator role and values; keyboard, with clamping at min and max and reset; session memory and restore; pointer drag with the selection lock; the detail stays editable; blocked storage; the clamp. |
| `frontend/src/components/workspace/Revision.test.tsx` | 6 | Details below. |
| `frontend/src/components/KnowledgeAdminRefinements.test.tsx` | 8 | Details below. |
| `frontend/src/components/Telephony.test.tsx` | 6 | Details below. |

**`test_m8_phone.py` covers:**
- the endpoint refusing when the line is off, with a bad token, with a short token, and when the IP is not allowed;
- bad metadata and an unsupported rate;
- the status never revealing the token, and its RBAC;
- the greeting and an answer at 16 and 8 kHz, in 20 ms frames;
- the call appearing in Live Calls with its masked number;
- a matching number staying anonymous until the PIN is typed;
- the wrong PIN, then the right one, switching the identity;
- lockout across calls;
- a shared number refusing verification;
- a staff number giving no student records;
- a takeover flushing queued audio (`killAudio`), the staff voice reaching the 8 kHz line, and the end sending `disconnect`;
- trial disclosure, keypad consent 1 / 3 and its events.

**`test_m8_api.py` covers:**
- RBAC on every admin route (401 and 403);
- another tenant getting 404;
- number normalise, mask and audit, with no digits in the audit;
- a student's number refused here;
- unknown fields refused;
- the PIN needing the current password;
- weak PINs refused;
- only a hash stored;
- the password form's rate limit;
- the summary scoped to the tenant.

**`test_manual_revision.py` covers:**
- a manual full-text revision of a returned AI request: attributed, `revised_after_return`, the AI proposal kept, nothing published or versioned, resubmitted to the
  same request, an audit without text;
- the manual-request path with its concurrency token (409 on a stale one);
- only the author, and only while returned (IDOR, admin and other tenant, validation);
- choosing the AI option clearing the manual attribution.

**`Revision.test.tsx` covers:**
- the two distinct options and the source labels;
- option A resuming the AI analysis, and converting a manual request first;
- option B starting from the returned text, comparing original / AI / manual, sending a manual decision with `expected_row_version`, labelling it "revised after
  return", resubmitting, with no publish and no admin call;
- the manual-request conflict being shown;
- no panel once the request is resubmitted.

**`KnowledgeAdminRefinements.test.tsx` covers:**
- 2.3: the form hidden by default; Cancel or Escape creating nothing and leaving nothing behind; a double click sending one request; an error keeping the form open;
  success closing it, refreshing the list and opening the new draft; `?new=1`.
- 2.4: the empty state, the highlight, a reselect, and Back; scrolling into view with `behavior: "auto"` under reduced motion; focus.
- M7: the editor prefills from vN, previews, and saves with `row_version`; the publish bar offers only the next lifecycle step.

**`Telephony.test.tsx` covers:**
- the line off and paid, and the summary;
- a staff number set; a student number not offered;
- a PIN reset only after confirming;
- the admin tab;
- the own-PIN form needing the password, digits only, never keeping the PIN on screen;
- Live Calls wording a match as a claim.

**Existing tests that changed.**
- `test_0013_is_the_head…` became `test_0014_is_the_head…`, because the migration head moved.
- The SQLite migration fixtures (`at_0006`, `at_0008`) now also drop the M8 tables and columns, because they rebuild pre-0014 revisions from today's models.
- The KnowledgeAdmin create test presses *New document* first.

No test was removed, skipped or weakened.

## 3. Run these on your machine
```bash
unzip -o supportgraph-m8.zip               # from the project root; code only (no .env, data or build output)
make db-migrate                            # upgrades your database to 0014 (additive; back up first if you like: pg_dump)
cd backend
../.venv/bin/python -m pytest -q -p no:cacheprovider tests/knowledge tests/rag
../.venv/bin/python -m pytest -q -p no:cacheprovider tests --ignore=tests/knowledge --ignore=tests/rag
SG_TEST_DATABASE_URL=postgresql+psycopg://<user>:<pw>@localhost:5432/<a scratch database> ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/db
cd ../frontend && npx tsc --noEmit -p . && npx vitest run && npm run build
cd .. && make eval-impact && make test-phone
```
`SG_TEST_DATABASE_URL` must point to a **scratch** database. The PostgreSQL tests create and drop their own databases, so never point them at your real data.

## 4. Manual checks (your machine)
1. **2.1.** As admin, open Knowledge → Change requests and select a request:
   - Drag the divider; try ←/→/Home/End on it; reload, and the width is kept.
   - Narrow the window below 900 px: one pane at a time, as before.
   - Repeat in Staff console → *My change requests*.
2. **2.2.** As admin, return a staff request for revision. As that staff member, open it in *My change requests*:
   - **B · Edit manually:** change, add and remove text, *Save revision*, check the comparison and the "Manual · revised after return" label, then
     *Resubmit for review*. As admin you see the same label. Nothing is published until you approve and publish.
   - Repeat with **A**.
3. **2.3 / 2.4.** In Drafts & document administration:
   - *New document* opens the form; Cancel creates nothing.
   - Select documents: the details open beside the list (or under it in a narrow window).
4. **M8, free.** Put `SG_TELEPHONY_PROVIDER=edesy` and a `SG_TELEPHONY_STREAM_TOKEN` of 32+ random characters in your `.env`, then restart `make api`.
   1. As admin, give a student a phone number through their student record. As that student, set a PIN under Chat → *Phone support PIN*.
   2. Run `make phone-sim ARGS='--from "<that number>" --wav <a question>.wav --keys "*<pin>#" --out reply.wav'`.
   3. In Staff console → Live Calls the call shows "Phone · not verified" and then "verified". Take it over and speak: your voice is in `reply.wav`.
   4. Admin → *Telephone line* shows the counts.
   5. With real speech models (`SG_VOICE_PROVIDER=real`), use a real recorded question.
5. **M8, paid (only after you approve).** Follow docs/M8.md §7 (`make edesy-stream-test`, `make edesy-attach`) and place a real call.

## 5. Assumptions and known issues
- **M8 scope.** It was taken from ROADMAP ("Edesy adapter (approval needed)") and master spec §5–8. Closure (M9) and answering on a staff member's own mobile phone
  are not included (docs/M8.md §1).
- **Edesy was not contacted.** The adapter follows Edesy's published protocol (checked 2026-11-12) and was tested against a fake stream and the simulator. Edesy does
  not document how fast `killAudio` takes effect or how deep its playback buffer is; check both on the first real call.
- **Phone PIN limits.** A spoofed number can lock that student's *phone* verification for 15 minutes (an administrator can reset it), and a typed PIN can be
  overheard. Stronger options (SMS OTP) are paid and need approval.
- **Transcript after verification.** It continues in the verified student's conversation; the anonymous part before verification stays in the anonymous record.
- **No project ZIP was attached.** Apply this delta over `supportgraph-m6r-m7.zip`.
