import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KnowledgeAdmin } from "./KnowledgeAdmin";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { KbDoc, KbVersionRow } from "@/lib/types";

/** Refinements 2.3 (New document button) and 2.4 (list | details), and the M7 remainder (editor that starts from the latest text, lifecycle publish bar). */
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const doc = (o: Partial<KbDoc> = {}): KbDoc => ({
  doc_id: "fees", title: "Fee rules", category: "fees", description: null, tags: [], status: "draft", origin: "store", latest_version: 2, published_version: 1, row_version: 7,
  latest_status: "draft", published_at: null, published_by: null, created_by: "admin1", created_at: "2026-11-01T09:00:00Z", updated_by: null, updated_at: "2026-11-02T09:00:00Z",
  archived_by: null, archived_at: null, legal_hold: false, legal_hold_reason: null, legal_hold_by: null, legal_hold_at: null, ...o,
});
const ver = (o: Partial<KbVersionRow> = {}): KbVersionRow => ({
  version: 1, title: "Fee rules", status: "superseded", lifecycle: "superseded", checksum: "a".repeat(64), size_bytes: 40, change_note: "Initial version", created_by: "admin1", created_at: "2026-11-01T09:00:00Z",
  restored_from: null, chunk_count: null, embedding_model: null, processed_at: null, processing_error: null, published_at: null, published_by: null, proposal: null, ...o,
});
const meta = { categories: ["fees", "faq"], limits: { max_bytes: 200000, max_chunks: 300 }, embedding_model: "fake:x", unmanaged_disk_documents: [],
  document_statuses: ["draft", "published", "archived"], audit_actions: [], purge_enabled: true, purge_min_archived_days: 0 };
const sent = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);
const body = (f: ReturnType<typeof mockApi>, method: string, path: string, i = 0) => JSON.parse(String((sent(f, method, path)[i][1] as RequestInit).body));

const TWO = [doc(), doc({ doc_id: "faq", title: "Admissions FAQ", category: "faq" })];
function world(latest: Partial<KbVersionRow> = { version: 2, lifecycle: "draft", status: "draft" }, extra: Record<string, (b: any, u: string) => any> = {}) {
  const v = (id: string) => ({ json: { items: [ver({ ...latest }), ver()], total: 2 } });
  return mockApi({
    "GET /api/admin/knowledge/documents": () => ({ json: { items: TWO, total: 2, page: 1, page_size: 25 } }),
    "GET /api/admin/knowledge/meta": () => ({ json: meta }),
    "GET /api/admin/knowledge/documents/fees": () => ({ json: TWO[0] }),
    "GET /api/admin/knowledge/documents/faq": () => ({ json: TWO[1] }),
    "GET /api/admin/knowledge/documents/fees/versions": () => v("fees"),
    "GET /api/admin/knowledge/documents/faq/versions": () => v("faq"),
    "GET /api/admin/knowledge/audit": () => ({ json: { items: [], next_before_id: null } }),
    "GET /api/admin/knowledge/feedback": () => ({ json: { items: [], total: 0 } }),
    ...extra,
  });
}
const mount = (props = {}) => { signedIn("admin", "admin1"); return renderWithAuth(<KnowledgeAdmin {...props} />); };

describe("2.3 New document button", () => {
  it("keeps the form closed until the button is pressed, and Cancel (or Escape) creates nothing", async () => {
    const f = world();
    mount();
    const open = await screen.findByRole("button", { name: "New document" });
    expect(screen.queryByRole("region", { name: "New document" })).toBeNull();
    await userEvent.click(open);
    const form = screen.getByRole("region", { name: "New document" });
    await userEvent.type(within(form).getByLabelText(/Id/), "draft-x");
    await userEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "New document" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "New document" }));
    expect(screen.getByLabelText(/Id/)).toHaveValue("");                                         // a cancelled form leaves nothing behind
    fireEvent.keyDown(screen.getByRole("region", { name: "New document" }), { key: "Escape" });
    expect(screen.queryByRole("region", { name: "New document" })).toBeNull();
    expect(sent(f, "POST", "/api/admin/knowledge/documents")).toHaveLength(0);
  });

  it("keeps the form open with the server's error, and a double click sends one request", async () => {
    let release: (v: unknown) => void = () => {};
    const f = world(undefined, { "POST /api/admin/knowledge/documents": () => new Promise((r) => { release = r; }).then(() => ({ status: 409, json: { detail: "a document with this id already exists" } })) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "New document" }));
    const form = screen.getByRole("region", { name: "New document" });
    await userEvent.type(within(form).getByLabelText(/Id/), "fees");
    await userEvent.type(within(form).getByLabelText("Title"), "Fees");
    await userEvent.type(within(form).getByLabelText(/Text/), "Body");
    const create = within(form).getByRole("button", { name: "Create document" });
    fireEvent.click(create); fireEvent.click(create);
    expect(await within(form).findByRole("button", { name: "Creating…" })).toBeDisabled();
    release(null);
    expect(await within(form).findByRole("alert")).toHaveTextContent("already exists");
    expect(sent(f, "POST", "/api/admin/knowledge/documents")).toHaveLength(1);
    expect(screen.getByRole("region", { name: "New document" })).toBeInTheDocument();
  });

  it("closes on success, refreshes the list and opens the new draft", async () => {
    const f = world(undefined, { "POST /api/admin/knowledge/documents": () => ({ status: 201, json: { document: TWO[1], version: ver() } }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "New document" }));
    const form = screen.getByRole("region", { name: "New document" });
    await userEvent.type(within(form).getByLabelText(/Id/), "faq");
    await userEvent.type(within(form).getByLabelText("Title"), "Admissions FAQ");
    await userEvent.type(within(form).getByLabelText(/Text/), "Body");
    const before = sent(f, "GET", "/api/admin/knowledge/documents").length;
    await userEvent.click(within(form).getByRole("button", { name: "Create document" }));
    expect(await screen.findByText(/Draft created/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New document" })).toBeNull();
    await waitFor(() => expect(sent(f, "GET", "/api/admin/knowledge/documents").length).toBeGreaterThan(before));
    expect(await screen.findByRole("region", { name: "Document faq" })).toBeInTheDocument();
  });

  it("opens directly when the workspace asks for it (?new=1)", async () => {
    world();
    mount({ startCreating: true });
    expect(await screen.findByRole("region", { name: "New document" })).toBeInTheDocument();
  });
});

describe("2.4 documents and details side by side", () => {
  it("shows an empty state, then the selected document next to the list, highlighted, and follows a new selection", async () => {
    world();
    mount();
    const detail = await screen.findByLabelText("Selected document");
    expect(within(detail).getByRole("heading", { name: "No document selected" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    expect(await within(detail).findByRole("region", { name: "Document fees" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Fee rules" })).toHaveAttribute("aria-current", "true");
    expect(detail.closest(".kb-split")).toHaveClass("has-sel");
    await userEvent.click(screen.getByRole("button", { name: "Open Admissions FAQ" }));
    expect(await within(detail).findByRole("region", { name: "Document faq" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Fee rules" })).not.toHaveAttribute("aria-current");
    await userEvent.click(within(detail).getByRole("button", { name: /Back to the documents/ }));
    expect(within(detail).getByRole("heading", { name: "No document selected" })).toBeInTheDocument();
  });

  it("scrolls the details into view when they are off-screen, without animation when reduced motion is requested", async () => {
    world();
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ top: 2000, bottom: 2400, left: 0, right: 100, width: 100, height: 400, x: 0, y: 2000, toJSON: () => ({}) } as DOMRect);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ behavior: "auto", block: "start" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Selected document"));
  });
});

describe("M7 remainder: editor and publish bar", () => {
  it("starts the editor from the latest version's text, previews the edit, and saves with the page's row_version", async () => {
    const f = world(undefined, {
      "GET /api/admin/knowledge/documents/fees/versions/2": () => ({ json: ver({ version: 2, content: "Fees are paid in two instalments." }) }),
      "POST /api/admin/knowledge/documents/fees/versions": () => ({ status: 201, json: ver({ version: 3 }) }),
    });
    mount({ openDoc: "fees" });
    const d = await screen.findByRole("region", { name: "Document fees" });
    const editor = await within(d).findByRole("form", { name: "New version" });
    await userEvent.click(within(editor).getByRole("button", { name: "Start from v2 text" }));
    const box = within(editor).getByLabelText("Text");
    await waitFor(() => expect(box).toHaveValue("Fees are paid in two instalments."));
    expect(within(editor).getByRole("button", { name: "Save as draft version" })).toBeDisabled();      // nothing changed yet
    await userEvent.clear(box);
    await userEvent.type(box, "Fees are paid in three instalments.");
    expect(within(editor).getAllByRole("region", { name: /Fee rules/ }).length).toBeGreaterThan(0);           // the preview against v2
    await userEvent.type(within(editor).getByLabelText("What changed"), "three instalments");
    await userEvent.click(within(editor).getByRole("button", { name: "Save as draft version" }));
    await waitFor(() => expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/versions")).toHaveLength(1));
    expect(body(f, "POST", "/api/admin/knowledge/documents/fees/versions")).toMatchObject({ content: "Fees are paid in three instalments.", change_note: "three instalments", expected_row_version: 7 });
  });

  it("the publish bar offers the next lifecycle step only: process a draft, publish a processed version", async () => {
    world();
    const { unmount } = mount({ openDoc: "fees" });
    let bar = await screen.findByRole("group", { name: "Publication" });
    expect(bar).toHaveTextContent("v2 is a draft");
    expect(within(bar).getByRole("button", { name: "Process latest (v2)" })).toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: /Publish latest/ })).toBeNull();
    unmount(); vi.unstubAllGlobals();
    const f = world({ version: 2, lifecycle: "ready_for_review", status: "ready_for_review" }, { "POST /api/admin/knowledge/documents/fees/versions/2/publish": () => ({ json: { version: 2, previous_version: 1 } }) });
    mount({ openDoc: "fees" });
    bar = await screen.findByRole("group", { name: "Publication" });
    expect(within(bar).queryByRole("button", { name: /Process latest/ })).toBeNull();
    await userEvent.click(within(bar).getByRole("button", { name: "Publish latest (v2)" }));
    await waitFor(() => expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/versions/2/publish")).toHaveLength(1));
  });
});
