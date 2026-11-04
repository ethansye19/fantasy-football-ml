/** Runs in both modes: shell, info pages and league setup work everywhere. */
import { expect, test } from "@playwright/test";
import { expectCopyRules, expectNoHorizontalOverflow, importLeagueViaUi, resetMock, target } from "./helpers";

test.describe("shell", () => {
  test("home renders with navigation, skip link and a model status", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.getByTestId("approval-banner")).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await expectCopyRules(page);
  });

  test("theme toggle switches between light and dark", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("button", { name: "Use system theme" }).click();
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
  });

  test("works at 360 px with the mobile menu", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    for (const path of ["/", "/setup", "/about/attribution", "/projections"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoHorizontalOverflow(page);
    }
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("link", { name: "About" }).click();
    await expect(page).toHaveURL(/\/about$/);
    await expect(page.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
  });

  test("unknown pages show the not-found state", async ({ page }) => {
    const res = await page.goto("/no-such-page");
    expect(res?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });
});

test.describe("information pages", () => {
  test("about, model card, limitations and attribution", async ({ page }) => {
    await page.goto("/about");
    await expect(page.getByRole("heading", { name: "Football first, fantasy points last" })).toBeVisible();
    await expectCopyRules(page);

    await page.getByRole("navigation", { name: "About pages" }).getByRole("link", { name: "Model card" }).click();
    const status = page.getByTestId("model-status");
    await expect(status).toBeVisible();
    await expect(status.getByTestId("not-approved-label")).toBeVisible();
    await expectCopyRules(page);

    await page.goto("/about/limitations");
    await expect(page.getByTestId("model-limitations")).toContainText(/low confidence/i);
    await expectCopyRules(page);

    await page.goto("/about/attribution");
    await expect(page.getByTestId("source-nflverse")).toContainText("Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0.");
    await expect(page.getByTestId("source-ftn")).toContainText("FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0.");
    await expect(page.getByTestId("source-sleeper")).toContainText("League and player data from Sleeper.");
    await expectCopyRules(page);
  });
});

test.describe("league setup", () => {
  test("Sleeper import: username, league, my team", async ({ page }, info) => {
    const t = target(info);
    await resetMock(t);
    await importLeagueViaUi(page, t);
    await page.reload();
    await expect(page.getByTestId("league-list")).toBeVisible();
    await expect(page.getByTestId("league-row").filter({ hasText: "In use" })).toHaveCount(1);
    await expectCopyRules(page);
  });

  test("manual settings form validates before saving", async ({ page }) => {
    await page.goto("/setup");
    await page.getByRole("tab", { name: "Enter settings by hand" }).click();
    await page.getByRole("button", { name: "Create league" }).click();
    await expect(page.getByTestId("error-summary")).toContainText("Enter a league name.");
    await expect(page.getByLabel("League name")).toHaveAttribute("aria-invalid", "true");

    await page.getByLabel("League name").fill("Smoke test league");
    await page.getByRole("radio", { name: /Half PPR/ }).check();
    await page.getByLabel("Passing TD").fill("abc");
    await page.getByRole("button", { name: "Create league" }).click();
    await expect(page.getByTestId("error-summary")).toContainText("Passing TD: Enter a number.");

    await page.getByLabel("Passing TD").fill("6");
    await page.getByRole("button", { name: "Create league" }).click();
    await expect(page.getByText("League created.")).toBeVisible();
    await expect(page.getByTestId("league-row").filter({ hasText: "Smoke test league" })).toBeVisible();
  });
});
