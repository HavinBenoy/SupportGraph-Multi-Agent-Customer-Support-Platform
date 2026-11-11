import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompareView, applyPatches, changeCount, versionsOf, wordDiff } from "./Compare";
import type { KwItem } from "@/lib/types";

const ORIGINAL = "# Gen AI\n\nThe Gen AI faculty is Vinu sir and the class timing is morning.\nFees of gen ai course is 25000 rupees.\n\n| Course | Fee |\n|---|---|\n| Gen AI | 25000 |\n";
const AI = ORIGINAL.replace("Vinu sir", "Mohanan sir").replace("is 25000 rupees", "is 15000 rupees");
const MANUAL = AI.replace("| Gen AI | 25000 |", "| Gen AI | 15000 |");
const item = (o: Partial<KwItem> = {}): KwItem => ({
  doc_id: "genai", doc_title: "Gen AI", base_version: 1, current_published_version: 1, base_stale: false, proposed_title: "Gen AI", stats: { added_chars: 1, deleted_chars: 1, changed_blocks: 1 },
  origin: "ai", decision: null, rationale: null, row_version: 0, result_version: null, diff: { title: [], body: [{ op: "equal", text: ORIGINAL }] }, proposed_content: AI,
  ai: { reason: null, signals: null, uncertainty: null, flags: null, path: null, passages: null, edits: [
    { start: ORIGINAL.indexOf("The Gen AI"), end: ORIGINAL.indexOf("\nFees"), original: ORIGINAL.slice(ORIGINAL.indexOf("The Gen AI"), ORIGINAL.indexOf("\nFees")),
      replacement: "The Gen AI faculty is Mohanan sir and the class timing is morning.", heading: "", verdict: "definite", confidence: 0.9, reason: "" },
    { start: ORIGINAL.indexOf("Fees of"), end: ORIGINAL.indexOf("\n\n|"), original: "Fees of gen ai course is 25000 rupees.", replacement: "Fees of gen ai course is 15000 rupees.", heading: "", verdict: "definite", confidence: 0.9, reason: "" }] },
  ...o,
});

describe("word-level comparison", () => {
  it("marks only the changed words; unchanged text stays plain", () => {
    const d = wordDiff("Fees are 25000 rupees per year.", "Fees are 15000 rupees per year.");
    expect(d).toEqual([{ op: "equal", text: "Fees are " }, { op: "delete", text: "25000" }, { op: "insert", text: "15000" }, { op: "equal", text: " rupees per year." }]);
    expect(changeCount(d)).toEqual({ blocks: 1, added: 1, removed: 1 });
  });
  it("handles list items, table cells and identical text", () => {
    expect(wordDiff("- Online\n- Weekend\n", "- Online\n- Weekday\n").filter((s) => s.op !== "equal").map((s) => s.text)).toEqual(["Weekend", "Weekday"]);
    expect(wordDiff("| A | 1 |", "| A | 2 |").filter((s) => s.op !== "equal").map((s) => s.text)).toEqual(["1", "2"]);
    expect(wordDiff("same", "same")).toEqual([{ op: "equal", text: "same" }]);
  });
  it("rebuilds the AI proposal from its patches and keeps it separate from a manual revision", () => {
    expect(applyPatches(ORIGINAL, item().ai!.edits)).toBe(AI);
    const v = versionsOf(item({ decision: "manual", proposed_content: MANUAL }));
    expect(v.ai).toBe(AI);
    expect(v.manual).toBe(MANUAL);
    expect(versionsOf(item({ decision: "accepted" })).manual).toBeNull();            // no third view without a manual edit
  });
});

describe("CompareView", () => {
  it("shows complete original and AI texts in scrollable panels, highlighting only the changes", () => {
    render(<CompareView it={item()} />);
    const orig = screen.getByRole("region", { name: "Original text of Gen AI" });
    const prop = screen.getByRole("region", { name: "AI proposal for Gen AI" });
    expect(orig).toHaveTextContent("Course | Fee");                                     // the whole document, not only a window
    expect(within(orig).getByText("Vinu").closest("del")).toBeTruthy();
    expect(within(prop).getByText("Mohanan").closest("ins")).toBeTruthy();
    expect(within(prop).queryByText("Vinu")).toBeNull();
    expect(prop.querySelectorAll("ins")).toHaveLength(2);                               // only the two changed words
    expect(screen.queryByRole("region", { name: /Manual revision/ })).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("2 changes");
  });
  it("adds a third view that marks only what the person changed relative to the AI", async () => {
    render(<CompareView it={item({ decision: "manual", proposed_content: MANUAL })} />);
    const man = screen.getByRole("region", { name: /Manual revision of Gen AI/ });
    expect(man.querySelectorAll("ins")).toHaveLength(1);
    expect(within(man).getByText("Mohanan sir and the class timing is morning.", { exact: false })).toBeTruthy();
    expect(man.querySelector("ins")!.textContent).toContain("15000");
    await userEvent.click(screen.getByRole("button", { name: "Inline" }));
    expect(screen.getByRole("region", { name: "Manual changes to Gen AI" })).toBeInTheDocument();
  });
});
