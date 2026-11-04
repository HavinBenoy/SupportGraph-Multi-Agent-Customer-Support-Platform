import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatWidget } from "./ChatWidget";
import { CONFIG, mockApi, renderWithAuth, signedIn } from "@/test/helpers";

const reply = (over = {}) => ({ ticket_id: "a-1.sig", reply: "Refunds are available within 7 days.", status: "answered", signed_in: false,
  citations: [{ id: "policy-refund", heading: "Refund policy" }], escalation_id: null, ...over });

afterEach(() => { vi.unstubAllGlobals(); });

describe("ChatWidget", () => {
  it("guest asks a question and sees the answer with its source", async () => {
    const f = mockApi({ "GET /api/config": () => ({ json: CONFIG }), "POST /api/chat": () => ({ json: reply() }) });
    renderWithAuth(<ChatWidget />);
    expect(await screen.findByText("Test Institute")).toBeInTheDocument();
    expect(screen.getByText(/not a real institute/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your message"), "What is the refund policy?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Refunds are available within 7 days.")).toBeInTheDocument();
    expect(screen.getByText("Refund policy")).toBeInTheDocument();
    const call = f.mock.calls.find((c) => String(c[0]).endsWith("/api/chat"))!;
    expect(JSON.parse(String(call[1]!.body))).toEqual({ text: "What is the refund policy?", tenant: "t1" });
    expect(sessionStorage.getItem("sg.ticket.guest")).toBe("a-1.sig");
  });

  it("keeps the ticket across turns", async () => {
    const f = mockApi({ "GET /api/config": () => ({ json: CONFIG }), "POST /api/chat": () => ({ json: reply() }) });
    renderWithAuth(<ChatWidget />);
    const box = await screen.findByLabelText("Your message");
    await userEvent.type(box, "one{Enter}");
    await screen.findByText("Refunds are available within 7 days.");
    await userEvent.type(box, "two{Enter}");
    await waitFor(() => expect(f.mock.calls.filter((c) => String(c[0]).endsWith("/api/chat"))).toHaveLength(2));
    const second = f.mock.calls.filter((c) => String(c[0]).endsWith("/api/chat"))[1];
    expect(JSON.parse(String(second[1]!.body)).ticket_id).toBe("a-1.sig");
  });

  it("restores text and shows the error when the server fails", async () => {
    mockApi({ "GET /api/config": () => ({ json: CONFIG }), "POST /api/chat": () => ({ status: 429, json: { detail: "you are sending messages too fast, wait a moment" } }) });
    renderWithAuth(<ChatWidget />);
    await userEvent.type(await screen.findByLabelText("Your message"), "hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too fast");
    expect(screen.getByLabelText("Your message")).toHaveValue("hello");
  });

  it("signed-in student: uses the token, no tenant in the body, and locks input while staff decide", async () => {
    signedIn("student", "asha");
    const f = mockApi({
      "GET /api/config": () => ({ json: CONFIG }),
      "POST /api/chat": () => ({ json: reply({ ticket_id: "s-9.sig", reply: "I've sent your request to staff.", status: "awaiting_staff", signed_in: true, citations: [] }) }),
    });
    renderWithAuth(<ChatWidget />);
    await screen.findByText(/Signed in as asha/);
    await userEvent.type(screen.getByLabelText("Your message"), "make-up please{Enter}");
    expect(await screen.findByText(/Waiting for a staff member/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your message")).toBeDisabled();
    const call = f.mock.calls.find((c) => String(c[0]).endsWith("/api/chat"))!;
    expect((call[1]!.headers as Record<string, string>).Authorization).toBe("Bearer tok-asha");
    expect(JSON.parse(String(call[1]!.body))).toEqual({ text: "make-up please" });
  });

  it("polls while awaiting staff and shows the outcome when they decide", async () => {
    signedIn("student", "asha");
    sessionStorage.setItem("sg.ticket.t1.asha", "s-9.sig");
    let decided = false;
    mockApi({
      "GET /api/config": () => ({ json: CONFIG }),
      "GET /api/chat/s-9.sig": () => ({ json: decided
        ? { ticket_id: "s-9.sig", awaiting: null, messages: [{ role: "user", content: "make-up please" }, { role: "assistant", content: "Your make-up class was approved." }] }
        : { ticket_id: "s-9.sig", awaiting: "staff", messages: [{ role: "user", content: "make-up please" }] } }),
    });
    renderWithAuth(<ChatWidget pollMs={50} />);
    expect(await screen.findByText(/Waiting for a staff member/)).toBeInTheDocument();
    decided = true;
    expect(await screen.findByText("Your make-up class was approved.")).toBeInTheDocument();
    expect(screen.getByLabelText("Your message")).toBeEnabled();
    expect(screen.getByText("Staff have responded to your request.")).toBeInTheDocument();
  });

  it("drops a saved ticket the server no longer recognises", async () => {
    sessionStorage.setItem("sg.ticket.guest", "a-old.sig");
    mockApi({ "GET /api/config": () => ({ json: CONFIG }), "GET /api/chat/a-old.sig": () => ({ status: 404, json: { detail: "unknown conversation" } }) });
    renderWithAuth(<ChatWidget />);
    await waitFor(() => expect(sessionStorage.getItem("sg.ticket.guest")).toBeNull());
  });

  it("new conversation clears the thread", async () => {
    mockApi({ "GET /api/config": () => ({ json: CONFIG }), "POST /api/chat": () => ({ json: reply() }) });
    renderWithAuth(<ChatWidget />);
    await userEvent.type(await screen.findByLabelText("Your message"), "hi{Enter}");
    await screen.findByText("Refunds are available within 7 days.");
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.queryByText("Refunds are available within 7 days.")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("sg.ticket.guest")).toBeNull();
  });

  it("tells staff to use the console instead of the student chat", async () => {
    signedIn("staff", "s1");
    mockApi({ "GET /api/config": () => ({ json: CONFIG }) });
    renderWithAuth(<ChatWidget />);
    expect(await screen.findByText(/signed in as staff/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
  });
});
