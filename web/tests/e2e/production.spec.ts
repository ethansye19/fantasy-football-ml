/** Production rules (D-097, D-099): projection features are off, calmly. */
import { expect, test } from "@playwright/test";
import { PROJECTION_PAGES, expectCopyRules, importLeagueViaUi, resetMock, target } from "./helpers";

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

test("the API itself refuses projections with 503 no_approved_model", async ({ request }, info) => {
  const res = await request.get("/api/projections?scoring=ppr");
  expect(res.status()).toBe(503);
  expect(res.headers()["x-ffml-approval"]).toBe("none");
  expect(await res.json()).toMatchObject({ status: "no_approved_model" });
  expect(target(info).mode).toBe("production");
});
