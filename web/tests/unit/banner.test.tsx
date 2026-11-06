import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApprovalBanner } from "@/components/status/ApprovalBanner";
import { SeasonBanner } from "@/components/status/SeasonBanner";
import { StatusProvider } from "@/components/status/StatusProvider";
import { claimsApproval, PRIVATE_APPROVAL, PRODUCTION_APPROVAL, stubApi, visibleText } from "./helpers";

function renderBanners() {
  return render(
    <StatusProvider>
      <ApprovalBanner />
      <SeasonBanner />
    </StatusProvider>,
  );
}

describe("ApprovalBanner", () => {
  it("shows a calm 'No approved model' banner in production", async () => {
    stubApi({ "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL } });
    renderBanners();
    const banner = await screen.findByTestId("approval-banner");
    expect(banner).toHaveAttribute("data-state", "no-approved-model");
    expect(banner).toHaveTextContent("No approved model.");
    expect(banner).toHaveTextContent("pre-registered test");
    expect(claimsApproval(visibleText(banner))).toBe(false);
  });

  it("shows the 'not approved' label and private-test wording in private_test", async () => {
    stubApi({
      "/api/meta/approval": { status: 200, body: PRIVATE_APPROVAL, headers: { "X-FFML-Approval": "not-approved" } },
    });
    renderBanners();
    const banner = await screen.findByTestId("approval-banner");
    expect(banner).toHaveAttribute("data-state", "not-approved");
    expect(screen.getByTestId("not-approved-label")).toHaveTextContent(/not approved/i);
    expect(banner).toHaveTextContent("Private test environment only, not a recommendation.");
    expect(claimsApproval(visibleText(banner))).toBe(false);
  });

  it("shows nothing when the API reports an approved live model", async () => {
    stubApi({
      "/api/meta/approval": {
        status: 200,
        body: { ...PRODUCTION_APPROVAL, serving: true, approval_status: "approved", label: "approved", reason: "approved_live_model" },
      },
    });
    const { container } = renderBanners();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("approval-banner")).toBeNull();
    expect(container.textContent ?? "").toBe("");
  });

  it("treats a missing API as 'status unavailable', never as approved", async () => {
    stubApi({
      "/api/meta/approval": {
        status: 502,
        body: { error: { code: "api_unreachable", message: "The app could not reach its API.", details: null } },
      },
    });
    renderBanners();
    const banner = await screen.findByTestId("approval-banner");
    expect(banner).toHaveAttribute("data-state", "unavailable");
    expect(banner).toHaveTextContent("treated as off");
  });
});

describe("SeasonBanner", () => {
  it("shows the offseason banner from /meta/season", async () => {
    stubApi({
      "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL },
      "/api/meta/season": {
        status: 200,
        body: { season: 2026, mode: "offseason", projection_week: null, week_in_progress: false, offseason: true, banner: null, as_of: "x" },
      },
    });
    renderBanners();
    expect(await screen.findByTestId("season-banner")).toHaveTextContent(/offseason/i);
  });

  it("keys on mode: 'unknown' shows a neutral note, not an offseason banner", async () => {
    stubApi({
      "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL },
      "/api/meta/season": {
        status: 200,
        body: {
          season: null,
          mode: "unknown",
          projection_week: null,
          week_in_progress: false,
          offseason: null,
          banner: "The schedule is not available yet, so the current week cannot be determined.",
          as_of: "x",
        },
      },
    });
    renderBanners();
    const banner = await screen.findByTestId("season-banner");
    expect(banner).toHaveAttribute("data-mode", "unknown");
    expect(banner).toHaveTextContent("Season status unknown.");
    expect(banner).not.toHaveTextContent(/offseason/i);
  });

  it("falls back to no banner when /meta/season is missing", async () => {
    stubApi({ "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL } });
    renderBanners();
    await screen.findByTestId("approval-banner");
    expect(screen.queryByTestId("season-banner")).toBeNull();
  });

  it("shows nothing during the regular season", async () => {
    stubApi({
      "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL },
      "/api/meta/season": {
        status: 200,
        body: { season: 2026, mode: "regular", projection_week: 10, week_in_progress: false, offseason: false, banner: null, as_of: "x" },
      },
    });
    renderBanners();
    await screen.findByTestId("approval-banner");
    expect(screen.queryByTestId("season-banner")).toBeNull();
  });
});
