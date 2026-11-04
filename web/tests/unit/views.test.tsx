import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { RankingsView } from "@/components/projections/RankingsView";
import { StartSitView } from "@/components/projections/StartSitView";
import { TradeView } from "@/components/projections/TradeView";
import { WaiversView } from "@/components/projections/WaiversView";
import { StatusProvider } from "@/components/status/StatusProvider";
import type { League } from "@/lib/api/types";
import { LeagueProvider } from "@/lib/league";
import { claimsApproval, stubWithMock, visibleText } from "./helpers";

function Providers({ children }: { children: ReactNode }) {
  return (
    <StatusProvider>
      <LeagueProvider>{children}</LeagueProvider>
    </StatusProvider>
  );
}

/** Import a fixture league in the mock and make it this browser's league. */
function withLeague(mode: "production" | "private_test" = "private_test"): League {
  const { mock } = stubWithMock({ mode });
  const out = mock.handle("POST", "/leagues/import/sleeper", new URLSearchParams(), { username: "oneleague" });
  const league = out.body as League;
  window.localStorage.setItem("ffml.leagueId", league.id);
  return league;
}

function expectNotApprovedNotice() {
  const notice = screen.getByTestId("projection-notice");
  expect(notice).toHaveAttribute("data-approval", "not-approved");
  expect(within(notice).getByTestId("not-approved-label")).toBeInTheDocument();
  expect(notice).toHaveTextContent("not a recommendation");
}

describe("gate and league requirements", () => {
  it.each([
    ["start/sit", StartSitView],
    ["waivers", WaiversView],
    ["trade", TradeView],
    ["rankings", RankingsView],
  ])("%s: production shows 'No approved model' even with a league", async (_name, View) => {
    withLeague("production");
    render(<View />, { wrapper: Providers });
    expect(await screen.findByTestId("no-approved-model")).toBeInTheDocument();
  });

  it("start/sit without a league asks for one", async () => {
    stubWithMock({ mode: "private_test" });
    render(<StartSitView />, { wrapper: Providers });
    expect(await screen.findByText("Set up a league first")).toBeInTheDocument();
  });
});

describe("start/sit", () => {
  it("compares two players and shows odds, reasons and no-guarantees language", async () => {
    const league = withLeague();
    const user = userEvent.setup();
    render(<StartSitView />, { wrapper: Providers });
    const mine = league.teams?.find((t) => t.is_me);
    const [a, b] = (mine?.players ?? []).filter((p) => p.position !== "DEF");
    await user.click(await screen.findByRole("button", { name: new RegExp(a?.name ?? "") }));
    await user.click(screen.getByRole("button", { name: new RegExp(b?.name ?? "") }));
    await user.click(screen.getByRole("button", { name: "Compare" }));

    const result = await screen.findByTestId("start-sit-result");
    expectNotApprovedNotice();
    expect(within(result).getByTestId("recommendation")).toHaveTextContent(/simulations/);
    expect(within(result).getByTestId("chance-bars")).toHaveTextContent("%");
    expect(within(result).getAllByTestId("compare-card")).toHaveLength(2);
    expect(within(result).getByTestId("disclaimer")).toHaveTextContent(/^No guarantees/);
    expect(claimsApproval(visibleText(result))).toBe(false);
  });
});

describe("waivers", () => {
  it("lists add/drop pairs for my roster", async () => {
    withLeague();
    render(<WaiversView />, { wrapper: Providers });
    const list = await screen.findByTestId("waiver-options");
    expectNotApprovedNotice();
    const first = within(list).getAllByRole("listitem")[0] as HTMLElement;
    expect(first).toHaveTextContent("Add");
    expect(first).toHaveTextContent("Drop");
    expect(first).toHaveTextContent(/Next week/);
    expect(screen.getByTestId("disclaimer")).toHaveTextContent(/^No guarantees/);
  });
});

describe("trade", () => {
  it("evaluates a trade and shows fairness, roster fit and no-guarantees language", async () => {
    withLeague();
    const user = userEvent.setup();
    render(<TradeView />, { wrapper: Providers });
    const give = await screen.findByRole("group", { name: /You give/ });
    const receive = screen.getByRole("group", { name: /You receive/ });
    await user.click(within(give).getAllByRole("checkbox")[0] as HTMLElement);
    await user.click(within(receive).getAllByRole("checkbox")[1] as HTMLElement);
    await user.click(screen.getByRole("button", { name: "Evaluate trade" }));

    const result = await screen.findByTestId("trade-result");
    expectNotApprovedNotice();
    expect(within(result).getByTestId("fairness")).toHaveTextContent(/rest-of-season points over replacement/);
    expect(within(result).getByTestId("roster-fit")).toHaveTextContent("You");
    expect(within(result).getByTestId("disclaimer")).toHaveTextContent(/^No guarantees/);
  });
});

describe("rankings", () => {
  it("uses the preset route without a league", async () => {
    const { fetchMock } = stubWithMock({ mode: "private_test" });
    render(<RankingsView />, { wrapper: Providers });
    await screen.findByTestId("ros-table");
    expectNotApprovedNotice();
    expect(screen.getAllByTestId("ros-row").length).toBeGreaterThan(10);
    expect(fetchMock.mock.calls.map(([u]) => String(u)).some((u) => u.startsWith("/api/ros?scoring=ppr"))).toBe(true);
  });
});
