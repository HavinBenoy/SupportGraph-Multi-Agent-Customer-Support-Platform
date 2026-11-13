import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MyRequests } from "./Requests";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { KwItem, KwRequest } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, "", "/"); });

const BASE = "# Fees\nPython Full Stack fee is INR 45000.\nJava Full Stack fee is INR 50000.\n";
const LINE = "Python Full Stack fee is INR 45000.";
const START = BASE.indexOf(LINE);
const NEW = "Python Full Stack fee is INR 48000.";
const AI_TEXT = BASE.replace(LINE, NEW);
const EDIT = { start: START, end: START + LINE.length, original: LINE, replacement: NEW, heading: "Fees", verdict: "change", confidence: 0.9, reason: "fee" };
const item = (o: Partial<KwItem> = {}): KwItem => ({
  doc_id: "python-fees", doc_title: "Python fees", base_version: 1, current_published_version: 1, base_stale: false, proposed_title: "Python fees",
  stats: { added_chars: 3, deleted_chars: 3, changed_blocks: 1 }, origin: "ai", decision: "accepted", rationale: null, row_version: 2, result_version: null,
  diff: { title: [{ op: "equal", text: "Fees" }], body: [{ op: "equal", text: BASE.slice(0, START) }, { op: "delete", text: LINE }, { op: "insert", text: NEW }, { op: "equal", text: BASE.slice(START + LINE.length) }] },
  proposed_content: AI_TEXT, base_checksum: "a", proposed_checksum: "b", classification: "definite", confidence: 0.9, included: true, actionable: true,
  ai: { reason: "fee", signals: null, uncertainty: null, flags: null, path: null, passages: null, edits: [EDIT] }, final_edits: [EDIT], revision_source: "ai", revised_after_return: false, ...o,
});
const req = (o: Partial<KwRequest> = {}): KwRequest => ({
  request_id: "cr_1", status: "needs_revision", source: "ai_assisted", description: "Python fee is now 48000", row_version: 3, created_by: "u1", created_at: null, updated_at: null,
  submitted_by: "u1", submitted_at: null, reviewed_by: "admin1", reviewed_at: null, review_note: null, applied_by: null, applied_at: null, document_count: 1,
  documents: [{ doc_id: "python-fees", title: "Python fees" }], added_chars: 0, deleted_chars: 0, items: [item()], return_note: "Mention GST",
  counts: { accepted: 1, edited: 0, discarded: 0, pending: 0, manual_items: 0, related: 0, included: 1, excluded_by_admin: 0 }, analysis: null, ...o,
} as KwRequest);

function mount(r0: KwRequest | (() => KwRequest), extra: Record<string, (b: unknown) => { status?: number; json?: unknown }> = {}) {
  signedIn("staff", "u1");
  window.history.replaceState(null, "", "/agent?cr=cr_1");
  const onResumeAi = vi.fn();
  const r = () => (typeof r0 === "function" ? r0() : r0);
  const f = mockApi({
    "GET /api/staff/knowledge/changes": () => ({ json: { items: [r()], total: 1, page: 1, page_size: 50 } }),
    "GET /api/staff/knowledge/changes/cr_1": () => ({ json: r() }),
    ...extra,
  });
  renderWithAuth(<MyRequests onResume={() => {}} onResumeAi={onResumeAi} />);
  return { f, onResumeAi };
}
const calls = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);

describe("refinement 2.2: revising a returned request", () => {
  it("offers two clearly separate options and labels where the text came from", async () => {
    mount(req());
    const panel = await screen.findByRole("region", { name: "Revise this request" });
    const group = within(panel).getByRole("group", { name: "Revision options" });
    expect(within(group).getByRole("button", { name: /A · Revise with the AI/ })).toBeEnabled();
    expect(within(group).getByRole("button", { name: /B · Edit manually/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Reviewer note: Mention GST")).toBeInTheDocument();
    expect(screen.getAllByText("AI-assisted").length).toBeGreaterThan(0);
    expect(within(panel).queryByRole("textbox")).toBeNull();                                  // the editor is closed until the manual option is chosen
  });

  it("option A re-opens the AI workflow for the same request", async () => {
    const { onResumeAi } = mount(req());
    await userEvent.click(await screen.findByRole("button", { name: /A · Revise with the AI/ }));
    await waitFor(() => expect(onResumeAi).toHaveBeenCalledWith("cr_1"));
  });

  it("option A converts a manual request into an AI-assisted one first (same request id)", async () => {
    const r = req({ source: "manual", items: [item({ origin: "manual", decision: null, actionable: false, revision_source: "manual", ai: null })] });
    const { f, onResumeAi } = mount(r, { "POST /api/staff/knowledge/ai-changes": (b) => ({ status: 201, json: { ...r, source: "ai_assisted", _b: b } }) });
    await userEvent.click(await screen.findByRole("button", { name: /A · Revise with the AI/ }));
    await waitFor(() => expect(onResumeAi).toHaveBeenCalledWith("cr_1"));
    expect(JSON.parse(String((calls(f, "POST", "/api/staff/knowledge/ai-changes")[0][1] as RequestInit).body)).request_id).toBe("cr_1");
  });

  it("option B edits the full returned proposal, compares original / AI / manual, saves it as a manual decision and resubmits without publishing", async () => {
    const manualText = AI_TEXT.replace(NEW, `${NEW} (inclusive of GST)`) + "Instalments are available on request.\n";
    let current = req();
    const { f } = mount(() => current, {
      "POST /api/staff/knowledge/changes/cr_1/items/python-fees/decision": () => {
        current = req({ items: [item({ decision: "manual", row_version: 3, proposed_content: manualText, revision_source: "manual", revised_after_return: true, staff_edited_by: "u1" })] });
        return { json: current.items![0] };
      },
      "POST /api/staff/knowledge/changes/cr_1/submit": () => ({ json: { ...current, status: "submitted" } }),
    });
    await userEvent.click(await screen.findByRole("button", { name: /B · Edit manually/ }));
    const box = screen.getByLabelText("Full proposed text (Markdown)");
    expect(box).toHaveValue(AI_TEXT);                                                          // starts from the returned proposal
    expect(screen.getByRole("button", { name: "Save revision" })).toBeDisabled();             // nothing changed yet
    await userEvent.clear(box);
    await userEvent.type(box, manualText.replace(/[[{]/g, (c) => c + c));
    expect(screen.getAllByRole("region", { name: /Comparison for Python fees/ }).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Save revision" }));
    await waitFor(() => expect(calls(f, "POST", "/api/staff/knowledge/changes/cr_1/items/python-fees/decision")).toHaveLength(1));
    const sent = JSON.parse(String((calls(f, "POST", "/api/staff/knowledge/changes/cr_1/items/python-fees/decision")[0][1] as RequestInit).body));
    expect(sent).toEqual({ decision: "manual", content: manualText, expected_row_version: 2 });
    await waitFor(() => expect(screen.getAllByText(/Manual · revised after return/).length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Resubmit for review" }));
    expect(await screen.findByText(/Resubmitted for review. Nothing is published/)).toBeInTheDocument();
    expect(f.mock.calls.some((c) => String(c[0]).includes("/publish") || String(c[0]).includes("/admin/"))).toBe(false);   // never publishes, never an admin call
  });

  it("option B on a manual request uses the draft editor with its concurrency token and shows a conflict", async () => {
    const r = req({ source: "manual", items: [item({ origin: "manual", decision: null, actionable: false, revision_source: "manual", ai: null, row_version: 5, proposed_content: "Hello\n" })] });
    const { f } = mount(r, { "PUT /api/staff/knowledge/changes/cr_1/items/python-fees": () => ({ status: 409, json: { detail: "this edit was saved from another window since you opened it: reload it" } }) });
    await userEvent.click(await screen.findByRole("button", { name: /B · Edit manually/ }));
    await userEvent.type(screen.getByLabelText("Full proposed text (Markdown)"), "More");
    await userEvent.click(screen.getByRole("button", { name: "Save revision" }));
    expect(await screen.findByText(/saved from another window/)).toBeInTheDocument();
    expect(JSON.parse(String((calls(f, "PUT", "/api/staff/knowledge/changes/cr_1/items/python-fees")[0][1] as RequestInit).body)).expected_row_version).toBe(5);
  });

  it("is not offered once the request is back in review", async () => {
    mount(req({ status: "submitted" }));
    await screen.findByRole("heading", { level: 2 });
    expect(screen.queryByRole("region", { name: "Revise this request" })).toBeNull();
  });
});
