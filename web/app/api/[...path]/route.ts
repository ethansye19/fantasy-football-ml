/**
 * Same-origin proxy to the API. The browser calls /api/<path>; this handler
 * forwards to FFML_API_URL/<path> (read at request time, so one build works
 * in every environment) and passes back the status, the body and the
 * X-FFML-Approval header untouched.
 *
 * Only the route families in docs/API.md are forwarded. Request bodies are
 * counted in bytes while they stream and refused above MAX_BODY_BYTES
 * (QA-F15); a Content-Length above the limit is refused before reading.
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const ALLOWED_ROOTS = new Set(["meta", "leagues", "sleeper", "players", "projections", "ros"]);
const PASS_HEADERS = ["content-type", "x-ffml-approval", "cache-control", "retry-after"];
export const MAX_BODY_BYTES = 256 * 1024;
const TIMEOUT_MS = 20_000;

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json(
    { error: { code, message, details: null } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

function targetUrl(segments: string[], search: string): URL | null {
  const root = segments[0];
  if (!root || !ALLOWED_ROOTS.has(root)) return null;
  if (segments.some((s) => s === "" || s === "." || s === ".." || s.includes("/") || s.includes("\\"))) return null;
  const base = (process.env.FFML_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
  const url = new URL(`${base}/${segments.map(encodeURIComponent).join("/")}`);
  url.search = search;
  return url;
}

/** Read the body as bytes, stopping as soon as it exceeds the limit. */
async function readBody(request: NextRequest): Promise<{ ok: true; body: Uint8Array<ArrayBuffer> | undefined } | { ok: false }> {
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return { ok: false };
  const stream = request.body;
  if (!stream) return { ok: true, body: undefined };
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return { ok: false };
    }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { ok: true, body };
}

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await context.params;
  const url = targetUrl(path ?? [], request.nextUrl.search);
  if (!url) return errorResponse(404, "not_found", "No such API route.");

  const method = request.method.toUpperCase();
  let body: Uint8Array<ArrayBuffer> | undefined;
  if (method === "POST" || method === "PUT") {
    const read = await readBody(request);
    if (!read.ok) return errorResponse(413, "payload_too_large", "The request body is too large.");
    body = read.body;
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  const contentType = request.headers.get("content-type");
  if (body !== undefined && contentType) headers["Content-Type"] = contentType;
  // When this app sits behind a reverse proxy, pass the client chain on so the
  // API can rate-limit per client (docs/API.md section 4.1, FFML_TRUSTED_PROXIES).
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers["X-Forwarded-For"] = forwardedFor;

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return errorResponse(502, "api_unreachable", "The app could not reach its API. Check that the API is running, then try again.");
  }

  const out = new Headers();
  for (const name of PASS_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) out.set(name, value);
  }
  if (!out.has("cache-control")) out.set("cache-control", "no-store");
  const payload = upstream.status === 204 || upstream.status === 304 ? null : await upstream.arrayBuffer();
  return new Response(payload, { status: upstream.status, headers: out });
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
