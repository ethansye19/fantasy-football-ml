"use client";

import type { ReactNode } from "react";
import { ApiStateView } from "@/components/states/ApiStateView";
import {
  LoadingState,
  NeedsLeagueState,
  NeedsTeamState,
  NoApprovedModelState,
  NoPrivateSnapshotState,
} from "@/components/states/States";
import { useStatus } from "@/components/status/StatusProvider";
import type { League, LeagueTeam } from "@/lib/api/types";
import { useLeague } from "@/lib/league";
import { seasonLabel } from "@/lib/season";

/**
 * Wraps every projection-derived view. Production without an approved model
 * shows the "No approved model" state before any request; a private test
 * environment without a snapshot shows its own state. Optional league and
 * "my team" requirements come next. Requests made inside still map their
 * own results through ApiStateView, so a gate that closes later is handled.
 */
export function GatedView({
  feature,
  league: leagueNeed = "none",
  children,
}: {
  feature: string;
  league?: "none" | "optional" | "required" | "team";
  children: (ctx: { league: League | null; myTeam: LeagueTeam | null }) => ReactNode;
}) {
  const { approval, season } = useStatus();
  const { hydrated, leagueId, league, myTeam, result, loading, reload } = useLeague();

  if (approval.kind === "loading") return <LoadingState label="Checking the model status" />;
  if (approval.kind === "no_approved_model") {
    return <NoApprovedModelState message={approval.message} feature={feature} season={seasonLabel(season)} />;
  }
  if (approval.kind === "not_approved" && !approval.serving && approval.reason === "no_private_test_snapshot") {
    return <NoPrivateSnapshotState message={approval.message} label={approval.label} />;
  }

  if (leagueNeed !== "none") {
    if (!hydrated || (leagueId && loading)) return <LoadingState label="Loading your league" />;
    if (leagueId && result && result.kind !== "ok") {
      if (leagueNeed === "optional") return <>{children({ league: null, myTeam: null })}</>;
      return <ApiStateView result={result} onRetry={reload} feature={feature}>{() => null}</ApiStateView>;
    }
    if (!league) {
      if (leagueNeed === "optional") return <>{children({ league: null, myTeam: null })}</>;
      return <NeedsLeagueState feature={feature} />;
    }
    if (leagueNeed === "team" && !myTeam) return <NeedsTeamState feature={feature} />;
  }
  return <>{children({ league, myTeam })}</>;
}
