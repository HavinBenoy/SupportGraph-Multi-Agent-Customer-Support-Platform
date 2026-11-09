import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";
import { DiffView } from "./DiffView";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { KwDiff, KwItem, KwReadDoc, KwRequest, KwTree } from "@/lib/types";
import { render } from "@testing-library/react";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const TREE: KwTree = {
  folders: [
    { folder_id: "f_1", parent_id: null, name: "Fees", path: "Fees", row_version: 0, document_count: 1 },
    { folder_id: "f_2", parent_id: "f_1", name: "Instalments", path: "Fees/Instalments", row_version: 3, document_count: 0 },
  ],
  documents: [
    { doc_id: "fee-rules", title: "Fee rules", category: "fees", folder_id: "f_1", status: "published", published_version: 2, latest_version: 2, updated_at: "2026-11-01T09:00:00Z" },
    { doc_id: "faq", title: "General FAQ", category: "faq", folder_id: null, status: "published", published_version: 1, latest_version: 1, updated_at: null },
  ],
  root_document_count: 1,
};
const ORIGINAL = "Fees can be paid in two instalments.\n";
const READ: KwReadDoc = {
  doc_id: "fee-rules", title: "Fee rules", category: "fees", tags: [], version: 2, published_at: "2026-11-01T09:00:00Z", published_by: "admin1", change_note: "Clarified", authored_by: "admin1",
  checksum: "abc", size_bytes: 40, row_version: 5, content: ORIGINAL, folder_id: "f_1", breadcrumbs: [{ id: "f_1", name: "Fees" }], path: "Fees",
};
const item = (o: Partial<KwItem> = {}): KwItem => ({
  doc_id: "fee-rules", doc_title: "Fee rules", base_version: 2, current_published_version: 2, base_stale: false, proposed_title: "Fee rules", stats: { added_chars: 0, deleted_chars: 0, changed_blocks: 0 },
  origin: "manual", decision: null, rationale: null, row_version: 0, result_version: null, diff: { title: [{ op: "equal", text: "Fee rules" }], body: [{ op: "equal", text: ORIGINAL }] }, proposed_content: ORIGINAL, ...o,
});
const req = (o: Partial<KwRequest> = {}): KwRequest => ({
  request_id: "cr_1", status: "draft", source: "manual", description: null, row_version: 0, created_by: "staff1", created_at: "2026-11-02T09:00:00Z", updated_at: "2026-11-02T09:00:00Z",
  submitted_by: null, submitted_at: null, reviewed_by: null, reviewed_at: null, review_note: null, applied_by: null, applied_at: null, document_count: 1,
  documents: [{ doc_id: "fee-rules", title: "Fee rules" }], added_chars: 0, deleted_chars: 0, items: [item()], ...o,
});
const DIFF: KwDiff = { title: [{ op: "equal", text: "Fee rules" }], body: [{ op: "equal", text: "Fees can be paid in " }, { op: "delete", text: "two" }, { op: "insert", text: "three" }, { op: "equal", text: " instalments.\n" }] };
const calls = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);

function staffWorld(extra: Record<string, (b: any, u: string) => any> = {}) {
  return mockApi({
    "GET /api/staff/knowledge/tree": () => ({ json: TREE }),
    "GET /api/staff/knowledge/documents/fee-rules": () => ({ json: READ }),
    "GET /api/staff/knowledge/documents": () => ({ json: { items: [{ doc_id: "fee-rules", title: "Fee rules", category: "fees" }], total: 1 } }),
    ...extra,
  });
}
const mountStaff = () => { signedIn("staff", "staff1"); return renderWithAuth(<KnowledgeWorkspace scope="staff" />); };

describe("Knowledge workspace — reading", () => {
  it("browses folders, opens a document read-only with breadcrumbs, version and last-updated info", async () => {
    staffWorld();
    mountStaff();
    await userEvent.click(await screen.findByRole("button", { name: "Open folder Fees" }));
    expect(screen.getByRole("heading", { name: "Fees" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open Fee rules" }));
    expect(await screen.findByRole("heading", { name: "Fee rules" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Knowledge base / Fees");
    expect(screen.getByText("Published v2")).toBeInTheDocument();
    expect(screen.getByText(/Last updated/)).toHaveTextContent("by admin1");
    expect(screen.getByLabelText("Published document (read only)")).toHaveTextContent("Fees can be paid in two instalments.");
    expect(screen.queryByRole("textbox", { name: "Document text" })).toBeNull();                    // the canonical text is never an editable field
    expect(screen.getByRole("button", { name: "Make changes" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Move to folder")).toBeNull();                                  // staff cannot reorganise
    expect(screen.queryByText("Create folder")).toBeNull();
  });

  it("searches by name and content through the server and opens a hit", async () => {
    const f = staffWorld();
    mountStaff();
    await screen.findByRole("button", { name: "Open folder Fees" });
    await userEvent.type(screen.getByRole("searchbox", { name: "Search knowledge" }), "instalment");
    const hit = await screen.findByRole("button", { name: "Open Fee rules" }, { timeout: 2000 });
    expect(String(calls(f, "GET", "/api/staff/knowledge/documents").at(-1)![0])).toContain("q=instalment");
    await userEvent.click(hit);
    expect(await screen.findByLabelText("Published document (read only)")).toBeInTheDocument();
  });

  it("shows version history and a structured comparison", async () => {
    staffWorld({
      "GET /api/staff/knowledge/documents/fee-rules/versions": () => ({ json: { items: [
        { version: 2, title: "Fee rules", change_note: "Three", authored_by: "staff1", published_at: "2026-11-01T09:00:00Z", published_by: "admin1", is_current: true, size_bytes: 40 },
        { version: 1, title: "Fee rules", change_note: "First", authored_by: "admin1", published_at: "2026-10-01T09:00:00Z", published_by: "admin1", is_current: false, size_bytes: 38 }], total: 2 } }),
      "GET /api/staff/knowledge/documents/fee-rules/compare": () => ({ json: { diff: DIFF } }),
    });
    mountStaff();
    await userEvent.click(await screen.findByRole("button", { name: "Open folder Fees" }));
    await userEvent.click(screen.getByRole("button", { name: "Open Fee rules" }));
    await userEvent.click(await screen.findByRole("button", { name: "Version history" }));
    await userEvent.click(await screen.findByRole("button", { name: "Compare v1 with v2" }));
    const region = await screen.findByRole("region", { name: "Differences between v1 and v2" });
    expect(within(region).getByText("two").closest("del")).not.toBeNull();
    expect(within(region).getByText("three").closest("ins")).not.toBeNull();
  });
});

describe("Knowledge workspace — editing", () => {
  const editorWorld = (extra: Record<string, (b: any, u: string) => any> = {}) => staffWorld({
    "POST /api/staff/knowledge/changes": () => ({ status: 201, json: { request: req(), existing: false } }),
    "POST /api/staff/knowledge/diff-preview": (b: { content: string }) => ({ json: { doc_id: "fee-rules", base_version: 2, diff: DIFF, stats: { added_chars: 5, deleted_chars: 3, changed_blocks: 2 }, unchanged: b.content === ORIGINAL } }),
    ...extra,
  });
  async function openEditor() {
    mountStaff();
    await userEvent.click(await screen.findByRole("button", { name: "Open folder Fees" }));
    await userEvent.click(screen.getByRole("button", { name: "Open Fee rules" }));
    await userEvent.click(await screen.findByRole("button", { name: "Make changes" }));
    return await screen.findByRole("textbox", { name: "Document text" });
  }

  it("Make changes opens an editor on a copy; review shows deleted text in red-marked <del> and added text in <ins>, unchanged text plain", async () => {
    const f = editorWorld();
    const box = await openEditor();
    expect(box).toHaveValue(ORIGINAL);
    expect(screen.getByText(/original v2 stays live/)).toBeInTheDocument();
    await userEvent.clear(box);
    await userEvent.type(box, "Fees can be paid in three instalments.{enter}");
    await userEvent.click(screen.getByRole("tab", { name: "Review changes" }));
    const region = await screen.findByRole("region", { name: "Your changes compared with the published text" }, { timeout: 2000 });
    expect(within(region).getByText("two").closest("del")).toHaveClass("kw-del");
    expect(within(region).getByText("three").closest("ins")).toHaveClass("kw-ins");
    expect(within(region).getByText(/Fees can be paid in/).closest("del,ins")).toBeNull();
    expect(region).toHaveTextContent("Deleted: two");                                              // not colour alone
    expect(calls(f, "PUT", "/api/staff/knowledge/changes/cr_1/items/fee-rules")).toHaveLength(0);   // reviewing never saves
  });

  it("Save draft sends the text with the item's version token and confirms the published text is unchanged", async () => {
    const f = editorWorld({ "PUT /api/staff/knowledge/changes/cr_1/items/fee-rules": (b: { content: string }) => ({ json: item({ row_version: 1, proposed_content: b.content, stats: { added_chars: 5, deleted_chars: 3, changed_blocks: 2 } }) }) });
    const box = await openEditor();
    await userEvent.type(box, "More.");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByRole("status")).toHaveTextContent("The published text has not changed");
    const body = JSON.parse(String((calls(f, "PUT", "/api/staff/knowledge/changes/cr_1/items/fee-rules")[0][1] as RequestInit).body));
    expect(body).toEqual({ content: ORIGINAL + "More.", expected_row_version: 0 });
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();                       // nothing unsaved any more
  });

  it("a 409 from another window is explained and offers the latest draft instead of overwriting", async () => {
    editorWorld({
      "PUT /api/staff/knowledge/changes/cr_1/items/fee-rules": () => ({ status: 409, json: { detail: "this edit was saved from another window since you opened it: reload it so you do not overwrite that work" } }),
      "GET /api/staff/knowledge/changes/cr_1": () => ({ json: req({ items: [item({ row_version: 4, proposed_content: "Other window text.\n" })] }) }),
    });
    const box = await openEditor();
    await userEvent.type(box, "x");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("another window");
    await userEvent.click(screen.getByRole("button", { name: "Load the latest draft" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Document text" })).toHaveValue("Other window text.\n"));
  });

  it("submitting needs a description, saves unsaved text first, and then returns to My change requests", async () => {
    const f = editorWorld({
      "PUT /api/staff/knowledge/changes/cr_1/items/fee-rules": (b: { content: string }) => ({ json: item({ row_version: 1, proposed_content: b.content }) }),
      "POST /api/staff/knowledge/changes/cr_1/submit": () => ({ json: req({ status: "submitted", description: "Three instalments" }) }),
      "GET /api/staff/knowledge/changes": () => ({ json: { items: [req({ status: "submitted", description: "Three instalments" })], total: 1, page: 1, page_size: 50 } }),
    });
    const box = await openEditor();
    await userEvent.type(box, "Extra line.");
    await userEvent.click(screen.getByRole("button", { name: "Submit change request…" }));
    await userEvent.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Describe the change");
    expect(calls(f, "POST", "/api/staff/knowledge/changes/cr_1/submit")).toHaveLength(0);
    await userEvent.type(screen.getByLabelText("What changed, and why?"), "Three instalments");
    await userEvent.click(screen.getByRole("button", { name: "Send for approval" }));
    expect(await screen.findByRole("heading", { name: "My change requests" })).toBeInTheDocument();
    expect(calls(f, "PUT", "/api/staff/knowledge/changes/cr_1/items/fee-rules")).toHaveLength(1);    // saved before submitting
    expect(JSON.parse(String((calls(f, "POST", "/api/staff/knowledge/changes/cr_1/submit")[0][1] as RequestInit).body))).toEqual({ description: "Three instalments" });
    expect(screen.getByRole("status")).toHaveTextContent("stays live until an administrator approves");
    expect(screen.getByText("Awaiting review")).toBeInTheDocument();
  });

  it("Cancel changes asks first; declining keeps editing, accepting cancels the request and returns to the reader", async () => {
    const f = editorWorld({ "POST /api/staff/knowledge/changes/cr_1/cancel": () => ({ json: req({ status: "cancelled" }) }) });
    const box = await openEditor();
    await userEvent.type(box, "oops");
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(screen.getByRole("button", { name: "Cancel changes" }));
    expect(calls(f, "POST", "/api/staff/knowledge/changes/cr_1/cancel")).toHaveLength(0);
    expect(screen.getByRole("textbox", { name: "Document text" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel changes" }));
    await waitFor(() => expect(calls(f, "POST", "/api/staff/knowledge/changes/cr_1/cancel")).toHaveLength(1));
    expect(await screen.findByRole("status")).toHaveTextContent("Changes discarded");
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("resumes a saved draft instead of starting over", async () => {
    editorWorld({ "POST /api/staff/knowledge/changes": () => ({ status: 201, json: { request: req({ items: [item({ row_version: 3, proposed_content: "Saved earlier.\n" })] }), existing: true } }) });
    const box = await openEditor();
    expect(box).toHaveValue("Saved earlier.\n");
    expect(screen.getByRole("status")).toHaveTextContent("Resumed your saved draft");
  });
});

describe("Knowledge workspace — administrators", () => {
  const adminWorld = (extra: Record<string, (b: any, u: string) => any> = {}) => mockApi({
    "GET /api/admin/knowledge/tree": () => ({ json: TREE }),
    "GET /api/staff/knowledge/documents/fee-rules": () => ({ json: READ }),
    ...extra,
  });
  const mountAdmin = () => { signedIn("admin", "admin1"); return renderWithAuth(<KnowledgeWorkspace scope="admin" />); };

  it("creates a folder in the current folder and renames it with the version token", async () => {
    const f = adminWorld({
      "POST /api/admin/knowledge/folders": () => ({ status: 201, json: {} }),
      "PUT /api/admin/knowledge/folders/f_2": () => ({ json: {} }),
    });
    mountAdmin();
    await userEvent.click(await screen.findByRole("button", { name: "Folder Fees" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open folder Instalments" }));
    await userEvent.type(screen.getByLabelText("New folder here"), "Monthly");
    await userEvent.click(screen.getByRole("button", { name: "Create folder" }));
    await waitFor(() => expect(calls(f, "POST", "/api/admin/knowledge/folders")).toHaveLength(1));
    expect(JSON.parse(String((calls(f, "POST", "/api/admin/knowledge/folders")[0][1] as RequestInit).body))).toEqual({ name: "Monthly", parent_id: "f_2" });
    const rn = screen.getByLabelText("Rename this folder");
    await userEvent.clear(rn);
    await userEvent.type(rn, "Payment plans");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(calls(f, "PUT", "/api/admin/knowledge/folders/f_2")).toHaveLength(1));
    expect(JSON.parse(String((calls(f, "PUT", "/api/admin/knowledge/folders/f_2")[0][1] as RequestInit).body))).toEqual({ name: "Payment plans", expected_row_version: 3 });
  });

  it("surfaces a duplicate-folder conflict from the server", async () => {
    adminWorld({ "POST /api/admin/knowledge/folders": () => ({ status: 409, json: { detail: "a folder with this name already exists here" } }) });
    mountAdmin();
    await screen.findByRole("button", { name: "Open folder Fees" });
    await userEvent.type(screen.getByLabelText("New folder here"), "Fees");
    await userEvent.click(screen.getByRole("button", { name: "Create folder" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });

  it("moves a document to another folder with the document's version token", async () => {
    const f = adminWorld({ "PUT /api/admin/knowledge/documents/fee-rules/folder": () => ({ json: { doc_id: "fee-rules", folder_id: "f_2", row_version: 6 } }) });
    mountAdmin();
    await userEvent.click(await screen.findByRole("button", { name: "Open folder Fees" }));
    await userEvent.click(screen.getByRole("button", { name: "Open Fee rules" }));
    await userEvent.selectOptions(await screen.findByLabelText("Move to folder"), "f_2");
    await waitFor(() => expect(calls(f, "PUT", "/api/admin/knowledge/documents/fee-rules/folder")).toHaveLength(1));
    expect(JSON.parse(String((calls(f, "PUT", "/api/admin/knowledge/documents/fee-rules/folder")[0][1] as RequestInit).body))).toEqual({ folder_id: "f_2", expected_row_version: 5 });
  });
});

describe("DiffView", () => {
  it("renders segment text as text (never as HTML) and labels deletions and additions for assistive tech", () => {
    const { container } = render(<DiffView segs={[{ op: "equal", text: "a " }, { op: "delete", text: "<b>bold</b>" }, { op: "insert", text: "<img src=x onerror=alert(1)>" }]} />);
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("del")).toHaveTextContent("Deleted: <b>bold</b>");
    expect(container.querySelector("ins")).toHaveTextContent("Added: <img src=x onerror=alert(1)>");
  });
});
