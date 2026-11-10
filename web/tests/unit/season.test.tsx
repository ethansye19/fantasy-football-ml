/** D-124: the current season and week on every page; test-data labels for replays. */
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { ModelCardView } from "@/components/about/ModelCardView";
import { ProjectionNotice } from "@/components/projections/ProjectionNotice";
import { ProjectionsView } from "@/components/projections/ProjectionsView";
import { ApprovalBanner } from "@/components/status/ApprovalBanner";
import { SeasonContext } from "@/components/status/SeasonContext";
import { StatusProvider } from "@/components/status/StatusProvider";
import type { RunInfo, SeasonResponse } from "@/lib/api/types";
import { LeagueProvider } from "@/lib/league";
import { seasonLabel, seasonSourceText, testDataLabel } from "@/lib/season";
import { MODEL_CARD } from "@/mock/fixtures.mjs";
import { claimsApproval, PRIVATE_APPROVAL, PRODUCTION_APPROVAL, stubApi, stubWithMock, visibleText } from "./helpers";

const REPLAY_LABEL = "TEST DATA: 2024 week 11 replay, not approved, not a recommendation";

/** /meta/season as the API answered it on 2026-11-10 (Monday night of week 9). */
const LIVE_SEASON: SeasonResponse = {
  season: 2026,
  week: 9,
  season_type: "regular",
  mode: "regular",
  projection_week: 9,
  week_in_progress: true,
  last_completed_week: 8,
  next_kickoff_utc: "2026-11-13T01:15:00+00:00",
  offseason: false,
  banner: null,
  source: "sleeper",
  as_of: "2026-11-10T02:40:00+00:00",
  cross_check: {
    status: "agree",
    differences: [],
    message: "Sleeper and the nflverse schedule agree.",
    sleeper: { season: 2026, season_type: "regular", week: 9, display_week: 9, raw_season_type: "regular" },
    schedule: { season: 2026, season_type: "regular", week: 9, week_in_progress: true, last_completed_week: 8, games_final: 135, games_total: 272, snapshot_id: "20261110T023724Z" },
  },
};

const REPLAY_RUN: RunInfo = {
  source: "replay",
  run_id: "2024w11-20261104T225732Z-replay",
  season: 2024,
  week: 11,
  model_version: "chain_v1",
  written_at: "2026-11-04T22:57:50Z",
  n_sims: 2000,
  is_replay: true,
  has_rest_of_season: true,
  test_data_label: REPLAY_LABEL,
};

function Providers({ children }: { children: ReactNode }) {
  return (
    <StatusProvider>
      <LeagueProvider>{children}</LeagueProvider>
    </StatusProvider>
  );
}

describe("season wording", () => {
  it("labels the regular season with the week and in-progress state", () => {
    expect(seasonLabel(LIVE_SEASON)).toBe("2026 season, week 9 (in progress)");
    expect(seasonLabel({ ...LIVE_SEASON, week_in_progress: false, week: 10, projection_week: 10 })).toBe("2026 season, week 10");
    expect(seasonLabel({ ...LIVE_SEASON, week: undefined })).toBe("2026 season, week 9 (in progress)");
    expect(seasonLabel({ ...LIVE_SEASON, mode: "postseason", week: null, projection_week: null })).toBe("2026 postseason");
    expect(seasonLabel({ ...LIVE_SEASON, season: null, mode: "unknown" })).toBeNull();
    expect(seasonLabel(null)).toBeNull();
  });

  it("names the source and the cross-check result", () => {
    expect(seasonSourceText(LIVE_SEASON)).toBe("From Sleeper, matching the nflverse schedule.");
    const disagree: SeasonResponse = {
      ...LIVE_SEASON,
      source: "schedule",
      cross_check: { ...LIVE_SEASON.cross_check!, status: "disagree", differences: ["week"], sleeper: { season: 2026, season_type: "regular", week: 8 } },
    };
    expect(seasonSourceText(disagree)).toMatch(/nflverse schedule: Sleeper disagrees \(Sleeper shows week 8\)/);
    const down: SeasonResponse = {
      ...LIVE_SEASON,
      source: "schedule",
      cross_check: { ...LIVE_SEASON.cross_check!, status: "sleeper_unavailable", sleeper: null, message: "Sleeper could not be reached, so ..." },
    };
    expect(seasonSourceText(down)).toBe("From the nflverse schedule: Sleeper could not be reached.");
    const noSchedule: SeasonResponse = { ...LIVE_SEASON, cross_check: { ...LIVE_SEASON.cross_check!, status: "schedule_unavailable", schedule: null } };
    expect(seasonSourceText(noSchedule)).toMatch(/not cross-checked/);
  });

  it("builds the test-data label for replays and fixtures only", () => {
    expect(testDataLabel(REPLAY_RUN)).toBe(REPLAY_LABEL);
    expect(testDataLabel({ ...REPLAY_RUN, test_data_label: undefined })).toBe(REPLAY_LABEL);
    expect(testDataLabel({ ...REPLAY_RUN, source: "fixture", is_replay: false, test_data_label: null })).toMatch(/^TEST DATA: fixture data/);
    expect(testDataLabel({ ...REPLAY_RUN, source: "private_test", is_replay: false, test_data_label: null })).toBeNull();
    expect(testDataLabel(null)).toBeNull();
  });
});

describe("SeasonContext", () => {
  it("shows the live season and its source on every page", async () => {
    stubApi({ "/api/meta/season": { status: 200, body: LIVE_SEASON }, "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL } });
    render(<SeasonContext />, { wrapper: Providers });
    const strip = await screen.findByTestId("season-context");
    expect(strip).toHaveAttribute("data-source", "sleeper");
    expect(strip).toHaveAttribute("data-cross-check", "agree");
    expect(within(strip).getByTestId("season-context-label")).toHaveTextContent("2026 season, week 9 (in progress)");
    expect(strip).toHaveTextContent("From Sleeper, matching the nflverse schedule.");
  });

  it("shows nothing when the season is unknown or the endpoint fails", async () => {
    stubApi({ "/api/meta/season": { status: 200, body: { ...LIVE_SEASON, season: null, week: null, mode: "unknown", source: "none" } } });
    const { unmount } = render(<SeasonContext />, { wrapper: Providers });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("season-context")).toBeNull();
    unmount();
    stubApi({});
    render(<SeasonContext />, { wrapper: Providers });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("season-context")).toBeNull();
  });
});

describe("gated views in production", () => {
  it("show the current season and week as context, and stay off", async () => {
    stubWithMock({ mode: "production" });
    render(<ProjectionsView />, { wrapper: Providers });
    const state = await screen.findByTestId("no-approved-model");
    expect(await within(state).findByTestId("gated-season-context")).toHaveTextContent("Current week: 2026 season, week 10.");
    expect(visibleText(state)).not.toMatch(/2024/);
    expect(claimsApproval(visibleText(state))).toBe(false);
  });
});

describe("test-data label", () => {
  it("is shown on the projection notice of a replay, next to the not-approved label, once", () => {
    render(
      <ProjectionNotice
        approval={{
          approved: false,
          status: "not approved",
          label: "not approved",
          environment: "private_test",
          header: "not-approved",
          run: REPLAY_RUN,
          notices: [REPLAY_LABEL, "Not approved: ... not a recommendation.", "Historical replay of 2024 week 11: ..."],
        }}
      />,
    );
    const notice = screen.getByTestId("projection-notice");
    expect(within(notice).getByTestId("not-approved-label")).toHaveTextContent(/not approved/i);
    expect(within(notice).getByTestId("test-data-label")).toHaveTextContent(REPLAY_LABEL);
    expect(within(notice).getAllByText(REPLAY_LABEL)).toHaveLength(1);
    expect(claimsApproval(visibleText(notice))).toBe(false);
  });

  it("is shown in the global banner while a replay is served", async () => {
    stubApi({
      "/api/meta/approval": {
        status: 200,
        body: { ...PRIVATE_APPROVAL, serving: true, reason: "private_test_replay", run: REPLAY_RUN, test_data_label: REPLAY_LABEL },
        headers: { "X-FFML-Approval": "not-approved" },
      },
    });
    render(<ApprovalBanner />, { wrapper: Providers });
    const banner = await screen.findByTestId("approval-banner");
    expect(banner).toHaveAttribute("data-state", "not-approved");
    expect(within(banner).getByTestId("test-data-label")).toHaveTextContent(REPLAY_LABEL);
    expect(within(banner).getByTestId("not-approved-label")).toBeInTheDocument();
  });

  it("is absent in production", async () => {
    stubApi({ "/api/meta/approval": { status: 200, body: PRODUCTION_APPROVAL } });
    render(<ApprovalBanner />, { wrapper: Providers });
    await screen.findByTestId("approval-banner");
    expect(screen.queryByTestId("test-data-label")).toBeNull();
  });
});

describe("model card registry", () => {
  it("lists models by name and status, without version ids", async () => {
    stubApi({
      "/api/meta/model-card": {
        status: 200,
        body: {
          ...MODEL_CARD,
          environment: "production",
          approval_status: "not approved",
          label: "not approved",
          serving: false,
          models: [{ name: "chain_v1", version: "chain_v1-primary-c2024w19-20261102T222634Z", registry_status: "rejected", serving: false, created_at: "2026-11-03T02:55:27Z" }],
        },
      },
    });
    render(<ModelCardView />);
    expect(await screen.findByText("chain_v1")).toBeInTheDocument();
    expect(screen.getByText("Rejected: did not pass its test")).toBeInTheDocument();
    expect(visibleText()).not.toMatch(/2024/);
  });
});
