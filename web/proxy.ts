/**
 * Per-request Content Security Policy with a nonce (QA-F16).
 *
 * Runs on every page request in production; the API proxy, static assets
 * and router prefetches are excluded by the matcher. Next.js reads the nonce
 * from the request's CSP header and puts it on the inline scripts it emits,
 * so `script-src` needs no 'unsafe-inline'; the theme bootstrap in
 * app/layout.tsx reads the same nonce through headers(). Reading headers()
 * makes every page render per request instead of being prerendered, which
 * is the documented cost of a nonce-based CSP in the App Router.
 *
 * `style-src` keeps 'unsafe-inline' for React style attributes (the
 * distribution strip positions its marks with inline styles); stylesheets
 * only come from this origin.
 */
import { NextResponse, type NextRequest } from "next/server";

export function proxy(request: NextRequest): NextResponse {
  if (process.env.NODE_ENV !== "production") return NextResponse.next();
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join("; ");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api/|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
