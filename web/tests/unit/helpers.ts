import { vi } from "vitest";

export type Route = { status: number; body: unknown; headers?: Record<string, string> };

/** Stub fetch with a map from "/api/<path>" (no query) to a response. Unknown paths answer 404. */
export function stubApi(routes: Record<string, Route | ((url: URL, init?: RequestInit) => Route)>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.pathname}`;
    const entry = routes[key] ?? routes[url.pathname];
    const route: Route = entry
      ? typeof entry === "function"
        ? entry(url, init)
        : entry
      : { status: 404, body: { error: { code: "not_found", message: "Not Found", details: null } } };
    return new Response(route.body === null ? null : JSON.stringify(route.body), {
      status: route.status,
      headers: { "Content-Type": "application/json", ...(route.headers ?? {}) },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const PRODUCTION_APPROVAL = {
  environment: "production",
  serving: false,
  approval_status: "not approved",
  label: "not approved",
  reason: "no_live_model",
  message: "No approved model. Projections are off until a model passes its pre-registered test.",
  checked_at: "2026-11-04T20:00:00Z",
  run: null,
  live_model_version: null,
};

export const PRIVATE_APPROVAL = {
  environment: "private_test",
  serving: true,
  approval_status: "not approved",
  label: "not approved",
  reason: "private_test_replay",
  message:
    "Not approved: these numbers come from a model that has not passed its pre-registered test. Private test environment only; not a recommendation.",
  checked_at: "2026-11-04T20:00:00Z",
  run: null,
};

export const GATE_503 = {
  status: 503,
  body: {
    status: "no_approved_model",
    message: "No approved model. Projections are off until a model passes its pre-registered test.",
  },
  headers: { "X-FFML-Approval": "none" },
};

/**
 * True when text claims approval: "approved" outside the negations
 * "not approved", "no approved ..." and "no model is approved".
 */
export function claimsApproval(text: string): boolean {
  const rest = text
    .replace(/\bnot[\s-]+approved\b/gi, "")
    .replace(/\bno approved\b/gi, "")
    .replace(/\bno model is approved\b/gi, "");
  return /\bapproved\b/i.test(rest);
}

/** Visible text with a space between text nodes (jsdom has no innerText). */
export function visibleText(root: Node = document.body): string {
  const parts: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) parts.push(n.textContent ?? "");
  return parts.join(" ").replace(/\s+/g, " ");
}
