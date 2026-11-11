import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminConsole } from "./AdminConsole";
import { Landing } from "./Landing";
import { Shell } from "./Shell";
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

  it("staff see Chat and the Staff console; administrators see Chat and Administration but no Staff console", () => {
    signedIn("staff", "staff1");
    const { unmount } = renderWithAuth(<Shell><p>x</p></Shell>);
    let nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Chat"); expect(nav).toHaveTextContent("Staff console"); expect(nav).not.toHaveTextContent("Administration");
    unmount();
    sessionStorage.clear();
    signedIn("admin", "admin1");
    renderWithAuth(<Shell><p>x</p></Shell>);
    nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Chat"); expect(nav).toHaveTextContent("Administration"); expect(nav).not.toHaveTextContent("Staff console");
  });

  it("the hero heading animates by line and the stylesheet turns every animation off under reduced motion", async () => {
    renderWithAuth(<Landing />);
    const lines = screen.getByRole("heading", { level: 1 }).querySelectorAll(".line");
    expect(lines).toHaveLength(3);
    const css = (await import("node:fs")).readFileSync("src/app/globals.css", "utf8");
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[^]*animation-duration: \.01ms !important/);
    expect(css).toMatch(/\.hero h1 em \{[^}]*padding: 0 \.12em[^}]*margin: 0 -\.12em/);                      // room for the italic "s" overhang, spacing unchanged
  });
});
