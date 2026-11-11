import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminConsole } from "./AdminConsole";
import { Landing } from "./Landing";
import { mockApi, renderWithAuth, signedIn } from "@/test/helpers";

afterEach(() => { vi.unstubAllGlobals(); });

describe("Part 3", () => {
  it("the landing page explains the product and links to the chat and to sign-in", () => {
    renderWithAuth(<Landing />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Support that answers");
    expect(screen.getByRole("link", { name: "Ask the assistant" })).toHaveAttribute("href", "/chat");
    expect(screen.getByRole("link", { name: "Staff sign in" })).toHaveAttribute("href", "/login");
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(4);
    document.querySelectorAll(".reveal").forEach((el) => expect(el.classList.contains("in")).toBe(true));   // jsdom has no IntersectionObserver: content is shown
  });

  it("the administration has no separate Publishing tab, and keeps the chosen tab in the URL", async () => {
    mockApi({ "GET /api/admin/knowledge/tree": () => ({ json: { folders: [], documents: [], root_document_count: 0 } }),
      "GET /api/admin/knowledge/changes": () => ({ json: { items: [], total: 0, page: 1, page_size: 50 } }) });
    window.history.replaceState({}, "", "/admin?tab=knowledge");                                            // a reload / shared link reopens the Knowledge tab
    signedIn("admin", "admin1");
    renderWithAuth(<AdminConsole />);
    expect(screen.getByRole("tab", { name: "Knowledge" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Change requests" }));
    expect(window.location.search).toContain("view=requests");
    const views = screen.getByRole("tablist", { name: "Knowledge views" });
    expect(views).toHaveTextContent("Workspace");
    expect(views).toHaveTextContent("Change requests");
    expect(screen.queryByRole("tab", { name: "Publishing" })).toBeNull();
  });
});
