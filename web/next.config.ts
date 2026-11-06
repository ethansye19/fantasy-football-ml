import type { NextConfig } from "next";

// The browser only talks to this origin: API calls go to /api/* and the route
// handler in app/api/[...path]/route.ts forwards them to FFML_API_URL at runtime.
// The Content-Security-Policy is set per request with a nonce in web/proxy.ts
// (production only, QA-F16); the static headers below apply everywhere.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // Dockerfile.web sets NEXT_OUTPUT=standalone; local `next start` uses the normal build.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // Do not write an AGENTS.md into web/ on `next dev`.
  agentRules: false,
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
