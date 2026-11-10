/**
 * Wording for the current season and week from /meta/season (D-124).
 * The API takes them from Sleeper, cross-checked with the nflverse schedule;
 * the web app never derives them from the calendar.
 */
import type { RunInfo, SeasonResponse } from "./api/types";

/** "2026 season, week 9 (in progress)", "2026 postseason", or null when unknown. */
export function seasonLabel(season: SeasonResponse | null | undefined): string | null {
  if (!season || season.mode === "unknown" || !season.season) return null;
  const week = season.week ?? season.projection_week ?? null;
  if (season.mode === "regular") {
    if (season.season_type === "preseason") return `${season.season} preseason, week 1 next`;
    if (!week) return `${season.season} regular season`;
    return `${season.season} season, week ${week}${season.week_in_progress ? " (in progress)" : ""}`;
  }
  return `${season.season} ${season.mode}`;
}

/** Plain-language source of the answer, including the cross-check result. */
export function seasonSourceText(season: SeasonResponse | null | undefined): string | null {
  if (!season || !season.source || season.source === "none") return null;
  const check = season.cross_check;
  switch (check?.status) {
    case "agree":
      return "From Sleeper, matching the nflverse schedule.";
    case "disagree": {
      const sw = check.sleeper?.week;
      const extra = check.differences.includes("week") && sw ? ` (Sleeper shows week ${sw})` : "";
      return `From the nflverse schedule: Sleeper disagrees${extra}, and the schedule is used when they differ.`;
    }
    case "sleeper_unavailable":
      return /not used/i.test(check.message)
        ? "From the nflverse schedule (Sleeper is not used here)."
        : "From the nflverse schedule: Sleeper could not be reached.";
    case "schedule_unavailable":
      return "From Sleeper; the nflverse schedule is not loaded, so it is not cross-checked.";
    default:
      return season.source === "sleeper" ? "From Sleeper." : "From the nflverse schedule.";
  }
}

/** The "TEST DATA: ..." label of a replay or fixture run, if any (API v1.5, D-124). */
export function testDataLabel(run: RunInfo | null | undefined): string | null {
  if (!run) return null;
  if (run.test_data_label) return run.test_data_label;
  if (run.is_replay) return `TEST DATA: ${run.season} week ${run.week} replay, not approved, not a recommendation`;
  if (run.source === "fixture") return "TEST DATA: fixture data, not approved, not a recommendation";
  return null;
}
