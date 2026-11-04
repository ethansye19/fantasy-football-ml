"use client";

import { Tabs } from "@/components/forms/Tabs";
import { ApiStateView } from "@/components/states/ApiStateView";
import { EmptyState } from "@/components/states/States";
import { useStatus } from "@/components/status/StatusProvider";
import { api } from "@/lib/api/client";
import { useApi } from "@/lib/api/hooks";
import type { League } from "@/lib/api/types";
import { useLeague } from "@/lib/league";
import { LeagueList } from "./LeagueList";
import { LeagueSettingsForm } from "./LeagueSettingsForm";
import { MyTeamPicker } from "./MyTeamPicker";
import { SleeperImport } from "./SleeperImport";

function fallbackSeason(): number {
  const now = new Date();
  // January and February still belong to the previous season.
  return now.getMonth() < 2 ? now.getFullYear() - 1 : now.getFullYear();
}

export function SetupView() {
  const { season } = useStatus();
  const { leagueId, league, setLeagueId, reload: reloadLeague } = useLeague();
  const list = useApi<{ leagues: League[] }>("leagues", (signal) => api.leagues.list({ signal }));
  const defaultSeason = season?.season ?? fallbackSeason();

  const adopt = (l: League) => {
    setLeagueId(l.id);
    reloadLeague();
    list.reload();
  };

  return (
    <div className="space-y-10">
      <section aria-labelledby="leagues-heading" className="space-y-3">
        <h2 id="leagues-heading" className="text-xl font-semibold">
          Your leagues
        </h2>
        <ApiStateView result={list.result} onRetry={list.reload} loadingLabel="Loading leagues">
          {(data) =>
            data.leagues.length ? (
              <LeagueList
                leagues={data.leagues}
                currentId={leagueId}
                onUse={(id) => setLeagueId(id)}
                onChanged={(l) => {
                  if (l.id === leagueId) reloadLeague();
                  list.reload();
                }}
                onDeleted={(id) => {
                  if (id === leagueId) setLeagueId(null);
                  list.reload();
                }}
              />
            ) : (
              <EmptyState title="No leagues yet">
                <p>Import a league from Sleeper or enter your league&apos;s settings by hand below.</p>
              </EmptyState>
            )
          }
        </ApiStateView>
      </section>

      {league ? (
        <section aria-labelledby="current-heading" className="space-y-6">
          <h2 id="current-heading" className="text-xl font-semibold">
            {league.name}
          </h2>
          <div className="card p-4 sm:p-5">
            <MyTeamPicker
              key={`${league.id}-${league.my_team_id ?? "none"}`}
              league={league}
              onSaved={() => {
                reloadLeague();
                list.reload();
              }}
            />
          </div>
          <details className="card p-4 sm:p-5" data-testid="league-settings">
            <summary className="cursor-pointer text-lg font-semibold">Scoring and roster settings</summary>
            <p className="mt-2 text-sm text-muted">
              Changes apply to every view of this league. Projections are re-scored with these rules; nothing is retrained.
            </p>
            <div className="mt-5">
              <LeagueSettingsForm
                key={`${league.id}-${league.updated_at}`}
                mode="edit"
                league={league}
                defaultSeason={defaultSeason}
                onSaved={() => {
                  reloadLeague();
                  list.reload();
                }}
              />
            </div>
          </details>
        </section>
      ) : null}

      <section aria-labelledby="add-heading" className="space-y-3">
        <h2 id="add-heading" className="text-xl font-semibold">
          Add a league
        </h2>
        <div className="card p-4 sm:p-6">
          <Tabs
            label="How to add a league"
            tabs={[
              {
                id: "sleeper",
                label: "Import from Sleeper",
                content: <SleeperImport defaultSeason={defaultSeason} onImported={adopt} />,
              },
              {
                id: "manual",
                label: "Enter settings by hand",
                content: <LeagueSettingsForm mode="create" defaultSeason={defaultSeason} onSaved={adopt} />,
              },
            ]}
          />
        </div>
      </section>
    </div>
  );
}
