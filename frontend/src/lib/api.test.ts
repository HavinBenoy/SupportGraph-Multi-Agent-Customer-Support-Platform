import { api, ApiError, loadSession, saveSession } from "./api";
import { mockApi } from "@/test/helpers";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the bearer token and JSON body, and maps login to a Session", async () => {
    const f = mockApi({ "POST /api/auth/login": () => ({ json: { token: "T", role: "staff", username: "s", tenant_id: "t1", student_id: null, expires_in: 60 } }) });
    const s = await api.login("s", "pw-pw-pw-pw");
    expect(s).toMatchObject({ token: "T", role: "staff", tenantId: "t1" });
    expect(s.expiresAt).toBeGreaterThan(Date.now());
    expect(JSON.parse(String(f.mock.calls[0][1]!.body))).toEqual({ username: "s", password: "pw-pw-pw-pw" });
  });

  it("only sends the tenant for anonymous chat; signed-in identity comes from the token", async () => {
    const f = mockApi({ "POST /api/chat": () => ({ json: { ticket_id: "a-1", reply: "hi", status: "answered", citations: [], escalation_id: null, signed_in: false } }) });
    await api.chat("hello", { tenant: "t1" });
    await api.chat("hello", { token: "T", tenant: "t1", ticketId: "s-1" });
    expect(JSON.parse(String(f.mock.calls[0][1]!.body))).toEqual({ text: "hello", tenant: "t1" });
    expect(JSON.parse(String(f.mock.calls[1][1]!.body))).toEqual({ text: "hello", ticket_id: "s-1" });
    expect((f.mock.calls[1][1]!.headers as Record<string, string>).Authorization).toBe("Bearer T");
  });

  it("turns server errors into safe ApiError messages", async () => {
    mockApi({
      "GET /api/staff/approvals": () => ({ status: 403, json: { detail: "staff access required" } }),
      "GET /api/staff/escalations": () => ({ status: 500, json: { detail: "something went wrong (reference ab12)" } }),
      "POST /api/auth/login": () => ({ status: 429, json: { detail: "too many failed attempts" }, headers: { "Retry-After": "300" } }),
      "POST /api/chat": () => ({ status: 422, json: { detail: [{ loc: ["body"], msg: "internal" }] } }),
    });
    await expect(api.approvals("t")).rejects.toMatchObject({ status: 403, message: "staff access required" });
    await expect(api.escalations("t", "open")).rejects.toBeInstanceOf(ApiError);
    await expect(api.login("a", "b")).rejects.toMatchObject({ status: 429, retryAfter: 300 });
    await expect(api.chat("x", {})).rejects.toMatchObject({ message: "That input was not accepted." });
  });

  it("reports an unreachable server", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    await expect(api.config()).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Cannot reach") });
  });

  it("encodes path segments", async () => {
    const f = mockApi({ "GET /api/staff/escalations/a%2Fb": () => ({ json: {} }) });
    await api.escalation("t", "a/b");
    expect(f).toHaveBeenCalled();
  });

  it("session storage ignores expired or corrupt entries", () => {
    saveSession({ token: "t", role: "staff", username: "u", tenantId: "t1", studentId: null, expiresAt: Date.now() - 1 });
    expect(loadSession()).toBeNull();
    sessionStorage.setItem("sg.session", "{not json");
    expect(loadSession()).toBeNull();
  });
});

describe("live calls API (M4)", () => {
  it("lists calls and posts an action to the right path with the bearer token, and surfaces the server's message on a refusal", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal("fetch", vi.fn(async (u: string, init?: RequestInit) => {
      calls.push([u, init]);
      return u.endsWith("/takeover") ? new Response(JSON.stringify({ detail: "another staff member has already taken over this call" }), { status: 409 }) : new Response("[]", { status: 200 });
    }));
    const { api, ApiError } = await import("./api");
    await api.calls("tok");
    expect(calls[0][0]).toBe("/api/staff/calls");
    expect((calls[0][1]!.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    await expect(api.callAction("tok", "c_1", "takeover")).rejects.toMatchObject({ status: 409, message: "another staff member has already taken over this call" });
    expect(calls[1][0]).toBe("/api/staff/calls/c_1/takeover");
    expect(calls[1][1]!.method).toBe("POST");
    expect(ApiError).toBeDefined();
    vi.unstubAllGlobals();
  });
});
