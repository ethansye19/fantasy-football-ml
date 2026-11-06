// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, MAX_BODY_BYTES, POST } from "@/app/api/[...path]/route";

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

function stubUpstream(status = 200, body: unknown = { ok: true }, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function streamBody(bytes: Uint8Array, chunk = 64 * 1024): ReadableStream<Uint8Array> {
  let offset = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= bytes.byteLength) {
        controller.close();
        return;
      }
      controller.enqueue(bytes.subarray(offset, offset + chunk));
      offset += chunk;
    },
  });
}

type NextInit = NonNullable<ConstructorParameters<typeof NextRequest>[1]>;

function post(path: string, body: BodyInit | null, headers: Record<string, string> = {}): NextRequest {
  // `duplex` is required for streaming request bodies in Node; Next's typing does not list it.
  const init = { method: "POST", body, headers: { "Content-Type": "application/json", ...headers }, duplex: "half" } as unknown as NextInit;
  return new NextRequest(`http://web.test/api/${path}`, init);
}

describe("API proxy", () => {
  beforeEach(() => {
    process.env.FFML_API_URL = "http://api.test:8000/";
  });
  afterEach(() => {
    delete process.env.FFML_API_URL;
  });

  it("forwards allowed routes with the query string and passes approval and cache headers back", async () => {
    const fetchMock = stubUpstream(200, { players: [] }, { "X-FFML-Approval": "not-approved", "Cache-Control": "public, max-age=300" });
    const res = await GET(new NextRequest("http://web.test/api/players?search=ash&limit=5"), ctx(["players"]));
    expect(res.status).toBe(200);
    expect(res.headers.get("x-ffml-approval")).toBe("not-approved");
    expect(res.headers.get("cache-control")).toBe("public, max-age=300");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(String(url)).toBe("http://api.test:8000/players?search=ash&limit=5");
    expect(init.method).toBe("GET");
  });

  it("refuses routes outside the contract without calling the API", async () => {
    const fetchMock = stubUpstream();
    const res = await GET(new NextRequest("http://web.test/api/admin/users"), ctx(["admin", "users"]));
    expect(res.status).toBe(404);
    const dots = await GET(new NextRequest("http://web.test/api/leagues/../meta"), ctx(["leagues", "..", "meta"]));
    expect(dots.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a declared Content-Length above the limit before reading the body", async () => {
    const fetchMock = stubUpstream();
    const res = await POST(post("leagues", "{}", { "Content-Length": String(MAX_BODY_BYTES + 1) }), ctx(["leagues"]));
    expect(res.status).toBe(413);
    expect((await res.json()).error.code).toBe("payload_too_large");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("counts bytes, not characters: 200k three-byte characters are refused (QA-F15)", async () => {
    const fetchMock = stubUpstream();
    const text = "€".repeat(200_000); // 200k chars, 600 KB
    expect(text.length).toBeLessThan(MAX_BODY_BYTES);
    const bytes = new TextEncoder().encode(text);
    expect(bytes.byteLength).toBeGreaterThan(MAX_BODY_BYTES);
    const res = await POST(post("leagues", streamBody(bytes)), ctx(["leagues"]));
    expect(res.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a body within the limit byte for byte", async () => {
    const fetchMock = stubUpstream(201, { id: "L1" });
    const text = JSON.stringify({ name: "Café league" });
    const res = await POST(post("leagues", streamBody(new TextEncoder().encode(text), 4)), ctx(["leagues"]));
    expect(res.status).toBe(201);
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    const sent = init.body as Uint8Array;
    expect(new TextDecoder().decode(sent)).toBe(text);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("answers 502 api_unreachable when the API cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("ECONNREFUSED"))));
    const res = await GET(new NextRequest("http://web.test/api/meta/health"), ctx(["meta", "health"]));
    expect(res.status).toBe(502);
    expect((await res.json()).error.code).toBe("api_unreachable");
  });
});
