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

export const PROJECTION_PAGES = [
  { path: "/projections", heading: "Weekly projections" },
  { path: "/start-sit", heading: "Start/sit" },
  { path: "/waivers", heading: "Waiver wire" },
  { path: "/trade", heading: "Trade analyzer" },
  { path: "/rankings", heading: "Rest-of-season rankings" },
] as const;
