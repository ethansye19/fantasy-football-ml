import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

// The browser only talks to this origin: API calls go to /api/* and the route
// handler in app/api/[...path]/route.ts forwards them to FFML_API_URL at runtime.
const csp = [
  "default-src 'self'",
  // Next.js inlines its bootstrap scripts; the theme script runs before paint.
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  ...(isProd ? [{ key: "Content-Security-Policy", value: csp }] : []),
];

const nextConfig: NextConfig = {
  output: "standalone",
  // Do not write an AGENTS.md into web/ on `next dev`.
  agentRules: false,
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
