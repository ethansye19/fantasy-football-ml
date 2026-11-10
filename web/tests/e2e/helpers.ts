import { expect, type Page, type TestInfo } from "@playwright/test";

export interface Target {
  mode: "production" | "private_test";
  api: string;
  target: "mock" | "real";
  sleeperUser: string;
}

export function target(info: TestInfo): Target {
  return info.project.metadata as Target;
}

/** True when text claims approval (anything but "not approved", "no approved ..." and "no model is approved"). */
export function claimsApproval(text: string): boolean {
  const rest = text
    .replace(/\bnot[\s-]+approved\b/gi, "")
    .replace(/\bno approved\b/gi, "")
    .replace(/\bno model is approved\b/gi, "");
  return /\bapproved\b/i.test(rest);
}

/** Copy rules for every rendered page (D-007, D-097, D-099, PROMPT_1). */
export async function expectCopyRules(page: Page): Promise<void> {
  const text = await page.locator("body").innerText();
  expect(text, "no 'lock' wording").not.toMatch(/\block(s|ed)?\b/i);
  expect(text, "no 'guaranteed' wording").not.toMatch(/\bguaranteed\b/i);
  expect(text, "no 'can't miss' wording").not.toMatch(/can['’]?t[\s-]+miss/i);
  expect(text, "no restricted ranking source").not.toMatch(/fantasy\s*pros|\bECR\b/i);
  expect(claimsApproval(text), "no claim of model approval").toBe(false);
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const { scroll, width } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(scroll, "page fits the viewport width").toBeLessThanOrEqual(width);
}

/** Reset the contract mock's leagues (no-op against the real API). */
export async function resetMock(t: Target): Promise<void> {
  if (t.target !== "mock") return;
  await fetch(`${t.api}/__mock/reset`, { method: "POST" });
}

/** Import a Sleeper league through the UI and make it this browser's league. */
export async function importLeagueViaUi(page: Page, t: Target): Promise<void> {
  await page.goto("/setup");
  await page.getByLabel("Sleeper username").fill(t.sleeperUser);
  await page.getByRole("button", { name: "Find my leagues" }).click();
  const leagues = page.getByRole("group", { name: /Leagues for/ });
  await expect(leagues).toBeVisible();
  await leagues.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Import league" }).click();
  const teams = page.getByRole("group", { name: "Which team is yours?" });
  await expect(teams).toBeVisible({ timeout: 30_000 });
  // keep the preselected "my team" when Sleeper marked one, else take the first
  if ((await teams.getByRole("radio", { checked: true }).count()) === 0) await teams.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Use this team" }).click();
  await expect(page.getByTestId("import-done")).toBeVisible();
}

/** Every page of the app (D-124 checks: production shows the current season and never 2024). */
export const ALL_PAGES = [
  { path: "/", ready: "approval-banner" },
  { path: "/setup", ready: "approval-banner" },
  { path: "/projections", ready: "no-approved-model" },
  { path: "/start-sit", ready: "no-approved-model" },
  { path: "/waivers", ready: "no-approved-model" },
  { path: "/trade", ready: "no-approved-model" },
  { path: "/rankings", ready: "no-approved-model" },
  { path: "/about", ready: "approval-banner" },
  { path: "/about/model-card", ready: "model-status" },
  { path: "/about/limitations", ready: "model-limitations" },
  { path: "/about/attribution", ready: "source-nflverse" },
] as const;

/** Production page check (D-124): the current season (2026) is shown and "2024" appears nowhere. */
export async function expectCurrentSeasonOnly(page: Page, season = "2026"): Promise<void> {
  await expect(page.getByTestId("season-context")).toContainText(`${season} `);
  await page.waitForLoadState("networkidle");
  const text = await page.locator("body").innerText();
  expect(text, "the page shows the current season").toContain(season);
  expect(text, "no 2024 anywhere in production").not.toMatch(/2024/);
}

/** "TEST DATA: <season> week <week> replay, not approved, not a recommendation" (API v1.5). */
export function expectedTestDataLabel(run: { source: string; season: number; week: number; is_replay: boolean } | null): string | null {
  if (!run) return null;
  if (run.is_replay) return `TEST DATA: ${run.season} week ${run.week} replay, not approved, not a recommendation`;
  if (run.source === "fixture") return "TEST DATA: fixture data, not approved, not a recommendation";
  return null;
}

export const PROJECTION_PAGES = [
  { path: "/projections", heading: "Weekly projections" },
  { path: "/start-sit", heading: "Start/sit" },
  { path: "/waivers", heading: "Waiver wire" },
  { path: "/trade", heading: "Trade analyzer" },
  { path: "/rankings", heading: "Rest-of-season rankings" },
] as const;
