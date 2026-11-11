# M6 add-on (2026-11-11): the two new AI failures, staff/admin chat, master-detail change requests, hero typography, motion

## Case A: "change the gen ai course fees to 20000 rupees and machine learning course fees to 12000 rupees"

**What happened.** The processing log showed one "Understanding value change", not two intents. So the Machine Learning section was validated as "replace 20000", and its correct 9999 → 12000 edit was rejected. The retry then failed because the feedback itself was wrong.

**Root causes**
1. The intent splitter only split on " and " when the next word was "that/the/for/…". "… and machine learning course fees to 12000" did not split.
2. Unsplit, `_pair_values` read the two amounts as old → new (20000 → 12000). That old value then became the validator's constraint for every section.

**Fixes**
- **The splitter cuts at every clause boundary.** Clauses are separated by "and", commas, ";", full stops, "also" and "while". A piece that states no value, target or action of its own is glued back to the next piece. So "Python and Java fees are now 9000" stays one intent, and a bare value list ("…now 9000, 8000 and 7000") stays together.
- **Pieces borrow what they leave out.** A piece without a verb borrows the first clause's verb: "change … to 12000". A piece starting "that for …" borrows the first clause's attribute.
- **Two "to/is <amount>" are never old → new.** They are two new values.
- **Every intent is its own spec and judged only against itself.**
  - Each has its own subject, attribute, old value (usually unknown) and new value.
  - Each is discovered, evaluated, validated and traced on its own; the validator sees only that intent's values.
  - Each is verified on its own after publication.
- **A failed intent never blocks the others.** If a section fails for one intent, the other intent's documents are still proposed. Each document is its own reviewable item.
- **No old value is needed.** The slot scan finds the current value ("9999 rupees"), or the model reads the section.

## Case B: "Change the work experience of santhosh sir to Mechanical industry"

**What happened.** Subject words matched 0% and the semantic score was 0.59, against 0.60 needed, so Data-Science never reached the model.

**Root causes**
1. The language-model parse named the NEW value ("mechanical industry") as the subject. "santhosh" and "work experience" were lost, so the lexical signal was 0%.
2. The semantic-only threshold of 0.60 was too high for nomic-embed-text with short paragraphs.

**Fixes**
- **The description's own subject words are always searched.** `build_spec` adds them whatever the model parse returned, and the new value's words are removed from the subject.
- **Lower, configurable semantic threshold.** `SG_KB_AI_SEMANTIC_ADMIT` defaults to **0.45** and is an independent path: no subject word is needed.
- **One exception for sibling subjects.** A document lacking the subject's distinguishing title word (a Java page for a Python change) needs `SG_KB_AI_SEMANTIC_ADMIT_UNKEYED` (0.70) to be admitted on meaning alone.
- **The model may correct work history.** Requests about work experience, career, background or employment now count as speaking about the past ("He previously worked in IT"). The model may correct that sentence; the change is flagged "explicitly corrects it: please confirm".
- **Value changes keep the stricter rule.** They still need an explicit correction cue ("actually", "should be", "instead") before a dated sentence can change.

## Staff and administrators in the chat

**Backend (real RBAC change, `app/api/routes/chat.py`).** Staff and admin tokens are now accepted by `/api/chat` and `/api/chat/{ticket}`, as a visitor of the token's tenant:
- they have no student identity, so no student's records are reachable;
- the tenant in the request body is ignored;
- a student's ticket stays inaccessible to them (404).

Everything else is unchanged: students keep their own scope, and staff/admin APIs are unchanged.

**Frontend**
- The chat accepts staff and admins and says "you see exactly what a visitor sees".
- The app bar shows Chat to everyone. It shows Staff console to staff only and Administration to admins only.
- `/agent` sends an administrator to `/admin`. The server still lets an admin call staff APIs that admin workflows use.

## Change requests: master-detail

- **Layout.** Both the admin "Change requests" and the staff "My change requests" show the list on the left and the selected request on the right.
- **Selection.** It is highlighted, marked with `aria-current`, and kept in the URL (`?cr=`). Switching requests swaps the panel with a short slide; the list, its scroll position and the status filter stay.
- **Phones and tablets (≤ 900 px).** The detail opens on its own with "← Back to the list", and the browser's Back button works too.
- **Information.** Each list row shows the status, id, time, author, document count and accepted/edited/discarded counts. The detail keeps the review actions, step indicator, documents and audit.

## Hero heading "s"

**Cause.** "answers" is italic Instrument Serif painted with `background-clip: text`. The italic final "s" extends past the right edge of its box, and the gradient is only painted inside the box, so the overhang was cut off and the glyph looked shifted.

**Fix.** Padding on the `em` gives the overhang room, and an equal negative margin keeps spacing, alignment and line breaks unchanged. Measured with Playwright:
- room for the overhang is 9.6 px (desktop), 6.9 px (tablet) and 5.0 px (mobile);
- the heading stays centred inside the viewport.

## Motion

All motion is CSS only, with no dependency, and uses only transform and opacity, which is cheap on a CPU:
- the page fades in;
- the hero rises line by line, then the lead text, then the buttons;
- landing sections reveal on scroll;
- buttons lift slightly on hover;
- the mobile menu and dialogs open and close smoothly;
- loading text "breathes";
- Knowledge Base views and change-request details slide in.

Everything is switched off by `prefers-reduced-motion`; Playwright measured an animation duration of `1e-05s` with reduced motion on.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SG_KB_AI_SEMANTIC_ADMIT` | 0.45 (was 0.6) | semantic similarity that alone sends a document to the model |
| `SG_KB_AI_SEMANTIC_ADMIT_UNKEYED` | 0.70 | the same, for a document lacking the subject's distinguishing word |

## Known limitations

- A "respectively" list ("python, java and testing fees are now 9000, 8000 and 7000") stays one intent.
- Semantic thresholds depend on the embedding model. Tune them with the processing log, which shows every score.
- Admins are no longer routed to the staff console. If an admin needs to claim an escalation, the API still allows it, but the UI does not offer it.
