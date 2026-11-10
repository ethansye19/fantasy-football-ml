/**
 * Smoke tests against the real API (api/, FastAPI), one uvicorn per mode,
 * each with a throwaway SQLite database under web/.e2e-data/.
 *
 *   npm run build && npm run e2e:real
 *
 * E2E_PYTHON overrides the interpreter (default: the repo's .venv).
 * E2E_SLEEPER_USER is the Sleeper account used for the import flow (default:
 * the project's test user, D-020); the import calls Sleeper read-only.
 * E2E_SLEEPER_LEAGUE is the league imported when the user has several
 * (default: the D-020 test league 1389750342958223360).
 */
import { mkdirSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const PORTS = { prodApi: 8801, privApi: 8802, prodWeb: 3201, privWeb: 3202 };
const NEXT = "node node_modules/next/dist/bin/next start -H 127.0.0.1";
const REPO = resolve(__dirname, "..");
const DATA = resolve(__dirname, ".e2e-data");
const PY = process.env.E2E_PYTHON ?? (process.platform === "win32" ? join(REPO, ".venv", "Scripts", "python.exe") : join(REPO, ".venv", "bin", "python"));
const SLEEPER_USER = process.env.E2E_SLEEPER_USER ?? "ethanye19";
const SLEEPER_LEAGUE = process.env.E2E_SLEEPER_LEAGUE ?? "1389750342958223360";

// Fresh databases every run. The config is evaluated again in each worker,
// so the run directory is chosen once in the main process and inherited.
if (!process.env.E2E_RUN_DIR) {
  try {
    rmSync(DATA, { recursive: true, force: true });
  } catch {
    // an earlier run still holds a file; the new run gets its own folder anyway
  }
  process.env.E2E_RUN_DIR = join(DATA, `run-${Date.now()}`);
  mkdirSync(process.env.E2E_RUN_DIR, { recursive: true });
}
const RUN_DIR = process.env.E2E_RUN_DIR;
const sqlite = (name: string) => `sqlite:///${join(RUN_DIR, name).replace(/\\/g, "/")}`;

const api = (port: number, env: string, db: string) => ({
  command: `"${PY}" -m uvicorn api.main:app --host 127.0.0.1 --port ${port}`,
  cwd: REPO,
  env: { FFML_APP_ENV: env, FFML_DATABASE_URL: sqlite(db), PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
  url: `http://127.0.0.1:${port}/meta/health`,
  reuseExistingServer: false,
  timeout: 120_000,
});

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report-real" }]],
  outputDir: "test-results-real",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    {
      name: "real-production",
      testMatch: /(shared|production)\.spec\.ts/,
      metadata: { mode: "production", api: `http://127.0.0.1:${PORTS.prodApi}`, target: "real", sleeperUser: SLEEPER_USER, sleeperLeague: SLEEPER_LEAGUE },
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORTS.prodWeb}` },
    },
    {
      name: "real-private_test",
      testMatch: /(shared|private-test)\.spec\.ts/,
      metadata: { mode: "private_test", api: `http://127.0.0.1:${PORTS.privApi}`, target: "real", sleeperUser: SLEEPER_USER, sleeperLeague: SLEEPER_LEAGUE },
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORTS.privWeb}` },
    },
  ],
  webServer: [
    api(PORTS.prodApi, "production", "production.db"),
    api(PORTS.privApi, "private_test", "private_test.db"),
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
