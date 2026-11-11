# Part 3: landing page, application-wide redesign, Knowledge Base navigation and folder management

## Scope (how it was derived)

`docs/ROADMAP.md` defined Part 3 as **"Public landing page and application-wide redesign"**; `docs/PART1.md` recorded two gaps left for later ("folders cannot be moved or deleted yet") and the Knowledge Workspace CSS said "the rest of the application keeps its current look until Part 3". Your UI/UX specification (navigation, folder management, removal of the Publishing tab, design system, landing page) fits inside that scope. Nothing outside it was added. There is no new knowledge representation and no new dependency, and the only backend additions are the two folder endpoints below.

## What was built

| Area | Implementation |
|---|---|
| Design system | `frontend/src/app/globals.css` rewritten around tokens, using the Part 1 workspace language for every page: near-black surfaces, violet action colour, cyan focus/info, emerald success, Inter for UI text, Instrument Serif for page titles. It includes buttons (primary / default / ghost / danger / small / lg), inputs, segmented tabs, cards, rows, pills and status colours, banners, dialogs, page headers, the chat, analytics, voice and transcript styles. Contrast: body text about 15:1, muted text at least 7:1. Every existing class name is kept, so no business component had to change to get the new look. |
| App bar | `Shell.tsx`: brand mark, role-aware navigation (Chat, Voice, Staff console, Administration), account, a skip link, a menu on narrow screens and `aria-current` on the active section. |
| Landing page | `/` is now `Landing.tsx`: hero, how-it-works flow, capabilities, call to action. Motion is CSS-only (a drifting glow, a pulse, reveal-on-scroll through one `IntersectionObserver`). Everything is shown immediately under `prefers-reduced-motion` or without the observer. The student chat moved to `/chat` (students land there after signing in). |
| Login | A two-panel sign-in (what staff/admins get; students need no account). |
| Page headers | Staff console and Administration have an eyebrow and a serif title. Their tabs live in the URL (`?tab=`, `?view=`). |
| KB navigation | `lib/nav.ts` (`useUrlParam`, `setUrlParams`) stores the workspace view in the URL: `?kb=doc|edit|ai|mine|admin`, `f=` folder, `d=` document, `r=` request. Browser Back/Forward move between folders, documents, the editor, AI changes and My change requests, and a reload or shared link reopens the same view. Every view has a **Back** button next to its breadcrumbs, and the breadcrumbs work as before. |
| Folder management | Toolbar on every folder (admins): **New document here**, **New folder** (top level) / **New sub-folder**, **Rename**, **Move** (the destination list excludes the folder itself and all its sub-folders), **Delete** (confirmation dialog; disabled with an explanation while the folder holds folders or documents). Backend: `PUT /api/admin/knowledge/folders/{id}/parent` and `DELETE /api/admin/knowledge/folders/{id}?expected_row_version=`, both admin-only, tenant-scoped, compare-and-set and audited (`folder_moved`, `folder_deleted`). Cycles, depth over 6, name clashes at the destination, stale versions and non-empty deletes are refused. |
| Publishing tab removed | The Administration › Knowledge "Publishing" tab is gone. What it alone offered was kept and moved where it belongs: **New document here** (creates the draft and places it in the current folder), **Drafts & document administration** in the workspace sidebar (versions, process, publish, restore, unpublish, archive, audit, call-feedback proposals), and **Manage versions** on a document. The M6 publication APIs and the integrated change-request publication are unchanged. |

## Accessibility and responsiveness

- Keyboard: skip link, visible focus ring, `role="tablist"`/`tab`, `alertdialog` with `aria-modal` and auto-focus on Cancel.
- Every icon is paired with text.
- At phone width the app bar collapses and the workspace sidebar becomes the existing "Folders" drawer. Playwright measured no horizontal overflow at 390 px.

## Verified

- Frontend: `tsc` clean, `vitest` (all, including the new Part 3, folder move/delete, URL Back and new-document tests), `next build` (routes `/`, `/chat`, `/login`, `/agent`, `/admin`, `/voice`).
- Screenshots: taken with Playwright against the production build, with the API mocked (no backend).
- Backend: `tests/knowledge/test_folder_management.py`.

## Limitations

- Fonts come from Google Fonts. Offline, the system font stack is used (the look degrades gracefully).
- Deleting a folder needs it empty. There is no recursive delete or recycle bin, so nothing can be lost by accident.
- Documents still move one at a time (from the document page).
- Analytics, Live calls and the voice page received the new design system and header but no layout rework. Their behaviour is unchanged.

## Screenshots (Playwright, production build, API mocked, reduced motion)
`docs/screenshots/`: `landing-desktop.png`, `landing-mobile.png`, `login-desktop.png`, `chat-desktop.png`, `kb-folder-desktop.png` (folder toolbar, Back, breadcrumbs), `kb-doc-desktop.png`, `kb-doc-mobile.png`. These were captured before one fix: on desktop the "Menu" toggle showed in the app bar (a CSS specificity bug). It is fixed in this delivery (`.top .menu-toggle`), so the screenshots still show it. Browser Back from a document returned to its folder ("Data-Science"), and Playwright found no horizontal overflow at 390 px. The only console errors were the blocked Google Fonts requests of the sandbox.
