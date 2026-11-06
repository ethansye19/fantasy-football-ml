"use client";

import { useStatus } from "./StatusProvider";

const DEFAULT_TEXT: Record<string, string> = {
  offseason:
    "No regular-season games remain. Weekly features are paused and rankings are season-level until next season.",
  postseason:
    "The regular season is over. Weekly projections, start/sit and waivers are paused; rankings are season-level until next season.",
  unknown: "The schedule is not available yet, so the current week cannot be determined.",
};

const TITLES: Record<string, string> = {
  offseason: "Offseason.",
  postseason: "Postseason.",
  unknown: "Season status unknown.",
};

/**
 * Season notice from /meta/season, keyed on `mode`. The API's `offseason`
 * flag is true in the postseason and offseason and null when the mode is
 * unknown (v1.3), so it is not used here. When the endpoint is missing or
 * fails, nothing is shown.
 */
export function SeasonBanner() {
  const { season } = useStatus();
  if (!season) return null;
  const mode = season.mode;
  if (mode !== "offseason" && mode !== "postseason" && mode !== "unknown") return null;
  const text = (season.banner?.trim() || DEFAULT_TEXT[mode] || "").replace(/^(Offseason|Postseason):\s*/i, "");
  return (
    <div
      role="region"
      aria-label="Season status"
      data-testid="season-banner"
      data-mode={mode}
      className="border-b border-line bg-surface-2 text-ink"
    >
      <p className="mx-auto max-w-6xl px-4 py-2 text-sm">
        <strong>{TITLES[mode]}</strong> {text}
      </p>
    </div>
  );
}
