"use client";

import { seasonLabel, seasonSourceText } from "@/lib/season";
import { useStatus } from "./StatusProvider";

/**
 * The current season and week on every page (D-124), from /meta/season:
 * Sleeper's state, cross-checked with the nflverse schedule. Shows nothing
 * while loading or when the season is unknown (SeasonBanner covers that).
 */
export function SeasonContext() {
  const { season } = useStatus();
  const label = seasonLabel(season);
  if (!season || !label) return null;
  const source = seasonSourceText(season);
  return (
    <div
      role="region"
      aria-label="Current season"
      data-testid="season-context"
      data-source={season.source ?? "unknown"}
      data-cross-check={season.cross_check?.status ?? "unknown"}
      className="border-b border-line bg-surface text-ink"
    >
      <p className="mx-auto flex max-w-6xl flex-wrap items-baseline gap-x-2 px-4 py-1.5 text-xs sm:text-sm">
        <span>
          Now: <strong data-testid="season-context-label">{label}</strong>
        </span>
        {source ? <span className="text-muted">{source}</span> : null}
      </p>
    </div>
  );
}
