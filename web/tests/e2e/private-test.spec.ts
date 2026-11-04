/** Private test rules (D-099): labeled "not approved" data, notices on every view. */
import { expect, test, type Page } from "@playwright/test";
import { expectCopyRules, importLeagueViaUi, resetMock, target } from "./helpers";

async function expectNotApprovedView(page: Page) {
  const notice = page.getByTestId("projection-notice");
  await expect(notice).toBeVisible();
  await expect(notice).toHaveAttribute("data-approval", "not-approved");
  await expect(notice.getByTestId("not-approved-label")).toBeVisible();
  await expect(notice).toContainText(/not a recommendation/i);
  await expectCopyRules(page);
}

test("the global banner is a persistent 'not approved' label", async ({ page }) => {
  await page.goto("/");
  const banner = page.getByTestId("approval-banner");
  await expect(banner).toHaveAttribute("data-state", "not-approved");
  await expect(banner.getByTestId("not-approved-label")).toBeVisible();
  await expect(banner).toContainText("Private test environment only, not a recommendation.");
  // sticky: still on screen after scrolling
  await page.mouse.wheel(0, 1500);
  await expect(banner).toBeInViewport();
});

test("every response is labeled not-approved", async ({ request }) => {
  const res = await request.get("/api/meta/approval");
  expect(res.headers()["x-ffml-approval"]).toBe("not-approved");
  const body = await res.json();
  expect(body.environment).toBe("private_test");
  expect(body.approval_status).toBe("not approved");
});

test.describe("projection views with a league", () => {
  test.beforeEach(async ({ page }, info) => {
    const t = target(info);
    await resetMock(t);
    await importLeagueViaUi(page, t);
  });

  test("projections: table, ranges, boom/bust, low confidence for K, drivers", async ({ page }) => {
    await page.goto("/projections");
    await expect(page.getByTestId("projections-table")).toBeVisible();
    await expectNotApprovedView(page);
    expect(await page.getByTestId("player-row").count()).toBeGreaterThan(5);

    await page.getByRole("group", { name: "Position" }).getByRole("button", { name: "K", exact: true }).click();
    await expect(page.getByRole("group", { name: "Position" }).getByRole("button", { name: "K", exact: true })).toHaveAttribute("aria-pressed", "true");
    const firstK = page.getByTestId("player-row").first();
    await expect(firstK.getByTestId("boom-bust").last()).toHaveAttribute("data-confidence", "low");

    await page.getByRole("group", { name: "Position" }).getByRole("button", { name: "All", exact: true }).click();
    await page.getByRole("button", { name: /^Why/ }).first().click();
    await expect(page.getByTestId("drivers").first()).toBeVisible();
  });

  test("start/sit: compare two players with no-guarantees language", async ({ page }) => {
    await page.goto("/start-sit");
    const roster = page.locator("fieldset").filter({ hasText: /^From / }).getByRole("button");
    await expect(roster.first()).toBeVisible();
    await roster.nth(0).click();
    await roster.nth(1).click();
    await page.getByRole("button", { name: "Compare" }).click();
    await expect(page.getByTestId("start-sit-result")).toBeVisible();
    await expectNotApprovedView(page);
    await expect(page.getByTestId("recommendation")).toBeVisible();
    await expect(page.getByTestId("disclaimer")).toContainText(/no guarantees/i);
  });

  test("waivers: add/drop options for my team", async ({ page }) => {
    await page.goto("/waivers");
    await expect(page.getByTestId("waiver-options").or(page.getByText("No free agent would improve your lineup"))).toBeVisible();
    await expectNotApprovedView(page);
    await expect(page.getByTestId("disclaimer")).toContainText(/no guarantees/i);
  });

  test("trade: evaluate a one-for-one trade", async ({ page }) => {
    await page.goto("/trade");
    const give = page.getByRole("group", { name: /You give/ });
    await expect(give).toBeVisible();
    await give.getByRole("checkbox").first().check();
    await page.getByRole("group", { name: /You receive/ }).getByRole("checkbox").first().check();
    await page.getByRole("button", { name: "Evaluate trade" }).click();
    await expect(page.getByTestId("trade-result")).toBeVisible();
    await expectNotApprovedView(page);
    await expect(page.getByTestId("fairness")).toBeVisible();
    await expect(page.getByTestId("disclaimer")).toContainText(/no guarantees/i);
  });

  test("rankings: rest-of-season table", async ({ page }) => {
    await page.goto("/rankings");
    await expect(page.getByTestId("ros-table")).toBeVisible();
    await expectNotApprovedView(page);
    expect(await page.getByTestId("ros-row").count()).toBeGreaterThan(5);
  });
});

test("projections without a league use preset scoring", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.removeItem("ffml.leagueId"));
  await page.goto("/projections");
  await expect(page.getByTestId("projections-table")).toBeVisible();
  await expect(page.getByLabel("Scoring")).toBeVisible();
  await expectNotApprovedView(page);
});
