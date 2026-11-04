"use client";

import { useStatus } from "./StatusProvider";

const DEFAULT_TEXT: Record<string, string> = {
  offseason:
    "Offseason: no regular-season games remain. Weekly features are paused and rankings are season-level until the next schedule is out.",
  postseason:
    "Postseason: the fantasy regular season is over. Weekly features are paused and rankings are season-level.",
};

/**
 * Offseason and postseason notice from /meta/season. When that endpoint is
 * missing or fails, the app treats the mode as unknown and shows nothing.
 */
export function SeasonBanner() {
  const { season } = useStatus();
  if (!season) return null;
  const mode = season.offseason ? "offseason" : season.mode;
  if (mode !== "offseason" && mode !== "postseason") return null;
  const text = season.banner?.trim() || DEFAULT_TEXT[mode];
  return (
    <div
      role="region"
      aria-label="Season status"
      data-testid="season-banner"
      className="border-b border-line bg-surface-2 text-ink"
    >
      <p className="mx-auto max-w-6xl px-4 py-2 text-sm">
        <strong className="capitalize">{mode}.</strong> {text?.replace(/^(Offseason|Postseason):\s*/i, "")}
      </p>
    </div>
  );
}
