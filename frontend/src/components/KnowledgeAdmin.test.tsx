import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KnowledgeAdmin } from "./KnowledgeAdmin";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { KbDoc, KbVersionRow } from "@/lib/types";

afterEach(() => { vi.unstubAllGlobals(); });

const doc = (o: Partial<KbDoc> = {}): KbDoc => ({
  doc_id: "fees", title: "Fee rules", category: "fees", description: null, tags: [], status: "draft", origin: "store", latest_version: 1, published_version: null, row_version: 4,
  latest_status: "draft", published_at: null, published_by: null, created_by: "admin1", created_at: "2026-11-01T09:00:00Z", updated_by: null, updated_at: null, archived_by: null, archived_at: null, ...o,
});
const ver = (o: Partial<KbVersionRow> = {}): KbVersionRow => ({
  version: 1, title: "Fee rules", status: "draft", lifecycle: "draft", checksum: "a".repeat(64), size_bytes: 40, change_note: "Initial version", created_by: "admin1", created_at: "2026-11-01T09:00:00Z",
  restored_from: null, chunk_count: null, embedding_model: null, processed_at: null, processing_error: null, published_at: null, published_by: null, proposal: null, ...o,
});
const meta = { categories: ["fees", "faq"], limits: { max_bytes: 200000, max_chunks: 300 }, embedding_model: "fake:x", unmanaged_disk_documents: ["old-a", "old-b"] };

function world(o: { doc?: KbDoc; versions?: KbVersionRow[]; feedback?: unknown[] } = {}, extra: Record<string, (b: any, u: string) => any> = {}) {
  const w = { doc: o.doc ?? doc(), versions: o.versions ?? [ver()], feedback: o.feedback ?? [] };
  const f = mockApi({
    "GET /api/admin/knowledge/documents": () => ({ json: { items: [w.doc], total: 1, page: 1, page_size: 50 } }),
    "GET /api/admin/knowledge/meta": () => ({ json: meta }),
    "GET /api/admin/knowledge/documents/fees": () => ({ json: w.doc }),
    "GET /api/admin/knowledge/documents/fees/versions": () => ({ json: { items: w.versions, total: w.versions.length } }),
    "GET /api/admin/knowledge/audit": () => ({ json: { items: [{ id: 1, at: "2026-11-01T09:00:00Z", actor: "admin1", action: "document_created", resource_id: "fees", doc_id: "fees", version: 1 }], next_before_id: null } }),
    "GET /api/admin/knowledge/feedback": () => ({ json: { items: w.feedback, total: w.feedback.length } }),
    "GET /api/admin/knowledge/proposals": () => ({ json: { items: [], total: 0 } }),
    ...extra,
  });
  return { w, f };
}
const sent = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);
const mount = () => { signedIn("admin", "admin1"); return renderWithAuth(<KnowledgeAdmin />); };

describe("Knowledge admin", () => {
  it("lists documents with status and the live version, and notes disk articles that are not managed yet", async () => {
    world({ doc: doc({ status: "published", published_version: 2, latest_version: 3 }) });
    mount();
    const row = await screen.findByRole("button", { name: "Open Fee rules" });
    expect(row).toHaveTextContent("Published");
    expect(row).toHaveTextContent("latest v3 · live v2");
    expect(await screen.findByTestId("unmanaged")).toHaveTextContent("2 article(s)");
  });

  it("walks a draft through process and publish: publish is offered only once the version is ready for review", async () => {
    const { w, f } = world({}, {
      "POST /api/admin/knowledge/documents/fees/versions/1/process": () => ({ json: { status: "ready", chunks: 2 } }),
      "POST /api/admin/knowledge/documents/fees/versions/1/publish": () => ({ json: { version: 1, previous_version: null } }),
    });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    const v1 = await screen.findByTestId("version-1");
    expect(within(v1).queryByRole("button", { name: "Publish v1" })).toBeNull();
    w.versions = [ver({ lifecycle: "ready_for_review", status: "ready", chunk_count: 2 })];
    await userEvent.click(within(v1).getByRole("button", { name: "Process v1" }));
    expect(await screen.findByText("Version 1 processed.")).toBeInTheDocument();
    expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/versions/1/process")).toHaveLength(1);
    expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/versions/1/publish")).toHaveLength(0);          // nothing publishes by itself
    const ready = await screen.findByTestId("version-1");
    expect(await within(ready).findByRole("button", { name: "Publish v1" })).toBeInTheDocument();                 // now offered, but still not pressed
    expect(within(ready).getByRole("button", { name: "Re-process v1" })).toBeInTheDocument();
  });

  it("publishing sends exactly one request and reports the new live version", async () => {
    const { f } = world({ versions: [ver({ lifecycle: "ready_for_review", status: "ready" })] }, {
      "POST /api/admin/knowledge/documents/fees/versions/1/publish": () => ({ json: { version: 1, previous_version: null } }),
    });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    await userEvent.click(await screen.findByRole("button", { name: "Publish v1" }));
    expect(await screen.findByText("Version 1 is now live.")).toBeInTheDocument();
    expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/versions/1/publish")).toHaveLength(1);
  });

  it("a failed processing run is shown as an error and the server's refusal text reaches the administrator", async () => {
    world({}, { "POST /api/admin/knowledge/documents/fees/versions/1/process": () => ({ json: { status: "failed", error: "EmbeddingError", chunks: 0 } }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    await userEvent.click(await screen.findByRole("button", { name: "Process v1" }));
    expect(await screen.findByText(/Processing failed: EmbeddingError/)).toBeInTheDocument();
  });

  it("a server refusal (409) is shown in words, not swallowed", async () => {
    world({ versions: [ver({ lifecycle: "ready_for_review" })] }, { "POST /api/admin/knowledge/documents/fees/versions/1/publish": () => ({ status: 409, json: { detail: "this version comes from a feedback proposal that is proposed: an administrator must approve the proposal first" } }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    await userEvent.click(await screen.findByRole("button", { name: "Publish v1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("must approve the proposal first");
  });

  it("saving a new version sends the row version it was loaded with, as a draft", async () => {
    const { f } = world({}, { "POST /api/admin/knowledge/documents/fees/versions": () => ({ status: 201, json: { document: doc(), version: ver({ version: 2 }) } }) });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    const form = await screen.findByRole("form", { name: "New version" });
    await userEvent.type(within(form).getByLabelText("Text"), "Three instalments are allowed.");
    await userEvent.type(within(form).getByLabelText("What changed"), "now three");
    await userEvent.click(within(form).getByRole("button", { name: "Save as draft version" }));
    expect(await screen.findByText("New version saved as a draft.")).toBeInTheDocument();
    expect(JSON.parse(String((sent(f, "POST", "/api/admin/knowledge/documents/fees/versions")[0][1] as RequestInit).body))).toEqual({ content: "Three instalments are allowed.", change_note: "now three", expected_row_version: 4 });
  });

  it("restore creates a new version rather than editing history, and the old version can be viewed", async () => {
    const { f } = world({ doc: doc({ latest_version: 2 }), versions: [ver({ version: 2, lifecycle: "draft" }), ver({ version: 1, lifecycle: "superseded", status: "superseded" })] }, {
      "POST /api/admin/knowledge/documents/fees/versions/1/restore": () => ({ status: 201, json: { document: doc(), version: ver({ version: 3, restored_from: 1 }) } }),
      "GET /api/admin/knowledge/documents/fees/versions/1": () => ({ json: ver({ version: 1, content: "Original wording." }) }),
    });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    const v1 = await screen.findByTestId("version-1");
    await userEvent.click(within(v1).getByRole("button", { name: "View v1" }));
    expect(await screen.findByText("Original wording.")).toBeInTheDocument();
    await userEvent.click(within(v1).getByRole("button", { name: "Restore v1" }));
    expect(await screen.findByText("Version 1 restored as a new draft version.")).toBeInTheDocument();
    expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/versions/1/restore")).toHaveLength(1);
    expect(within(screen.getByTestId("version-2")).queryByRole("button", { name: "Restore v2" })).not.toBeNull();
  });

  it("the published version has no restore or process button; a live document offers Unpublish and a deletion REQUEST, never a one-click archive", async () => {
    world({ doc: doc({ status: "published", published_version: 1 }), versions: [ver({ lifecycle: "published", status: "published" })] });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    const v1 = await screen.findByTestId("version-1");
    expect(within(v1).queryByRole("button", { name: /Restore|Process|Publish/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Unpublish" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Request deletion" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("not deleted now");
  });

  it("an archived document offers only unarchive and no new-version form", async () => {
    world({ doc: doc({ status: "archived" }), versions: [ver({ lifecycle: "archived", status: "archived" })] });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" }));
    expect(await screen.findByRole("button", { name: "Unarchive" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    expect(screen.queryByRole("form", { name: "New version" })).toBeNull();
  });

  it("creates a new document as a draft", async () => {
    const { f } = world({}, { "POST /api/admin/knowledge/documents": () => ({ status: 201, json: { document: doc(), version: ver() } }) });
    mount();
    expect(await screen.findByRole("button", { name: "New document" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New document" })).toBeNull();                     // 2.3: hidden until asked for
    await userEvent.click(screen.getByRole("button", { name: "New document" }));
    const form = await screen.findByRole("region", { name: "New document" });
    await userEvent.type(within(form).getByLabelText(/^Id/), "fees");
    await userEvent.type(within(form).getByLabelText("Title"), "Fee rules");
    await userEvent.type(within(form).getByLabelText(/^Text/), "# Fees\n\nBody text here.");
    await userEvent.click(within(form).getByRole("button", { name: "Create document" }));
    expect(await screen.findByText(/Draft created/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New document" })).toBeNull();                     // closes after success
    expect(JSON.parse(String((sent(f, "POST", "/api/admin/knowledge/documents")[0][1] as RequestInit).body))).toMatchObject({ doc_id: "fees", title: "Fee rules", category: "fees" });
  });

  it("approved feedback starts a draft only; approve and reject are separate steps and never publish", async () => {
    const fbRow = { call_id: "c1", category: "incorrect_information", what_was_wrong: "Wrong instalment rule.", suggested_correction: "Two instalments.", written_by: "staff1", proposal: null };
    const { w, f } = world({ feedback: [fbRow] }, {
      "POST /api/admin/knowledge/proposals": () => { w.feedback = [{ ...fbRow, proposal: { call_id: "c1", doc_id: "new-doc", version: 1, status: "proposed", proposed_by: "admin1", reviewed_by: null, published_by: null, review_note: null } }]; return { status: 201, json: {} }; },
      "POST /api/admin/knowledge/proposals/c1/review": () => ({ json: {} }),
    });
    mount();
    await userEvent.click(await screen.findByRole("tab", { name: "Review queue" }));               // M7: the review queue has its own tab
    const card = await screen.findByTestId("fb-c1");
    expect(within(card).getByText("Wrong instalment rule.")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Start a knowledge change" }));
    await userEvent.type(within(card).getByLabelText("New document id"), "new-doc");
    await userEvent.type(within(card).getByLabelText("Title"), "New doc");
    await userEvent.type(within(card).getByLabelText("Text"), "Two instalments are allowed.");
    await userEvent.click(within(card).getByRole("button", { name: "Create draft" }));
    expect(await screen.findByText(/Draft created from feedback/)).toBeInTheDocument();
    await userEvent.click(await within(await screen.findByTestId("fb-c1")).findByRole("button", { name: "Approve proposal" }));
    expect(await screen.findByText(/It still has to be processed and published/)).toBeInTheDocument();
    expect(JSON.parse(String((sent(f, "POST", "/api/admin/knowledge/proposals/c1/review")[0][1] as RequestInit).body))).toEqual({ decision: "approve" });
    expect(f.mock.calls.some((c) => String(c[0]).endsWith("/publish"))).toBe(false);
  });
});
