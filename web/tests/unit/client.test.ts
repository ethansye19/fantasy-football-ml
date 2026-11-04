import { describe, expect, it, vi } from "vitest";
import { api, buildPath, request, subscribeApproval, type ApprovalEvent } from "@/lib/api/client";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("buildPath", () => {
  it("drops empty values and encodes the rest", () => {
    expect(buildPath("/projections", { scoring: "ppr", position: undefined, search: "", limit: 50 })).toBe(
      "/projections?scoring=ppr&limit=50",
    );
    expect(buildPath("/x", { search: "St. Brown & co" })).toBe("/x?search=St.+Brown+%26+co");
    expect(buildPath("/x")).toBe("/x");
  });
});

describe("request", () => {
  it("calls the same-origin proxy and maps a 503 gate response", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(503, { status: "no_approved_model", message: "No approved model. Off." }, { "X-FFML-Approval": "none" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const r = await api.projections.league("abc", { position: "WR" });
    expect(r.kind).toBe("no_approved_model");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/leagues/abc/projections?position=WR");
    expect(init.method).toBe("GET");
  });

  it("sends JSON bodies for POST", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { id: "L1" }));
    vi.stubGlobal("fetch", fetchMock);
    await api.leagues.importSleeper({ username: "someone", season: 2026 });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ username: "someone", season: 2026 }));
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("encodes path segments", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(404, { error: { code: "sleeper_not_found", message: "no", details: null } }));
    vi.stubGlobal("fetch", fetchMock);
    const r = await api.sleeper.userLeagues("a/b", 2026);
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toBe("/api/sleeper/users/a%2Fb/leagues?season=2026");
    expect(r).toMatchObject({ kind: "error", code: "sleeper_not_found" });
  });

  it("returns unreachable when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const r = await api.meta.approval();
    expect(r.kind).toBe("unreachable");
  });

  it("returns aborted when the caller aborts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      ),
    );
    const controller = new AbortController();
    const pending = request("/meta/health", { signal: controller.signal });
    controller.abort();
    expect((await pending).kind).toBe("aborted");
  });

  it("tells approval listeners about every observation", async () => {
    const events: ApprovalEvent[] = [];
    const unsubscribe = subscribeApproval((e) => events.push(e));
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse(
            200,
            { approval_status: "not approved", label: "not approved", environment: "private_test", run: null, notices: ["n"], players: [] },
            { "X-FFML-Approval": "not-approved" },
          ),
        )
        .mockResolvedValueOnce(
          jsonResponse(503, { status: "no_approved_model", message: "No approved model." }, { "X-FFML-Approval": "none" }),
        )
        .mockResolvedValueOnce(jsonResponse(200, { leagues: [] })),
    );
    await api.ros.preset("ppr");
    await api.waivers("L1");
    await api.leagues.list();
    unsubscribe();
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ type: "observation", observation: { approved: false, label: "not approved" } });
    expect(events[1]).toMatchObject({ type: "no_approved_model" });
  });
});
