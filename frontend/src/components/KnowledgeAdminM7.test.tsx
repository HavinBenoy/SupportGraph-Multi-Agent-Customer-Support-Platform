import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KnowledgeAdmin } from "./KnowledgeAdmin";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";
import type { KbDoc, KbVersionRow } from "@/lib/types";

/** M7 (docs/M6.md section 13) and final-M6 refinement 2.4 on the administrator's document console. */
const doc = (o: Partial<KbDoc> = {}): KbDoc => ({
  doc_id: "fees", title: "Fee rules", category: "fees", description: null, tags: [], status: "archived", origin: "store", latest_version: 2, published_version: null, row_version: 7,
  latest_status: "archived", published_at: null, published_by: null, created_by: "admin1", created_at: "2026-11-01T09:00:00Z", updated_by: null, updated_at: "2026-11-02T09:00:00Z",
  archived_by: "admin1", archived_at: "2026-11-02T09:00:00Z", legal_hold: false, legal_hold_reason: null, legal_hold_by: null, legal_hold_at: null, ...o,
});
const ver = (o: Partial<KbVersionRow> = {}): KbVersionRow => ({
  version: 1, title: "Fee rules", status: "superseded", lifecycle: "superseded", checksum: "a".repeat(64), size_bytes: 40, change_note: "Initial version", created_by: "admin1", created_at: "2026-11-01T09:00:00Z",
  restored_from: null, chunk_count: null, embedding_model: null, processed_at: null, processing_error: null, published_at: null, published_by: null, proposal: null, ...o,
});
const meta = { categories: ["fees", "faq"], limits: { max_bytes: 200000, max_chunks: 300 }, embedding_model: "fake:x", unmanaged_disk_documents: ["about-the-institute", "fees-and-payments"],
  document_statuses: ["draft", "published", "archived"], audit_actions: ["document_created", "document_archived", "document_purged"], purge_enabled: true, purge_min_archived_days: 0 };
const preview = (o = {}) => ({ doc_id: "fees", title: "Fee rules", status: "archived", row_version: 7, legal_hold: false, legal_hold_reason: null, archived_at: null, origin: "store",
  removes: { versions: 2, staged_chunks: 0, live_chunks: 0, change_items: 1, change_requests: 1, proposals: 0 }, keeps: [], blockers: [], disk_file_note: null, ...o });
const sent = (f: ReturnType<typeof mockApi>, method: string, path: string) => f.mock.calls.filter((c) => ((c[1] as RequestInit | undefined)?.method ?? "GET") === method && String(c[0]).split("?")[0] === path);
const body = (f: ReturnType<typeof mockApi>, method: string, path: string, i = 0) => JSON.parse(String((sent(f, method, path)[i][1] as RequestInit).body));

function world(o: { doc?: KbDoc; total?: number; docs?: KbDoc[] } = {}, extra: Record<string, (b: any, u: string) => any> = {}) {
  const w = { doc: o.doc ?? doc() };
  const f = mockApi({
    "GET /api/admin/knowledge/documents": () => ({ json: { items: o.docs ?? [w.doc], total: o.total ?? 1, page: 1, page_size: 25 } }),
    "GET /api/admin/knowledge/meta": () => ({ json: meta }),
    "GET /api/admin/knowledge/documents/fees": () => ({ json: w.doc }),
    "GET /api/admin/knowledge/documents/fees/versions": () => ({ json: { items: [ver({ version: 2, lifecycle: "archived", status: "archived" }), ver()], total: 2 } }),
    "GET /api/admin/knowledge/audit": () => ({ json: { items: [], next_before_id: null } }),
    "GET /api/admin/knowledge/feedback": () => ({ json: { items: [], total: 0 } }),
    "GET /api/admin/knowledge/proposals": () => ({ json: { items: [], total: 0 } }),
    "GET /api/admin/knowledge/documents/fees/purge-preview": () => ({ json: preview() }),
    ...extra,
  });
  return { w, f };
}
const mount = () => { signedIn("admin", "admin1"); return renderWithAuth(<KnowledgeAdmin />); };
const open = async () => { await userEvent.click(await screen.findByRole("button", { name: "Open Fee rules" })); return screen.findByRole("region", { name: "Document fees" }); };

describe("M7 documents table", () => {
  it("filters by status and category on the server and pages through the results", async () => {
    const { f } = world({ total: 30 });
    mount();
    expect(await screen.findByText("Page 1 of 2 · 30 document(s)")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Document status"), "archived");
    await waitFor(() => expect(String(f.mock.calls.at(-1)?.[0] ?? "")).toBeTruthy());
    await waitFor(() => expect(sent(f, "GET", "/api/admin/knowledge/documents").some((c) => String(c[0]).includes("status=archived"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Document category"), "faq");
    await waitFor(() => expect(sent(f, "GET", "/api/admin/knowledge/documents").some((c) => String(c[0]).includes("category=faq") && String(c[0]).includes("status=archived"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(sent(f, "GET", "/api/admin/knowledge/documents").some((c) => String(c[0]).includes("page=2"))).toBe(true));
    expect(await screen.findByText(/Page 2 of 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("shows an empty state for filters that match nothing, and server errors in words", async () => {
    world({ docs: [], total: 0 });
    mount();
    await userEvent.selectOptions(await screen.findByLabelText("Document status"), "draft");
    expect(await screen.findByText("No document matches these filters.")).toBeInTheDocument();
  });

  it("an error from the server is shown, not swallowed", async () => {
    mockApi({ "GET /api/admin/knowledge/documents": () => ({ status: 500, json: { detail: "database unavailable (reference ab12)" } }), "GET /api/admin/knowledge/meta": () => ({ json: meta }) });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("database unavailable");
  });
});

describe("M7 version comparison", () => {
  it("compares any two versions with the server's statistics and a word-level view of both complete texts", async () => {
    const { f } = world({}, {
      "GET /api/admin/knowledge/documents/fees/compare": () => ({ json: { from: ver(), to: ver({ version: 2 }), identical: false, title_changed: false, stats: { added_lines: 1, removed_lines: 1, size_delta_bytes: 2 }, diff: [], truncated: false } }),
      "GET /api/admin/knowledge/documents/fees/versions/1": () => ({ json: ver({ content: "Fees are paid in two instalments." }) }),
      "GET /api/admin/knowledge/documents/fees/versions/2": () => ({ json: ver({ version: 2, content: "Fees are paid in three instalments." }) }),
    });
    mount();
    const d = await open();
    await userEvent.click(await within(d).findByRole("button", { name: "Compare v1 → v2" }));
    expect(await within(d).findByText(/\+1 \/ −1 lines/)).toBeInTheDocument();
    expect(String(sent(f, "GET", "/api/admin/knowledge/documents/fees/compare")[0][0])).toContain("from=1&to=2");
    expect(within(d).getByRole("region", { name: "Version 1 of Fee rules" }).querySelector("del")!.textContent).toContain("two");
    expect(within(d).getByRole("region", { name: "Version 2 of Fee rules" }).querySelector("ins")!.textContent).toContain("three");
  });
});

describe("Refinement 2.4: legal hold and permanent deletion", () => {
  it("Archive and Permanently delete are separate; a live document offers no permanent deletion", async () => {
    world({ doc: doc({ status: "published", published_version: 2, latest_status: "published" }) });
    mount();
    const d = await open();
    expect(within(d).queryByRole("button", { name: /Permanently delete/ })).toBeNull();
    expect(within(d).getByRole("button", { name: "Request deletion" })).toBeInTheDocument();
  });

  it("needs the exact id, the exact title and the acknowledgement; sends the row version it was opened with", async () => {
    const { f } = world({}, { "POST /api/admin/knowledge/documents/fees/purge": () => ({ json: { doc_id: "fees", outcome: "deleted" } }) });
    mount();
    const d = await open();
    await userEvent.click(within(d).getByRole("button", { name: "Permanently delete…" }));
    const dlg = await screen.findByRole("dialog", { name: /Permanently delete/ });
    expect(dlg).toHaveTextContent("This cannot be undone.");
    expect(await within(dlg).findByRole("list", { name: "What will be removed" })).toHaveTextContent("2 version(s)");
    const go = within(dlg).getByRole("button", { name: "Delete permanently" });
    expect(go).toBeDisabled();
    await userEvent.type(within(dlg).getByLabelText("Type the document id to confirm"), "fees");
    await userEvent.type(within(dlg).getByLabelText("Type the document title to confirm"), "fee rules");                 // wrong case
    await userEvent.click(within(dlg).getByRole("checkbox"));
    expect(go).toBeDisabled();
    await userEvent.clear(within(dlg).getByLabelText("Type the document title to confirm"));
    await userEvent.type(within(dlg).getByLabelText("Type the document title to confirm"), "Fee rules");
    expect(go).toBeEnabled();
    await userEvent.click(go);
    await waitFor(() => expect(sent(f, "POST", "/api/admin/knowledge/documents/fees/purge")).toHaveLength(1));
    expect(body(f, "POST", "/api/admin/knowledge/documents/fees/purge")).toEqual({ confirm_doc_id: "fees", confirm_title: "Fee rules", expected_row_version: 7 });
    expect(await screen.findByText(/was deleted permanently/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Document fees" })).toBeNull();                                         // the detail closes
  });

  it("shows why deletion is refused and keeps the button disabled; a stale page is flagged", async () => {
    world({}, { "GET /api/admin/knowledge/documents/fees/purge-preview": () => ({ json: preview({ row_version: 8, blockers: ["Open change request(s) still reference it: cr_1."] }) }) });
    mount();
    await userEvent.click(within(await open()).getByRole("button", { name: "Permanently delete…" }));
    const dlg = await screen.findByRole("dialog", { name: /Permanently delete/ });
    expect(await within(dlg).findByText(/cr_1/)).toBeInTheDocument();
    expect(within(dlg).getByText(/changed since this page was loaded/)).toBeInTheDocument();
    await userEvent.type(within(dlg).getByLabelText("Type the document id to confirm"), "fees");
    await userEvent.type(within(dlg).getByLabelText("Type the document title to confirm"), "Fee rules");
    await userEvent.click(within(dlg).getByRole("checkbox"));
    expect(within(dlg).getByRole("button", { name: "Delete permanently" })).toBeDisabled();
  });

  it("a legal hold needs a reason, is shown on the document and disables permanent deletion", async () => {
    const { w, f } = world({}, { "PUT /api/admin/knowledge/documents/fees/legal-hold": () => { w.doc = doc({ legal_hold: true, legal_hold_reason: "audit 2026", legal_hold_by: "admin1", row_version: 8 }); return { json: w.doc }; } });
    mount();
    const d = await open();
    await userEvent.click(within(d).getByRole("button", { name: "Place legal hold" }));
    const dlg = await screen.findByRole("dialog", { name: /legal hold/ });
    expect(within(dlg).getByRole("button", { name: "Place hold" })).toBeDisabled();
    await userEvent.type(within(dlg).getByLabelText("Reason (required)"), "audit 2026");
    await userEvent.click(within(dlg).getByRole("button", { name: "Place hold" }));
    await waitFor(() => expect(body(f, "PUT", "/api/admin/knowledge/documents/fees/legal-hold")).toEqual({ hold: true, reason: "audit 2026", expected_row_version: 7 }));
    expect(await within(d).findByRole("note")).toHaveTextContent("cannot be deleted permanently while the hold is in place");
    expect(within(d).getByRole("button", { name: "Permanently delete…" })).toBeDisabled();
    expect(within(d).getByRole("button", { name: "Release legal hold" })).toBeInTheDocument();
  });
});

describe("M7 review queue, audit browser and import wizard", () => {
  it("lists every proposal with its state, filters by status, and opens the document", async () => {
    const { f } = world({}, { "GET /api/admin/knowledge/proposals": () => ({ json: { items: [{ call_id: "c1", doc_id: "fees", version: 2, status: "approved", proposed_by: "admin1", proposed_at: "2026-11-02T09:00:00Z", reviewed_by: "admin2", reviewed_at: null, review_note: null, published_by: null }], total: 1 } }) });
    mount();
    await userEvent.click(await screen.findByRole("tab", { name: "Review queue" }));
    const q = await screen.findByRole("region", { name: "Proposal queue" });
    expect(within(await within(q).findByRole("table")).getByText("Approved — not yet published")).toBeInTheDocument();
    await userEvent.selectOptions(within(q).getByLabelText("Proposal status"), "proposed");
    await waitFor(() => expect(sent(f, "GET", "/api/admin/knowledge/proposals").some((c) => String(c[0]).includes("status=proposed"))).toBe(true));
    await userEvent.click(await within(q).findByRole("button", { name: "Open document fees" }));
    expect(await screen.findByRole("region", { name: "Document fees" })).toBeInTheDocument();
  });

  it("browses the audit trail with filters and the before_id cursor; a purged document is marked, not linked", async () => {
    let n = 0;
    const { f } = world({}, { "GET /api/admin/knowledge/audit": (_b: unknown, u: string) => {
      if (u.includes("doc_id=fees") && !u.includes("limit=50")) return { json: { items: [], next_before_id: null } };   // the document panel's own audit
      n += 1;
      return u.includes("before_id=10") ? { json: { items: [{ id: 9, at: "2026-11-01T09:00:00Z", actor: "admin1", action: "document_created", resource_id: "fees", doc_id: "fees", version: 1, summary: { category: "fees" } }], next_before_id: null } }
        : { json: { items: [{ id: 11, at: "2026-11-03T09:00:00Z", actor: "admin2", action: "document_purged", resource_id: "gone", doc_id: "gone", version: null, summary: { count: 3 } },
          { id: 10, at: "2026-11-02T09:00:00Z", actor: "admin1", action: "document_archived", resource_id: "fees", doc_id: "fees", version: 1, summary: {} }], next_before_id: 10 } };
    } });
    mount();
    await userEvent.click(await screen.findByRole("tab", { name: "Audit log" }));
    const log = await screen.findByRole("region", { name: "Audit log" });
    const table = await within(log).findByRole("table");
    expect(within(table).getByText("document purged")).toBeInTheDocument();
    expect(within(log).queryByRole("button", { name: "Open document gone" })).toBeNull();
    expect(within(log).getByText("count: 3")).toBeInTheDocument();
    await userEvent.click(within(log).getByRole("button", { name: "Load older events" }));
    expect(await within(within(log).getByRole("table")).findByText("document created")).toBeInTheDocument();
    expect(within(log).getAllByRole("row")).toHaveLength(4);                                                            // header + 3 events (appended)
    await userEvent.type(within(log).getByLabelText("Filter by actor"), "admin1");
    await userEvent.selectOptions(within(log).getByLabelText("Filter by action"), "document_archived");
    await userEvent.click(within(log).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(sent(f, "GET", "/api/admin/knowledge/audit").some((c) => String(c[0]).includes("action=document_archived") && String(c[0]).includes("actor=admin1"))).toBe(true));
    expect(n).toBeGreaterThanOrEqual(3);
  });

  it("imports the disk articles only after review and confirmation, and reports the result", async () => {
    const { f } = world({}, { "POST /api/admin/knowledge/import-disk": () => ({ json: { imported: ["about-the-institute"], skipped: ["fees-and-payments"], failed: [] } }) });
    mount();
    await userEvent.click(await screen.findByRole("tab", { name: "Import from disk (2)" }));
    const w = await screen.findByRole("region", { name: "Import from disk" });
    expect(within(w).getByText("fees-and-payments")).toBeInTheDocument();
    await userEvent.click(within(w).getByRole("button", { name: "Continue" }));
    expect(sent(f, "POST", "/api/admin/knowledge/import-disk")).toHaveLength(0);
    await userEvent.click(within(w).getByRole("button", { name: "Import 2 article(s)" }));
    expect(await within(w).findByText("1 imported · 1 skipped · 0 failed.")).toBeInTheDocument();
    expect(sent(f, "POST", "/api/admin/knowledge/import-disk")).toHaveLength(1);
  });

  it("says when there is nothing to import", async () => {
    mockApi({ "GET /api/admin/knowledge/documents": () => ({ json: { items: [], total: 0, page: 1, page_size: 25 } }), "GET /api/admin/knowledge/meta": () => ({ json: { ...meta, unmanaged_disk_documents: [] } }) });
    mount();
    await userEvent.click(await screen.findByRole("tab", { name: "Import from disk" }));
    expect(await screen.findByText(/Nothing to import/)).toBeInTheDocument();
  });
});
