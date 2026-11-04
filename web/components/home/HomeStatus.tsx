"use client";

import Link from "next/link";
import { useStatus } from "@/components/status/StatusProvider";
import type { ApprovalMode } from "@/components/status/approval-mode";
import { useLeague } from "@/lib/league";
import { NAV_ITEMS } from "@/lib/nav";

function projectionChip(mode: ApprovalMode): { text: string; className: string } {
  switch (mode.kind) {
    case "no_approved_model":
      return { text: "Off: no approved model", className: "border-info-line bg-info-bg text-info-ink" };
    case "not_approved":
      return { text: "Test data: not approved", className: "border-warn-line bg-warn-bg text-warn-ink" };
    case "approved":
      return { text: "Available", className: "border-line bg-accent-soft text-ink" };
    case "unavailable":
      return { text: "Status unavailable", className: "border-line bg-surface-2 text-ink" };
    default:
      return { text: "Checking…", className: "border-line bg-surface-2 text-muted" };
  }
}

export function FeatureGrid() {
  const { approval } = useStatus();
  const chip = projectionChip(approval);
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {NAV_ITEMS.filter((i) => i.href !== "/about").map((item) => (
        <li key={item.href}>
          <Link
            href={item.href}
            className="card flex h-full flex-col gap-2 p-4 transition-colors hover:border-line-strong"
          >
            <span className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold">{item.label}</span>
              {item.projection ? (
                <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${chip.className}`}>{chip.text}</span>
              ) : (
                <span className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs font-semibold text-ink">
                  Works now
                </span>
              )}
            </span>
            <span className="text-sm text-muted">{item.description}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function modelLine(mode: ApprovalMode): string {
  switch (mode.kind) {
    case "loading":
      return "Checking…";
    case "no_approved_model":
      return "No approved model. Projections are off.";
    case "not_approved":
      return mode.serving ? "Private test data, not approved." : "Private test environment, nothing served.";
    case "approved":
      return mode.liveModelVersion ? `Live model ${mode.liveModelVersion}.` : "Live model published.";
    case "unavailable":
      return "Unavailable.";
  }
}

export function StatusSummary() {
  const { approval, season } = useStatus();
  const { league, leagueId, hydrated, loading } = useLeague();

  let seasonText = "Season status unavailable.";
  if (season) {
    const year = season.season ? `${season.season} ` : "";
    if (season.mode === "regular" && season.projection_week) {
      seasonText = `${year}season, week ${season.projection_week}${season.week_in_progress ? " (in progress)" : ""}.`;
    } else if (season.mode === "unknown" || season.mode === "regular") {
      seasonText = "Schedule not loaded yet.";
    } else {
      seasonText = `${year}${season.mode}.`;
    }
  }

  let leagueText = "No league yet.";
  if (!hydrated || (leagueId && loading)) leagueText = "Checking…";
  else if (league) leagueText = `${league.name} (${league.num_teams} teams, ${league.season}).`;
  else if (leagueId) leagueText = "Saved league could not be loaded.";

  return (
    <section aria-labelledby="status-heading" className="card p-4 sm:p-5">
      <h2 id="status-heading" className="mb-3 text-base font-semibold">
        Current status
      </h2>
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-muted">Model</dt>
          <dd className="font-medium" data-testid="status-model">
            {modelLine(approval)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Season</dt>
          <dd className="font-medium" data-testid="status-season">
            {seasonText}
          </dd>
        </div>
        <div>
          <dt className="text-muted">League</dt>
          <dd className="font-medium" data-testid="status-league">
            {leagueText}{" "}
            <Link href="/setup" className="link font-normal">
              {league ? "Change" : "Set up"}
            </Link>
          </dd>
        </div>
      </dl>
    </section>
  );
}
