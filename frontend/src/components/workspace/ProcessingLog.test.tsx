import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ProcessingLog } from "./ProcessingLog";

describe("ProcessingLog", () => {
  it("lists every stage, names the outcome and why, and marks the stages where a change stopped", async () => {
    render(<ProcessingLog trace={{ dropped: 0, content: false, events: [
      { n: 1, ms: 0, stage: "understanding", outcome: "general_change", why: "not a value replacement" },
      { n: 2, ms: 5, stage: "discovery", outcome: "nominated", doc: "data-science", why: "it names what the change is about" },
      { n: 3, ms: 9, stage: "model_call", outcome: "model_failed", doc: "data-science", section: "Faculty", seconds: 300, why: "the model did not answer within 300 s" },
      { n: 4, ms: 9, stage: "validation", outcome: "rejected", doc: "web", rejected: ["“x”: it introduces figures"] }] }} />);
    await userEvent.click(screen.getByText(/Processing log \(4 steps\)/));
    const log = screen.getByRole("list", { name: "Processing log" });
    expect(log).toHaveTextContent("Model call");
    expect(log).toHaveTextContent("model failed");
    expect(log).toHaveTextContent("section “Faculty” · 300s · the model did not answer within 300 s");
    expect(log).toHaveTextContent("dropped: “x”: it introduces figures");
    expect(screen.getAllByRole("listitem").filter((li) => li.className === "kw-trace-bad")).toHaveLength(2);
  });
  it("renders nothing without a trace", () => {
    const { container } = render(<ProcessingLog trace={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
