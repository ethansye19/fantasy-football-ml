import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { ProjectionsView } from "@/components/projections/ProjectionsView";
import { StatusProvider } from "@/components/status/StatusProvider";
import { LeagueProvider } from "@/lib/league";
import { claimsApproval, stubWithMock, visibleText } from "./helpers";

function Providers({ children }: { children: ReactNode }) {
  return (
    <StatusProvider>
      <LeagueProvider>{children}</LeagueProvider>
    </StatusProvider>
  );
}

function gatedCalls(fetchMock: { mock: { calls: unknown[][] } }) {
  return fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => /\/api\/(projections|ros|leagues\/[^/]+\/(projections|ros|waivers|start-sit|trades))/.test(u));
}

describe("ProjectionsView", () => {
  it("production: shows the calm 'No approved model' state and makes no projection request", async () => {
    const { fetchMock } = stubWithMock({ mode: "production" });
    render(<ProjectionsView />, { wrapper: Providers });
    const state = await screen.findByTestId("no-approved-model");
    expect(state).toHaveTextContent("No approved model");
    expect(state).toHaveTextContent("pre-registered test");
    expect(screen.queryByTestId("error-state")).toBeNull();
    expect(gatedCalls(fetchMock)).toEqual([]);
  });

  it("private_test: shows the not-approved label, every notice, and the table", async () => {
    stubWithMock({ mode: "private_test" });
    render(<ProjectionsView />, { wrapper: Providers });
    const notice = await screen.findByTestId("projection-notice");
    expect(notice).toHaveAttribute("data-approval", "not-approved");
    expect(within(notice).getByTestId("not-approved-label")).toHaveTextContent(/not approved/i);
    expect(notice).toHaveTextContent("Private test environment only; not a recommendation.");
    expect(notice).toHaveTextContent("Fixture data for testing; not real projections.");
    expect(notice).toHaveTextContent("Fixture data: 2026 week 10");
    const rows = screen.getAllByTestId("player-row");
    expect(rows.length).toBeGreaterThan(10);
    expect(screen.getAllByTestId("distribution-strip").length).toBeGreaterThan(10);
    expect(claimsApproval(visibleText())).toBe(false);
  });

  it("marks K and DST boom/bust as low confidence and shows drivers on demand", async () => {
    stubWithMock({ mode: "private_test" });
    const user = userEvent.setup();
    render(<ProjectionsView />, { wrapper: Providers });
    await screen.findByTestId("projections-table");
    await user.click(screen.getByRole("button", { name: "K" }));
    await waitFor(() => {
      const rows = screen.getAllByTestId("player-row");
      expect(rows.every((r) => /\bK\b/.test(visibleText(r)))).toBe(true);
    });
    const kRows = screen.getAllByTestId("player-row");
    for (const row of kRows) {
      const bb = within(row).getAllByTestId("boom-bust");
      expect(bb[0]).toHaveAttribute("data-confidence", "low");
      expect(row).toHaveTextContent("Low confidence");
    }
    await user.click(screen.getAllByRole("button", { name: /^Why/ })[0] as HTMLElement);
    expect(await screen.findByTestId("drivers")).toHaveTextContent(/pts/);
  });

  it("shows no range for a player active in too few simulations", async () => {
    stubWithMock({ mode: "private_test" });
    const user = userEvent.setup();
    render(<ProjectionsView />, { wrapper: Providers });
    await screen.findByTestId("projections-table");
    await user.type(screen.getByLabelText("Search players"), "Zane Ellery");
    expect(await screen.findByText("Too few simulations for a range", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByText(/few simulations/).length).toBeGreaterThanOrEqual(2);
  });

  it("private_test without a snapshot: shows the no-snapshot state", async () => {
    stubWithMock({ mode: "private_test", snapshot: false });
    render(<ProjectionsView />, { wrapper: Providers });
    const state = await screen.findByTestId("no-private-test-snapshot");
    expect(within(state).getByTestId("not-approved-label")).toBeInTheDocument();
  });
});
