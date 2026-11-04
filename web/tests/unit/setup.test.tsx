import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LeagueSettingsForm } from "@/components/setup/LeagueSettingsForm";
import { SleeperImport } from "@/components/setup/SleeperImport";
import { stubWithMock } from "./helpers";

describe("SleeperImport (against the contract mock)", () => {
  it("goes username -> league -> my team", async () => {
    const { fetchMock } = stubWithMock({ mode: "production" });
    const onImported = vi.fn();
    const user = userEvent.setup();
    render(<SleeperImport defaultSeason={2026} onImported={onImported} />);

    await user.type(screen.getByLabelText("Sleeper username"), "fixtureuser");
    await user.click(screen.getByRole("button", { name: "Find my leagues" }));

    const group = await screen.findByRole("group", { name: /Leagues for Fixture User in 2026/ });
    await user.click(within(group).getByRole("radio", { name: /Fixture Office League/ }));
    await user.click(screen.getByRole("button", { name: "Import league" }));

    const teamGroup = await screen.findByRole("group", { name: "Which team is yours?" });
    const mine = within(teamGroup).getByRole("radio", { name: /Team fixtureuser/ });
    expect(mine).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Use this team" }));

    expect(await screen.findByTestId("import-done")).toHaveTextContent("Fixture Office League is ready.");
    expect(onImported).toHaveBeenCalledTimes(1);
    const calls = fetchMock.mock.calls.map(([u, init]) => `${(init as RequestInit | undefined)?.method ?? "GET"} ${String(u)}`);
    expect(calls).toContain("GET /api/sleeper/users/fixtureuser/leagues?season=2026");
    expect(calls).toContain("POST /api/leagues/import/sleeper");
  });

  it("validates the username before calling the API", async () => {
    const { fetchMock } = stubWithMock();
    const user = userEvent.setup();
    render(<SleeperImport defaultSeason={2026} onImported={() => {}} />);
    await user.type(screen.getByLabelText("Sleeper username"), "bad name!");
    await user.click(screen.getByRole("button", { name: "Find my leagues" }));
    expect(await screen.findByTestId("error-summary")).toHaveTextContent("letters, numbers and underscores");
    expect(screen.getByLabelText("Sleeper username")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("explains an unknown Sleeper user", async () => {
    stubWithMock();
    const user = userEvent.setup();
    render(<SleeperImport defaultSeason={2026} onImported={() => {}} />);
    await user.type(screen.getByLabelText("Sleeper username"), "nobody_here");
    await user.click(screen.getByRole("button", { name: "Find my leagues" }));
    expect(await screen.findByTestId("error-summary")).toHaveTextContent("Not found on Sleeper");
  });

  it("explains when Sleeper is down", async () => {
    stubWithMock();
    const user = userEvent.setup();
    render(<SleeperImport defaultSeason={2026} onImported={() => {}} />);
    await user.type(screen.getByLabelText("Sleeper username"), "sleeperdown");
    await user.click(screen.getByRole("button", { name: "Find my leagues" }));
    expect(await screen.findByTestId("error-summary")).toHaveTextContent("Sleeper is not responding");
  });
});

describe("LeagueSettingsForm (create)", () => {
  it("shows field errors and an error summary, then creates the league", async () => {
    stubWithMock();
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<LeagueSettingsForm mode="create" defaultSeason={2026} onSaved={onSaved} />);

    await user.click(screen.getByRole("button", { name: "Create league" }));
    const summary = await screen.findByTestId("error-summary");
    expect(summary).toHaveTextContent("League name: Enter a league name.");
    expect(screen.getByLabelText("League name")).toHaveAttribute("aria-invalid", "true");
    expect(onSaved).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("League name"), "Office league");
    await user.click(screen.getByRole("radio", { name: /Half PPR/ }));
    expect(screen.getByLabelText("Reception")).toHaveValue("0.5");
    await user.click(screen.getByRole("button", { name: "Create league" }));

    expect(await screen.findByText("League created.")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onSaved.mock.calls[0]?.[0]).toMatchObject({ name: "Office league", source: "manual", scoring: { preset_like: "half" } });
  });

  it("blocks a roster without starting slots", async () => {
    stubWithMock();
    const user = userEvent.setup();
    render(<LeagueSettingsForm mode="create" defaultSeason={2026} onSaved={() => {}} />);
    await user.type(screen.getByLabelText("League name"), "x");
    for (const label of ["Quarterback", "Running back", "Wide receiver", "Tight end", "Flex", "Kicker", "Team defense"]) {
      const input = screen.getByLabelText(new RegExp(`^${label}`));
      await user.clear(input);
      await user.type(input, "0");
    }
    await user.click(screen.getByRole("button", { name: "Create league" }));
    expect(await screen.findByTestId("error-summary")).toHaveTextContent("Add at least one starting slot.");
  });
});
