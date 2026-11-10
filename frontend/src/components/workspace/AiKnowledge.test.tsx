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
    const card = await screen.findByRole("article", { name: "Suggestion for Python fees" }, { timeout: 5000 });
    expect(card).toHaveTextContent("States the old fee.");
    expect(card).toHaveTextContent("Confidence 92%");
    expect(card).toHaveTextContent("Fees/Courses");
    expect(card).toHaveTextContent("Definitely affected");
    expect(within(card).getByText(LINE).closest("mark")).toHaveClass("kw-old");
    expect(body(f, "POST", "/api/staff/knowledge/ai-changes")).toEqual({ description: "Python fee is now 48000" });
  }, 15000);

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
    await userEvent.click(within(screen.getByRole("article", { name: "Suggestion for Python fees" })).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Suggestion for Python fees" })).getByText(/Accepted/)).toBeInTheDocument());
    expect(body(f, "POST", "/api/staff/knowledge/changes/cr_ai1/items/python-fees/decision")).toEqual({ decision: "accepted", expected_row_version: 0 });
    const c2 = screen.getByRole("article", { name: "Suggestion for Brochure" });
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
    const card = await screen.findByRole("article", { name: "Suggestion for Python fees" });
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
    const card = await screen.findByRole("article", { name: "Suggestion for Python fees" });
    expect(within(card).getByRole("alert")).toHaveTextContent("published again");
    expect(within(card).getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Edit manually" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Discard" })).toBeEnabled();
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
    const row = (await screen.findByRole("button", { name: /Open request cr_ai1/ })).closest("tr")!;
    expect(row).toHaveTextContent("Python fee to 48000");
    expect(row).toHaveTextContent("staff1");
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(3, 7)).toEqual(["2", "1", "1", "1"]);
    expect(row).toHaveTextContent("Awaiting review");
  });

  it("shows accepted, edited AND discarded documents with the staff reason, and the audit history", async () => {
    world({ r: submitted() });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Open request cr_ai1/ }));
    await userEvent.click(await screen.findByRole("tab", { name: /Documents/ }));
    expect(await screen.findByRole("article", { name: "Document Python fees" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Document FAQ" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Document Brochure" })).toHaveTextContent("Reason: historical brochure");
    await userEvent.click(screen.getByRole("tab", { name: "Audit history" }));
    const log = await screen.findByRole("list", { name: "Audit history" });
    expect(log).toHaveTextContent("Staff decision");
    expect(log).toHaveTextContent("Submitted for review");
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
    const faq = await screen.findByRole("article", { name: "Document FAQ" });
    await userEvent.click(within(faq).getByRole("button", { name: "Exclude" }));
    expect(within(faq).getByRole("button", { name: "Exclude from publication" })).toBeDisabled();
    await userEvent.type(within(faq).getByLabelText(/Reason for excluding/), "not ready");
    await userEvent.click(within(faq).getByRole("button", { name: "Exclude from publication" }));
    await waitFor(() => expect(body(f, "POST", "/api/admin/knowledge/changes/cr_ai1/items/faq/review")).toEqual({ decision: "excluded", note: "not ready", expected_row_version: 1 }));
  });
});
