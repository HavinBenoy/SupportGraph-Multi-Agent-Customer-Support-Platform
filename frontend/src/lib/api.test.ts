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

describe("api client: M4.1 and M5 endpoints", () => {
  const body = (f: ReturnType<typeof mockApi>, i = 0) => JSON.parse(String(f.mock.calls[i][1]!.body));
  const auth = (f: ReturnType<typeof mockApi>, i = 0) => (f.mock.calls[i][1]!.headers as Record<string, string>).Authorization;

  it("has no way to return a call to the assistant", () => {
    expect(Object.keys(api).filter((k) => /return/i.test(k))).toEqual([]);
  });

  it("feedback: read, submit, edit, skip use the right verbs and paths, and encode the call id", async () => {
    const f = mockApi({
      "GET /api/staff/calls/c%201/feedback": () => ({ json: {} }), "POST /api/staff/calls/c%201/feedback": () => ({ json: {} }),
      "PUT /api/staff/calls/c%201/feedback": () => ({ json: {} }), "POST /api/staff/calls/c%201/feedback/skip": () => ({ json: {} }),
    });
    const input = { category: "other", what_was_wrong: "x", suggested_correction: "y", related_index: 1 };
    await api.feedback("T", "c 1"); await api.submitFeedback("T", "c 1", input); await api.editFeedback("T", "c 1", input); await api.skipFeedback("T", "c 1");
    expect(f.mock.calls.map((c) => `${(c[1] as RequestInit).method ?? "GET"} ${String(c[0])}`)).toEqual([
      "GET /api/staff/calls/c%201/feedback", "POST /api/staff/calls/c%201/feedback", "PUT /api/staff/calls/c%201/feedback", "POST /api/staff/calls/c%201/feedback/skip"]);
    expect(body(f, 1)).toEqual(input); expect(body(f, 2)).toEqual(input);
    expect(auth(f, 3)).toBe("Bearer T");
  });

  it("transcript and interventions are read with the token", async () => {
    const f = mockApi({ "GET /api/staff/calls/c_1/transcript": () => ({ json: { entries: [] } }), "GET /api/staff/interventions": () => ({ json: { items: [], categories: {} } }) });
    await api.transcript("T", "c_1"); await api.interventions("T");
    expect(auth(f, 0)).toBe("Bearer T"); expect(auth(f, 1)).toBe("Bearer T");
  });

  it("trial settings: the PUT always carries expected_version beside the patch", async () => {
    const f = mockApi({ "PUT /api/admin/trial": () => ({ json: {} }), "GET /api/admin/trial": () => ({ json: {} }), "GET /api/staff/trial": () => ({ json: { enabled: false, recording_offered: false } }) });
    await api.saveTrial("T", 7, { enabled: true, retention_recording_days: 14 });
    expect(body(f)).toEqual({ expected_version: 7, enabled: true, retention_recording_days: 14 });
    await api.trial("T"); await api.trialStatus("T");
    expect(String(f.mock.calls[2][0])).toBe("/api/staff/trial");
  });

  it("metrics: the period goes in the query only when given; feedback filter is encoded; review sends the note only when present", async () => {
    const f = mockApi({ "GET /api/admin/trial/metrics": () => ({ json: {} }), "GET /api/admin/trial/feedback": () => ({ json: {} }), "POST /api/admin/trial/feedback/c%2F1/review": () => ({ json: {} }) });
    await api.trialMetrics("T"); await api.trialMetrics("T", "2026-10-01", "2026-10-31"); await api.trialMetrics("T", "2026-10-01");
    await api.trialFeedback("T"); await api.trialFeedback("T", "pending_review");
    await api.reviewFeedback("T", "c/1", "rejected"); await api.reviewFeedback("T", "c/1", "rejected", "no");
    const urls = f.mock.calls.map((c) => String(c[0]));
    expect(urls.slice(0, 5)).toEqual(["/api/admin/trial/metrics", "/api/admin/trial/metrics?from=2026-10-01&to=2026-10-31", "/api/admin/trial/metrics?from=2026-10-01",
      "/api/admin/trial/feedback", "/api/admin/trial/feedback?status=pending_review"]);
    expect(body(f, 5)).toEqual({ status: "rejected" }); expect(body(f, 6)).toEqual({ status: "rejected", note: "no" });
  });

  it("retention: purge sends dry_run as a real boolean; hold sends a reason only when placing one", async () => {
    const f = mockApi({ "POST /api/admin/trial/retention/purge": () => ({ json: {} }), "POST /api/admin/trial/recordings/r_1/hold": () => ({ json: {} }),
      "GET /api/admin/trial/retention": () => ({ json: {} }), "GET /api/admin/trial/recordings": () => ({ json: [] }) });
    await api.purge("T", true); await api.purge("T", false);
    await api.holdRecording("T", "r_1", true, "Complaint"); await api.holdRecording("T", "r_1", false);
    await api.retention("T"); await api.recordings("T");
    expect([body(f, 0), body(f, 1), body(f, 2), body(f, 3)]).toEqual([{ dry_run: true }, { dry_run: false }, { hold: true, reason: "Complaint" }, { hold: false }]);
  });
});
