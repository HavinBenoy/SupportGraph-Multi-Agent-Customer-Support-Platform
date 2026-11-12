import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AiChange } from "./AiChange";
import { ChangeRequests } from "./AdminRequests";
import { PatchView, baseText } from "./PatchView";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { KwItem, KwRequest } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const BASE = "# Fees\nPython Full Stack fee is INR 45000.\nJava Full Stack fee is INR 50000.\n";
const LINE = "Python Full Stack fee is INR 45000.";
const START = BASE.indexOf(LINE);
const EDIT = { start: START, end: START + LINE.length, original: LINE, replacement: "Python Full Stack fee is INR 48000.", heading: "Fees", verdict: "change", confidence: 0.9, reason: "fee" };
const diffOf = (body = BASE) => ({ title: [{ op: "equal" as const, text: "Fees" }], body: [{ op: "equal" as const, text: body }] });

const item = (o: Partial<KwItem> = {}): KwItem => ({
  doc_id: "python-fees", doc_title: "Python fees", base_version: 1, current_published_version: 1, base_stale: false, proposed_title: "Python fees", stats: { added_chars: 0, deleted_chars: 0, changed_blocks: 0 },
  origin: "ai", decision: null, rationale: null, row_version: 0, result_version: null, diff: diffOf(), proposed_content: BASE, classification: "definite", confidence: 0.92,
  included: false, actionable: true, ai: { reason: "States the old fee.", signals: null, uncertainty: null, flags: null, path: "Fees/Courses", passages: null, edits: [EDIT] }, final_edits: null, ...o,
});
const req = (o: Partial<KwRequest> = {}): KwRequest => ({
  request_id: "cr_ai1", status: "awaiting_decisions", source: "ai_assisted", description: "Python fee is now 48000", row_version: 0, created_by: "staff1", created_at: null, updated_at: null,
  submitted_by: null, submitted_at: null, reviewed_by: null, reviewed_at: null, review_note: null, applied_by: null, applied_at: null, document_count: 1,
  documents: [{ doc_id: "python-fees", title: "Python fees" }], added_chars: 0, deleted_chars: 0, items: [item()],
  counts: { accepted: 0, edited: 0, discarded: 0, pending: 1, manual_items: 0, related: 0, included: 0, excluded_by_admin: 0 },
  analysis: { stage: null, done: null, total: null, error: null, model: "rules", attempt: 1, started_at: null, completed_at: null, questions: [], answers: {},
    summary: { discovered: 2, definite: 1, potential: 0, related_no_change: 1, not_relevant: [], flags: { unsupported: 0, historical: 0, scope: 0 } }, spec: null }, ...o,
});
/** Documents are listed collapsed (final M6): open one to see its comparison and decisions. */
const expand = async (name: string) => {
  const card = await screen.findByRole("article", { name }, { timeout: 5000 });
  const head = within(card).getAllByRole("button")[0];
  if (head.getAttribute("aria-expanded") !== "true") await userEvent.click(head);
  return screen.getByRole("article", { name });
};
const decided = (d: "accepted" | "manual" | "discarded", extra: Partial<KwItem> = {}) => item({ decision: d, row_version: 1, included: d !== "discarded", final_edits: d === "discarded" ? [] : [EDIT], ...extra });
const body = (f: ReturnType<typeof mockApi>, method: string, path: string, n = 0) => JSON.parse(String((f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path)[n][1] as RequestInit).body));
const count = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path).length;

describe("PatchView", () => {
  it("shows the existing passage and the replacement as tagged text, never as HTML", () => {
    const { container } = render(<PatchView base={BASE} edits={[{ ...EDIT, replacement: "<img src=x onerror=alert(1)>" }]} label="Proposed change" />);
    expect(container.querySelector("img")).toBeNull();
    const region = screen.getByRole("region", { name: "Proposed change" });
    expect(within(region).getByText(LINE).closest("mark")).toHaveClass("kw-old");
    expect(region).toHaveTextContent("Existing passage the AI says needs to change");
    expect(region).toHaveTextContent("AI replacement: <img src=x onerror=alert(1)>");
    expect(container.querySelector("mark.kw-new")).not.toBeNull();
  });
  it("says so instead of guessing when the patch no longer matches the text", () => {
    render(<PatchView base="something else entirely" edits={[EDIT]} label="x" />);
    expect(screen.getByText(/no longer lines up/)).toBeInTheDocument();
  });
  it("rebuilds the original text from a structured diff", () => {
    expect(baseText([{ op: "equal", text: "a " }, { op: "delete", text: "b" }, { op: "insert", text: "c" }])).toBe("a b");
  });
});

describe("AI-assisted change (staff)", () => {
  const mount = (initial?: Parameters<typeof AiChange>[0]["initial"], onSubmitted = vi.fn()) => { signedIn("staff", "staff1"); renderWithAuth(<AiChange initial={initial} onClose={vi.fn()} onSubmitted={onSubmitted} />); return onSubmitted; };

  it("analyses a described change, shows progress, then the candidate with explanation, confidence and the orange/blue patch", async () => {
    let polls = 0;
    const f = mockApi({
      "POST /api/staff/knowledge/ai-changes": () => ({ status: 201, json: req({ status: "draft", items: [], analysis: null }) }),
      "POST /api/staff/knowledge/changes/cr_ai1/analyse": () => ({ status: 202, json: { attempt: 1, request: req({ status: "analysing", items: [], analysis: { ...req().analysis!, stage: "evaluating", done: 1, total: 4, summary: null } }) } }),
      "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: ++polls < 2 ? req({ status: "analysing", items: [], analysis: { ...req().analysis!, stage: "evaluating", done: 2, total: 4, summary: null } }) : req() }),
    });
    mount();
    await userEvent.type(screen.getByLabelText("What is changing?"), "Python fee is now 48000");
    await userEvent.click(screen.getByRole("button", { name: "Analyse" }));
    const bar = await screen.findByRole("progressbar", { name: "Analysis progress" });
    expect(bar).toHaveAttribute("aria-valuemax", "4");
    const card = await expand("Suggestion for Python fees");
    expect(within(card).getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(card).toHaveTextContent("States the old fee.");
    expect(card).toHaveTextContent("confidence 92%");
    expect(card).toHaveTextContent("Fees/Courses");
    expect(card).toHaveTextContent("Definitely affected");
    expect(within(card).getByText(LINE).closest("mark")).toHaveClass("kw-old");
    expect(body(f, "POST", "/api/staff/knowledge/ai-changes")).toEqual({ description: "Python fee is now 48000" });
  }, 15000);

  it("shows, per passage, how it was judged: class, evidence and what the language model said", async () => {
    const base = item();
    const withPassages = item({ ai: { ...base.ai!, passages: [
      { n: 0, start: 0, end: 5, text: "His total work experience is 3 years.", heading: "Faculty", verdict: "potential", confidence: 0.5, reason: "The rules saw no link to the subject.", replacement: "His total work experience is 5 years.",
        kind: "potential", evidence: "the old value is written here; subject only in the context", judge: "agrees" },
      { n: 1, start: 6, end: 9, text: "Until 2023 he had 3 years.", heading: "Faculty", verdict: "no_change", confidence: 0.8, reason: "Historical or explicitly dated record: left unchanged.", replacement: null, kind: "historical" }] } });
    mockApi({ "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req({ items: [withPassages] }) }) });
    mount({ requestId: "cr_ai1" });
    const card = await expand("Suggestion for Python fees");
    expect(card).toHaveTextContent("How each passage was judged (2)");
    expect(card).toHaveTextContent("Potentially affected");
    expect(card).toHaveTextContent("Historical — keep");
    expect(card).toHaveTextContent("Evidence: the old value is written here; subject only in the context");
    expect(card).toHaveTextContent("The language model read the section and agrees.");
  });

  it("shows an added, a removed and a rewritten change as labelled hunks with the checks the validator raised", async () => {
    const base = item();
    const doc = "# DS\n- Any graduate\n- Age under 30\nSanthosh sir teaches ML.\n";
    const edits = [
      { start: 5, end: 33, original: "- Any graduate\n- Age under 30", replacement: "- Any graduate", heading: "DS", verdict: "potential", confidence: 0.5, reason: "r", kind: "remove" as const },
      { start: 34, end: 58, original: "Santhosh sir teaches ML.", replacement: "Santhosh sir teaches ML.\nHe holds an MTech in AI.", heading: "DS", verdict: "potential", confidence: 0.5, reason: "r", kind: "add" as const, flags: ["it uses names that are in neither the document nor the request (MTech)"] }];
    const generalItem = item({ diff: { header: { old_title: "DS", new_title: "DS" }, body: [{ op: "equal", text: doc }] } as unknown as KwItem["diff"], classification: "potential",
      ai: { ...base.ai!, edits, passages: [{ n: 0, start: 34, end: 58, text: "Santhosh sir teaches ML.", heading: "DS", verdict: "potential", confidence: 0.5, reason: "the language model says the change applies here (add); please confirm.",
        replacement: "x", kind: "potential", judge: "agrees", hunk: 0, change: { kind: "add", removed: [], added: ["He holds an MTech in AI."], flags: ["it uses names that are in neither the document nor the request (MTech)"], notes: [] } }] } });
    mockApi({ "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req({ items: [generalItem] }) }) });
    mount({ requestId: "cr_ai1" });
    const card = await expand("Suggestion for Python fees");
    expect(card).toHaveTextContent("Removed");
    expect(card).toHaveTextContent("Added");
    expect(card).toHaveTextContent("Proposed: add information — adds 1 line");
    expect(card).toHaveTextContent("Check: it uses names that are in neither the document nor the request (MTech)");
    expect(card).toHaveTextContent("He holds an MTech in AI.");
  });

  it("asks the follow-up question instead of guessing, and sends the answer back", async () => {
    const q = req({ status: "awaiting_clarification", items: [], analysis: { ...req().analysis!, summary: null, questions: [{ id: "q1", text: "Which Data Science batch?", options: ["Weekend batch", "Weekday batch"] }] } });
    const f = mockApi({
      "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: q }),
      "POST /api/staff/knowledge/changes/cr_ai1/analyse": () => ({ status: 202, json: { attempt: 2, request: req({ status: "analysing", items: [] }) } }),
    });
    mount({ requestId: "cr_ai1" });
    const go = await screen.findByRole("button", { name: "Continue analysis" });
    expect(go).toBeDisabled();
    await userEvent.click(screen.getByRole("radio", { name: "Weekend batch" }));
    await userEvent.click(go);
    await waitFor(() => expect(count(f, "POST", "/api/staff/knowledge/changes/cr_ai1/analyse")).toBe(1));
    expect(body(f, "POST", "/api/staff/knowledge/changes/cr_ai1/analyse")).toEqual({ answers: { q1: "Weekend batch" } });
  });

  it("records Accept and Discard with the item's version token; Submit waits for every decision", async () => {
    const st = { items: [item(), item({ doc_id: "brochure", doc_title: "Brochure", classification: "potential", confidence: 0.6 })] };
    const cur = () => req({ items: st.items, counts: { ...req().counts!, pending: st.items.filter((i) => !i.decision).length, included: st.items.filter((i) => i.included).length } });
    const set = (n: KwItem) => { st.items = st.items.map((i) => (i.doc_id === n.doc_id ? n : i)); return { json: n }; };
    const f = mockApi({
      "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: cur() }),
      "POST /api/staff/knowledge/changes/cr_ai1/items/python-fees/decision": () => set(decided("accepted")),
      "POST /api/staff/knowledge/changes/cr_ai1/items/brochure/decision": () => set(decided("discarded", { doc_id: "brochure", doc_title: "Brochure", rationale: "historical" })),
      "POST /api/staff/knowledge/changes/cr_ai1/submit": () => ({ json: req({ status: "submitted" }) }),
    });
    const onSubmitted = mount({ requestId: "cr_ai1" });
    const submit = await screen.findByRole("button", { name: /Submit for administrator review/ });
    expect(submit).toBeDisabled();
    expect(screen.getByText("2 document(s) still need a decision.")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Suggestion for Python fees" })).toHaveTextContent("Action required");
    await userEvent.click(within(await expand("Suggestion for Python fees")).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Suggestion for Python fees" })).getByText(/Accepted — this proposal is part of the request/)).toBeInTheDocument());
    const accepted = screen.getByRole("article", { name: "Suggestion for Python fees" });
    expect(within(accepted).queryByRole("button", { name: "Accept" })).toBeNull();                  // no second Accept
    expect(within(accepted).getByRole("button", { name: "Edit manually" })).toBeInTheDocument();
    expect(within(accepted).getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(body(f, "POST", "/api/staff/knowledge/changes/cr_ai1/items/python-fees/decision")).toEqual({ decision: "accepted", expected_row_version: 0 });
    const c2 = await expand("Suggestion for Brochure");
    expect(screen.getByRole("article", { name: "Suggestion for Python fees" }).querySelector("[aria-expanded=true]")).toBeNull();   // one open at a time
    await userEvent.click(within(c2).getByRole("button", { name: "Discard" }));
    await userEvent.type(within(c2).getByLabelText("Reason (optional)"), "historical");
    await userEvent.click(within(c2).getByRole("button", { name: "Confirm discard" }));
    await waitFor(() => expect(body(f, "POST", "/api/staff/knowledge/changes/cr_ai1/items/brochure/decision")).toEqual({ decision: "discarded", reason: "historical", expected_row_version: 0 }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Submit for administrator review/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /Submit for administrator review/ }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(body(f, "POST", "/api/staff/knowledge/changes/cr_ai1/submit")).toEqual({ description: "Python fee is now 48000" });
  });

  it("Edit manually previews the staff member's own wording and saves it per passage", async () => {
    const f = mockApi({
      "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req() }),
      "POST /api/staff/knowledge/changes/cr_ai1/items/python-fees/decision": () => ({ json: decided("manual") }),
    });
    mount({ requestId: "cr_ai1" });
    const card = await expand("Suggestion for Python fees");
    await userEvent.click(within(card).getByRole("button", { name: "Edit manually" }));
    const ta = within(card).getByLabelText(/Replacement for/);
    await userEvent.clear(ta);
    await userEvent.type(ta, "Fee is INR 49000.");
    expect(within(card).getByRole("region", { name: "Your edit, before saving" })).toHaveTextContent("Fee is INR 49000.");
    await userEvent.click(within(card).getByRole("button", { name: "Save my edit" }));
    await waitFor(() => expect(body(f, "POST", "/api/staff/knowledge/changes/cr_ai1/items/python-fees/decision")).toEqual({ decision: "manual", replacements: [{ index: 0, replacement: "Fee is INR 49000." }], expected_row_version: 0 }));
  });

  it("does not let a stale suggestion be accepted or edited, but still allows discarding", async () => {
    mockApi({ "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req({ items: [item({ base_stale: true, current_published_version: 2 })] }) }) });
    mount({ requestId: "cr_ai1" });
    const card = await expand("Suggestion for Python fees");
    expect(within(card).getByRole("alert")).toHaveTextContent("published again");
    expect(within(card).getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Edit manually" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Discard" })).toBeEnabled();
  });

  it("shows the original, the AI proposal and the manual revision; the third view marks only what the person changed", async () => {
    const manual = BASE.replace("INR 45000", "INR 49000");
    mockApi({ "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req({ items: [decided("manual", { proposed_content: manual })] }) }) });
    mount({ requestId: "cr_ai1" });
    const card = await expand("Suggestion for Python fees");
    const orig = within(card).getByRole("region", { name: "Original text of Python fees" });
    const ai = within(card).getByRole("region", { name: "AI proposal for Python fees" });
    const man = within(card).getByRole("region", { name: /Manual revision of Python fees/ });
    expect(orig).toHaveTextContent("Java Full Stack fee is INR 50000.");                                  // complete context, not a snippet
    expect(within(orig).getByText("45000").tagName).toBe("DEL");
    expect(within(ai).getByText("48000").tagName).toBe("INS");
    expect(man.querySelectorAll("ins")).toHaveLength(1);
    expect(within(man).getByText("49000").tagName).toBe("INS");                                           // AI → manual only
    expect(within(card).getByRole("button", { name: "Use the AI proposal instead" })).toBeInTheDocument();
  });

  it("lists every task of a multi-task request with its status, the decomposition note and the final check", async () => {
    const tasks = [{ intent: 1, text: "Change the Gen AI faculty to Mohanan sir", status: "completed" as const, status_label: "Completed proposal" },
      { intent: 2, text: "Change the Quantum faculty to Ravi sir", status: "no_document" as const, status_label: "No relevant document found" }];
    mockApi({ "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req({ analysis: { ...req().analysis!, intents: tasks, decomposition: { source: "rules", tasks: [], note: "" },
      final_check: ["Task 1 (“Change the Gen AI faculty”): its new value “Mohanan sir” is not in the final proposal for genai — a later task may have overwritten it."] },
      items: [item({ ai: { ...item().ai!, tasks: [{ task: 1, classification: "definite", reason: "faculty line", edits: 1 }] } })] }) }) });
    mount({ requestId: "cr_ai1" });
    const list = await screen.findByRole("region", { name: "Tasks in this request" });
    expect(list).toHaveTextContent("Completed proposal");
    expect(list).toHaveTextContent("No relevant document found");
    expect(within(list).getByRole("alert")).toHaveTextContent("overwritten");
    expect(screen.getByRole("article", { name: "Suggestion for Python fees" })).toHaveTextContent("Task 1: Change the Gen AI faculty to Mohanan sir");
  });

  it("shows an analysis failure with a retry instead of an empty result", async () => {
    mockApi({ "GET /api/staff/knowledge/changes/cr_ai1": () => ({ json: req({ status: "draft", items: [], analysis: { ...req().analysis!, error: "The model did not answer in time.", summary: null } }) }) });
    mount({ requestId: "cr_ai1" });
    expect(await screen.findByRole("alert")).toHaveTextContent("The model did not answer in time.");
    expect(screen.getByRole("button", { name: "Retry analysis" })).toBeInTheDocument();
  });
});

describe("Administrator change requests (integrated approve → process → publish)", () => {
  const submitted = (o: Partial<KwRequest> = {}) => req({
    status: "submitted", description: "Python fee to 48000", submitted_by: "staff1", submitted_at: "2026-11-03T09:00:00Z",
    items: [decided("accepted", { included: true }), decided("discarded", { doc_id: "brochure", doc_title: "Brochure", rationale: "historical brochure" }), decided("manual", { doc_id: "faq", doc_title: "FAQ", included: true })],
    counts: { accepted: 1, edited: 1, discarded: 1, pending: 0, manual_items: 0, related: 0, included: 2, excluded_by_admin: 0 }, document_count: 3, ...o });
  const world = (state: { r: KwRequest }, extra: Record<string, (b: any, u: string) => any> = {}) => mockApi({
    "GET /api/admin/knowledge/changes": () => ({ json: { items: [state.r], total: 1, page: 1, page_size: 50 } }),
    "GET /api/admin/knowledge/changes/cr_ai1": () => ({ json: state.r }),
    "GET /api/admin/knowledge/changes/cr_ai1/audit": () => ({ json: { items: [{ id: 2, at: "2026-11-03T09:01:00Z", actor: "staff1", action: "change_request_submitted", doc_id: null, version: null, summary: {} }, { id: 1, at: "2026-11-03T09:00:00Z", actor: "staff1", action: "change_decision_recorded", doc_id: "brochure", version: null, summary: { decision: "discarded" } }] } }),
    ...extra,
  });
  const mount = () => { signedIn("admin", "admin1"); renderWithAuth(<ChangeRequests />); };

  it("lists id, description, submitter, time, document count and accepted / edited / discarded counts", async () => {
    world({ r: submitted() });
    mount();
    const row = await screen.findByRole("button", { name: /Open request cr_ai1/ });
    expect(row).toHaveTextContent("Python fee to 48000");
    expect(row).toHaveTextContent("by staff1");
    expect(row).toHaveTextContent("2 document(s) · 1 accepted · 1 edited · 1 discarded");
    expect(row).toHaveTextContent("Awaiting review");
  });

  it("opens the details beside the list, marks the selection and keeps it in the URL", async () => {
    world({ r: submitted() });
    mount();
    const row = await screen.findByRole("button", { name: /Open request cr_ai1/ });
    await userEvent.click(row);
    const detail = await screen.findByRole("region", { name: "Request details" });
    expect(screen.getByRole("region", { name: "Change requests" })).toBeInTheDocument();          // the list is still there
    expect(row).toHaveAttribute("aria-current", "true");
    expect(window.location.search).toContain("cr=cr_ai1");
    expect(detail).toHaveTextContent("Python fee to 48000");
    expect(row.closest(".kw-md")).toHaveClass("has-sel");                                       // on phones the list hides and "Back to the list" shows (CSS)
    await userEvent.click(within(detail).getByRole("button", { name: /Back to the list/ }));
    expect(window.location.search).not.toContain("cr=");
  });

  it("shows accepted, edited AND discarded documents with the staff reason, and the audit history", async () => {
    world({ r: submitted() });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    await userEvent.click(await screen.findByRole("tab", { name: /Documents/ }));
    expect(await screen.findByRole("article", { name: "Document Python fees" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Document FAQ" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Document Brochure" })).toHaveTextContent("Discarded by staff: historical brochure");
    expect(await expand("Document Brochure")).toHaveTextContent("Reason: historical brochure");
    await userEvent.click(screen.getByRole("tab", { name: "Audit history" }));
    const log = await screen.findByRole("list", { name: "Audit history" });
    expect(log).toHaveTextContent("Staff decision");
    expect(log).toHaveTextContent("Submitted for review");
  });

  it("names the staff member who edited (never 'you' for someone else's edit) and the administrator who edited the final wording", async () => {
    world({ r: submitted({ created_by: "staff7", items: [
      decided("manual", { doc_id: "faq", doc_title: "FAQ", included: true, staff_edited_by: "staff7", last_edited_by: "staff7", last_edit_role: "author", editors: ["staff7"] }),
      decided("manual", { doc_id: "py", doc_title: "Python fees", included: true, admin_edited: true, staff_edited_by: "staff8", admin_edited_by: "admin1",
        last_edited_by: "admin1", last_edit_role: "admin", last_edited_at: "2026-11-03T10:00:00Z", editors: ["admin1", "staff8"] })] }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    await userEvent.click(await screen.findByRole("tab", { name: /Documents/ }));
    const faq = await screen.findByRole("article", { name: "Document FAQ" });
    expect(faq).toHaveTextContent("Edited by staff7");
    expect(faq).not.toHaveTextContent("Edited by you");
    const py = screen.getByRole("article", { name: "Document Python fees" });
    expect(py).toHaveTextContent("Edited by staff8");                                      // the staff edit stays attributed to its author ...
    expect(py).toHaveTextContent("Final wording edited by you");                          // ... and admin1 (the viewer) made the latest edit
    expect(await expand("Document Python fees")).toHaveTextContent("Requested by staff7 · text proposed by the AI · last edited by you");
  });

  it("without persisted editor metadata it says 'Edited', never guesses 'you'", async () => {
    world({ r: submitted({ items: [decided("manual", { doc_id: "faq", doc_title: "FAQ", included: true })] }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    await userEvent.click(await screen.findByRole("tab", { name: /Documents/ }));
    const faq = await screen.findByRole("article", { name: "Document FAQ" });
    expect(faq).toHaveTextContent("Edited");
    expect(faq).not.toHaveTextContent("by you");
  });

  it("Approve publishes nothing; publication needs an explicit confirmation and only then calls publish", async () => {
    const state = { r: submitted({ status: "under_review" }) };
    const f = world(state, {
      "POST /api/admin/knowledge/changes/cr_ai1/review": () => { state.r = { ...state.r, status: "approved" }; return { json: state.r }; },
      "POST /api/admin/knowledge/changes/cr_ai1/publish": () => { state.r = { ...state.r, status: "processing" }; return { status: 202, json: { outcome: "started", attempt: 1, request: state.r } }; },
    });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Nothing has been published");
    expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/publish")).toBe(0);
    await userEvent.click(await screen.findByRole("button", { name: "Process and Publish Approved Changes" }));
    expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/publish")).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Confirm — process and publish" }));
    await waitFor(() => expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/publish")).toBe(1));
    expect(await screen.findByRole("progressbar", { name: "Release progress" })).toBeInTheDocument();
  });

  it("never reports success until the server says published; then lists the final versions", async () => {
    const processing = submitted({ status: "processing", items: submitted().items!.map((i) => ({ ...i, process_status: i.included ? ("ready" as const) : null, publish_status: i.included ? ("pending" as const) : null })) });
    const state = { r: processing };
    world(state);
    mount();
    await userEvent.click((await screen.findByRole("combobox", { name: "Status" })) && await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    expect(screen.queryByText(/Every document went live together/)).toBeNull();
    state.r = { ...processing, status: "published", published_by: "admin1", published_at: "2026-11-03T10:00:00Z", release: { versions: { "python-fees": 2, faq: 4 }, previous: { "python-fees": 1, faq: 3 }, attempts: 1, model: "m", published_at: "x" },
      items: processing.items!.map((i) => ({ ...i, publish_status: i.included ? ("published" as const) : null, result_version: i.doc_id === "faq" ? 4 : 2 })) };
    expect(await screen.findByText(/Every document went live together/, undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText(/Python fees: v1/)).toHaveTextContent("Python fees: v1 → v2");
  }, 15000);

  it("says that a change that is not a value replacement cannot be fully verified automatically", async () => {
    const pub = submitted({ status: "published", published_by: "admin1", published_at: "2026-11-03T10:00:00Z",
      release: { versions: { "python-fees": 2 }, previous: { "python-fees": 1 }, attempts: 1, model: "m", published_at: "x",
        verification: { checked_at: "2026-11-03T10:01:00Z", documents_scanned: 9, clean: true, mode: "statements", verifiable: false, remaining_total: 0, remaining: [], confirmed_new: 1,
          note: "This change is not a value replacement, so there is no old value to search for." } } });
    world({ r: pub }, {});
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    expect(await screen.findByText(/not a value replacement\): the new text is live and no removed statement was found elsewhere/)).toBeInTheDocument();
    expect(screen.getByText(/no old value to search for/)).toBeInTheDocument();
  });
  it("shows what the follow-up check still finds in the live knowledge, and can run it again", async () => {
    const pub = submitted({ status: "published", published_by: "admin1", published_at: "2026-11-03T10:00:00Z",
      release: { versions: { "python-fees": 2 }, previous: { "python-fees": 1 }, attempts: 1, model: "m", published_at: "x",
        verification: { checked_at: "2026-11-03T10:01:00Z", documents_scanned: 9, clean: false, remaining_total: 1, remaining: [{ doc_id: "faq", title: "FAQ", heading: "Fees", text: "Python fee is INR 45000.", kind: "outdated", left_by_reviewer: true }] } } });
    const state = { r: pub };
    const f = world(state, { "POST /api/admin/knowledge/changes/cr_ai1/verify": () => ({ json: { verification: { clean: true } } }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    expect(await screen.findByText(/1 passage may still be out of date/)).toBeInTheDocument();
    expect(screen.getByText(/A reviewer chose to leave this unchanged/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/verify")).toBe(1));
  });

  it("explains a failed release (nothing published) and offers Retry and Reopen", async () => {
    const state = { r: submitted({ status: "failed", failure: { stage: "processing", message: "Embedding service unavailable.", documents: ["faq"] } }) };
    const f = world(state, { "POST /api/admin/knowledge/changes/cr_ai1/publish": () => ({ status: 202, json: { outcome: "started", attempt: 2, request: { ...state.r, status: "processing" } } }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent("Nothing was published");
    expect(screen.getByRole("button", { name: "Reopen for review" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry publication" }));
    await waitFor(() => expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/publish")).toBe(1));
  });

  it("returning a request needs a note; excluding a document needs a reason", async () => {
    const f = world({ r: submitted({ status: "under_review" }) }, {
      "POST /api/admin/knowledge/changes/cr_ai1/review": () => ({ json: submitted({ status: "needs_revision" }) }),
      "POST /api/admin/knowledge/changes/cr_ai1/items/faq/review": () => ({ json: decided("manual", { doc_id: "faq", admin_decision: "excluded" }) }),
    });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    await userEvent.type(await screen.findByLabelText(/Note \(required to return/), "Please recheck the fee");
    await userEvent.click(screen.getByRole("button", { name: "Return for revision" }));
    await waitFor(() => expect(body(f, "POST", "/api/admin/knowledge/changes/cr_ai1/review")).toEqual({ decision: "return", note: "Please recheck the fee" }));
    await userEvent.click(screen.getByRole("tab", { name: /Documents/ }));
    const faq = await expand("Document FAQ");
    await userEvent.click(within(faq).getByRole("button", { name: "Exclude" }));
    expect(within(faq).getByRole("button", { name: "Exclude from publication" })).toBeDisabled();
    await userEvent.type(within(faq).getByLabelText(/Reason for excluding/), "not ready");
    await userEvent.click(within(faq).getByRole("button", { name: "Exclude from publication" }));
    await waitFor(() => expect(body(f, "POST", "/api/admin/knowledge/changes/cr_ai1/items/faq/review")).toEqual({ decision: "excluded", note: "not ready", expected_row_version: 1 }));
  });
});

describe("Deletion requests (administrator)", () => {
  const del = (o: Partial<KwRequest> = {}) => req({ status: "submitted", description: "Delete “Python fees”: course closed", submitted_by: "staff1", submitted_at: "2026-11-03T09:00:00Z",
    items: [item({ decision: "accepted", included: true, rationale: "course closed", stats: { added_chars: 0, deleted_chars: 0, changed_blocks: 0, delete: true } as KwItem["stats"], ai: null, origin: "manual" })],
    analysis: { ...req().analysis!, kind: "deletion", deletion_reason: "course closed", summary: null }, counts: { ...req().counts!, included: 1, pending: 0 }, ...o });
  it("labels the request, explains what approval does, offers no 'return', and archives only after confirmation", async () => {
    const st = { r: del({ status: "approved" }) };
    const f = mockApi({
      "GET /api/admin/knowledge/changes": () => ({ json: { items: [st.r], total: 1, page: 1, page_size: 50 } }),
      "GET /api/admin/knowledge/changes/cr_ai1": () => ({ json: st.r }),
      "POST /api/admin/knowledge/changes/cr_ai1/publish": () => { st.r = del({ status: "published", published_by: "admin1", published_at: "2026-11-03T10:00:00Z" }); return { status: 202, json: { outcome: "deleted", attempt: 1, request: st.r } }; },
    });
    signedIn("admin", "admin1"); renderWithAuth(<ChangeRequests />);
    const row = await screen.findByRole("button", { name: /Open request cr_ai1/ });
    expect(row).toHaveTextContent("Deletion");
    await userEvent.click(row);
    expect(await screen.findByRole("note")).toHaveTextContent("archives the document");
    expect(screen.queryByRole("button", { name: "Return for revision" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Archive the document now" }));
    expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/publish")).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Confirm — archive" }));
    await waitFor(() => expect(count(f, "POST", "/api/admin/knowledge/changes/cr_ai1/publish")).toBe(1));
    expect(await screen.findByText(/Deleted \(archived\)/)).toBeInTheDocument();
  });
});
