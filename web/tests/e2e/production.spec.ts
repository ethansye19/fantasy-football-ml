/** Production rules (D-097, D-099): projection features are off, calmly. */
import { expect, test } from "@playwright/test";
import { ALL_PAGES, PROJECTION_PAGES, expectCopyRules, expectCurrentSeasonOnly, importLeagueViaUi, resetMock, target } from "./helpers";

const GATED = /\/api\/(projections|ros)(\?|$)|\/api\/leagues\/[^/]+\/(projections|ros|waivers|start-sit|trades)/;

test("the global banner says no approved model", async ({ page }) => {
  await page.goto("/");
  const banner = page.getByTestId("approval-banner");
  await expect(banner).toHaveAttribute("data-state", "no-approved-model");
  await expect(banner).toContainText("No approved model.");
  await expect(banner).toContainText("pre-registered test");
  await expect(page.getByTestId("status-model")).toContainText("Projections are off.");
});

test("every projection view shows the 'No approved model' state, without a league", async ({ page }) => {
  for (const { path, heading } of PROJECTION_PAGES) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    const state = page.getByTestId("no-approved-model");
    await expect(state).toBeVisible();
    await expect(state).toContainText("No approved model");
    await expect(page.getByTestId("error-state")).toHaveCount(0);
    await expectCopyRules(page);
  }
});

test("with an imported league the views stay off and never request projections", async ({ page }, info) => {
  const t = target(info);
  await resetMock(t);
  await importLeagueViaUi(page, t);
  const gated: string[] = [];
  page.on("request", (r) => {
    if (GATED.test(r.url())) gated.push(r.url());
  });
  for (const { path } of PROJECTION_PAGES) {
    await page.goto(path);
    await expect(page.getByTestId("no-approved-model")).toBeVisible();
    await expect(page.getByTestId("projection-notice")).toHaveCount(0);
  }
  expect(gated).toEqual([]);
});

test("every page shows the current 2026 season and week, and no 2024 (D-124)", async ({ page }) => {
  for (const { path, ready } of ALL_PAGES) {
    await page.goto(path);
    await expect(page.getByTestId(ready).first()).toBeVisible();
    await expectCurrentSeasonOnly(page);
  }
});

test("gated views give the current season and week as context (D-124)", async ({ page }) => {
  for (const { path } of PROJECTION_PAGES) {
    await page.goto(path);
    const context = page.getByTestId("gated-season-context");
    await expect(context).toContainText(/Current week: 2026 (season, week \d+|preseason|postseason|offseason)/);
    await expect(page.getByTestId("test-data-label")).toHaveCount(0);
  }
});

test("with the Sleeper league imported, every page still shows 2026 and no 2024", async ({ page }, info) => {
  const t = target(info);
  await resetMock(t);
  await importLeagueViaUi(page, t);
  for (const { path, ready } of ALL_PAGES) {
    await page.goto(path);
    await expect(page.getByTestId(ready).first()).toBeVisible();
    await expectCurrentSeasonOnly(page);
  }
  await page.goto("/");
  await expect(page.getByTestId("status-league")).toContainText("2026");
});

test("meta endpoints report the live 2026 season and no replay (D-124)", async ({ request }) => {
  const season = await (await request.get("/api/meta/season")).json();
  expect(season.season).toBe(2026);
  expect(["sleeper", "schedule"]).toContain(season.source);
  expect(["agree", "disagree", "sleeper_unavailable", "schedule_unavailable"]).toContain(season.cross_check.status);
  if (season.mode === "regular") expect(season.week).toBeGreaterThanOrEqual(1);
  for (const path of ["/api/meta/season", "/api/meta/run-info", "/api/meta/approval"]) {
    const res = await request.get(path);
    expect(await res.text(), path).not.toMatch(/2024/);
  }
  const runInfo = await (await request.get("/api/meta/run-info")).json();
  expect(runInfo.run).toBeNull();
  expect(runInfo.test_data_label ?? null).toBeNull();
});

test("the API itself refuses projections with 503 no_approved_model", async ({ request }, info) => {
  const res = await request.get("/api/projections?scoring=ppr");
  expect(res.status()).toBe(503);
  expect(res.headers()["x-ffml-approval"]).toBe("none");
  expect(await res.json()).toMatchObject({ status: "no_approved_model" });
  expect(target(info).mode).toBe("production");
});
