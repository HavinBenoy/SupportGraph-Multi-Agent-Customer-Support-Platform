import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useRef } from "react";
import { Splitter, useSplitWidth, clampWidth, SPLIT_MIN } from "./Splitter";

function Harness({ k = "t.split" }: { k?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const split = useSplitWidth(box, k, 360);
  return (
    <div ref={box} className="kw-md resizable" data-testid="box">
      <div className="kw-md-list"><button className="kw-row sel" aria-current="true">Selected</button></div>
      <Splitter container={box} split={split} label="Resize the list" controls="d" />
      <div className="kw-md-detail" id="d"><input aria-label="Proposal text" defaultValue="keeps working" /></div>
    </div>
  );
}

describe("refinement 2.1: the resizable list | detail divider", () => {
  beforeEach(() => { window.sessionStorage.clear(); Element.prototype.scrollIntoView = vi.fn(); });

  it("is an accessible, focusable vertical separator with its value and bounds", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator", { name: "Resize the list" });
    expect(sep).toHaveAttribute("aria-orientation", "vertical");
    expect(sep).toHaveAttribute("tabindex", "0");
    expect(sep).toHaveAttribute("aria-valuenow", "360");
    expect(sep).toHaveAttribute("aria-valuemin", String(SPLIT_MIN));
    expect(Number(sep.getAttribute("aria-valuemax"))).toBeGreaterThan(SPLIT_MIN);
    expect(screen.getByTestId("box").style.getPropertyValue("--kw-md-left")).toBe("360px");
  });

  it("resizes with the keyboard, clamps to min/max, resets, and remembers the width for the session", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator");
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(sep).toHaveAttribute("aria-valuenow", "376");
    fireEvent.keyDown(sep, { key: "ArrowLeft", shiftKey: true });
    expect(sep).toHaveAttribute("aria-valuenow", "312");
    fireEvent.keyDown(sep, { key: "Home" });
    expect(sep).toHaveAttribute("aria-valuenow", String(SPLIT_MIN));
    fireEvent.keyDown(sep, { key: "ArrowLeft" });
    expect(sep).toHaveAttribute("aria-valuenow", String(SPLIT_MIN));                      // never below the minimum
    fireEvent.keyDown(sep, { key: "End" });
    const max = sep.getAttribute("aria-valuemax");
    expect(sep).toHaveAttribute("aria-valuenow", max);
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(sep).toHaveAttribute("aria-valuenow", max);                                      // never above the maximum
    expect(window.sessionStorage.getItem("t.split")).toBe(max);
    fireEvent.keyDown(sep, { key: "Enter" });
    expect(sep).toHaveAttribute("aria-valuenow", "360");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();                             // the selected row is kept in view
  });

  it("restores the remembered width", () => {
    window.sessionStorage.setItem("t.split", "500");
    render(<Harness />);
    expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "500");
  });

  it("drags with the pointer, blocks text selection only while dragging, and leaves the detail usable", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator");
    fireEvent.pointerDown(sep, { button: 0, pointerId: 1, clientX: 360 });
    expect(document.body).toHaveClass("kw-resizing");
    expect(sep).toHaveClass("dragging");
    fireEvent.pointerUp(sep, { pointerId: 1, clientX: 420 });
    expect(document.body).not.toHaveClass("kw-resizing");
    const input = screen.getByLabelText("Proposal text");
    fireEvent.change(input, { target: { value: "edited" } });
    expect(input).toHaveValue("edited");
  });

  it("still works when session storage is blocked", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Harness />);
    const sep = screen.getByRole("separator");
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(sep).toHaveAttribute("aria-valuenow", "376");
    get.mockRestore(); set.mockRestore();
  });

  it("clamps against the container width", () => {
    expect(clampWidth(100, 1000)).toBe(SPLIT_MIN);
    expect(clampWidth(900, 1000)).toBe(650);
    expect(clampWidth(400, 300)).toBe(SPLIT_MIN);                                           // a tiny container never inverts the bounds
  });
});
