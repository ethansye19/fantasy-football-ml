/**
 * Smoke tests against the contract mock (web/mock), one app server per mode:
 *   production    -> every projection view shows "No approved model"
 *   private_test  -> labeled "not approved" fixture data
 * Needs a production build first: `npm run build`, then `npm run e2e`.
 * For the real API use playwright.real.config.ts (`npm run e2e:real`).
 */
import { defineConfig, devices } from "@playwright/test";

const PORTS = { prodApi: 8791, privApi: 8792, prodWeb: 3101, privWeb: 3102 };
const NEXT = "node node_modules/next/dist/bin/next start -H 127.0.0.1";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    {
      name: "production",
      testMatch: /(shared|production)\.spec\.ts/,
      metadata: { mode: "production", api: `http://127.0.0.1:${PORTS.prodApi}`, target: "mock", sleeperUser: "fixtureuser" },
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORTS.prodWeb}` },
    },
    {
      name: "private_test",
      testMatch: /(shared|private-test)\.spec\.ts/,
      metadata: { mode: "private_test", api: `http://127.0.0.1:${PORTS.privApi}`, target: "mock", sleeperUser: "fixtureuser" },
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORTS.privWeb}` },
    },
  ],
  webServer: [
    {
      command: "node mock/server.mjs",
      env: { MOCK_PORT: String(PORTS.prodApi), MOCK_MODE: "production" },
      url: `http://127.0.0.1:${PORTS.prodApi}/meta/health`,
      reuseExistingServer: false,
    },
    {
      command: "node mock/server.mjs",
      env: { MOCK_PORT: String(PORTS.privApi), MOCK_MODE: "private_test" },
      url: `http://127.0.0.1:${PORTS.privApi}/meta/health`,
      reuseExistingServer: false,
    },
    {
      command: `${NEXT} -p ${PORTS.prodWeb}`,
      env: { FFML_API_URL: `http://127.0.0.1:${PORTS.prodApi}`, NEXT_TELEMETRY_DISABLED: "1" },
      url: `http://127.0.0.1:${PORTS.prodWeb}/`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `${NEXT} -p ${PORTS.privWeb}`,
      env: { FFML_API_URL: `http://127.0.0.1:${PORTS.privApi}`, NEXT_TELEMETRY_DISABLED: "1" },
      url: `http://127.0.0.1:${PORTS.privWeb}/`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
