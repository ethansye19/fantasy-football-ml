"use client";

import { useState } from "react";
import { ApiStateView } from "@/components/states/ApiStateView";
import { EmptyState } from "@/components/states/States";
import { api } from "@/lib/api/client";
import { useApi } from "@/lib/api/hooks";
import type { Availability, League, Position, RosResponse } from "@/lib/api/types";
import { niceMax, pts, signedPts } from "@/lib/format";
import { DistributionStrip, StripScale } from "./DistributionStrip";
import { FilterBar, PositionFilter, SelectFilter } from "./Filters";
import { GatedView } from "./GatedView";
import { PositionTag } from "./PlayerBits";
import { ProjectionNotice } from "./ProjectionNotice";
import { ScoringNote } from "./ScoringNote";

const PAGE = 50;

export function RankingsView() {
  return (
    <GatedView feature="Rest-of-season rankings" league="optional">
      {({ league }) => <RankingsBody league={league} />}
    </GatedView>
  );
}

function RankingsBody({ league }: { league: League | null }) {
  const [position, setPosition] = useState<Position | null>(null);
  const [availability, setAvailability] = useState<Availability>("all");
  const [scoring, setScoring] = useState<"ppr" | "half" | "std">("ppr");
  const [includeCurrent, setIncludeCurrent] = useState(true);
  const [limit, setLimit] = useState(PAGE);

  const query = { position: position ?? undefined, limit, include_current_week: includeCurrent, ...(league ? { availability } : {}) };
  const key = JSON.stringify({ l: league?.id ?? null, s: league ? null : scoring, ...query });
  const { result, previous, loading, reload } = useApi<RosResponse>(key, (signal) =>
    league ? api.ros.league(league.id, query, { signal }) : api.ros.preset(scoring, query, { signal }),
  );
  const shown = result ?? (previous?.kind === "ok" ? previous : null);

  return (
    <div>
      <FilterBar>
        <PositionFilter
          value={position}
          onChange={(p) => {
            setPosition(p);
            setLimit(PAGE);
          }}
        />
        {league ? (
          <SelectFilter
            label="Players"
            value={availability}
            options={[
              { value: "all", label: "All players" },
              { value: "free_agents", label: "Free agents" },
              { value: "rostered", label: "Rostered" },
              { value: "mine", label: "My team" },
            ]}
            onChange={(v) => {
              setAvailability(v);
              setLimit(PAGE);
            }}
          />
        ) : (
          <SelectFilter
            label="Scoring"
            value={scoring}
            options={[
              { value: "ppr", label: "PPR" },
              { value: "half", label: "Half PPR" },
              { value: "std", label: "Standard" },
            ]}
            onChange={(v) => setScoring(v)}
          />
        )}
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={includeCurrent} onChange={(e) => setIncludeCurrent(e.target.checked)} />
          Include this week
        </label>
      </FilterBar>

      <div aria-busy={loading} className={loading && shown ? "opacity-60 transition-opacity" : undefined}>
        <ApiStateView result={shown} onRetry={reload} feature="Rest-of-season rankings" loadingLabel="Loading rankings">
          {(data, ok) => (
            <>
              <ProjectionNotice approval={ok.approval} />
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-lg font-semibold">
                  Weeks {data.first_week}-{data.last_week}
                  <span className="font-normal text-muted"> · {league ? league.name : `${scoring.toUpperCase()} scoring, 12-team default`}</span>
                </h2>
                {data.total != null ? (
                  <p className="text-sm text-muted" role="status">
                    {data.total.toLocaleString()} players
                  </p>
                ) : null}
              </div>
              <ScoringNote scoring={data.scoring} />
              {data.players.length ? <RosTable data={data} showRoster={Boolean(league)} /> : (
                <EmptyState title="No players match">
                  <p>Try another position or show all players.</p>
                </EmptyState>
              )}
              {data.total != null && data.players.length < data.total ? (
                <div className="mt-4 flex justify-center">
                  <button type="button" className="btn btn-secondary" onClick={() => setLimit((l) => Math.min(500, l + PAGE))} disabled={limit >= 500 || loading}>
                    {loading ? "Loading…" : `Show more (${data.players.length} of ${data.total})`}
                  </button>
                </div>
              ) : null}
              {data.replacement_level ? (
                <p className="mt-4 text-xs text-muted">
                  Replacement level:{" "}
                  {Object.entries(data.replacement_level)
                    .map(([pos, v]) => `${pos} ${pts(v)}`)
                    .join(" · ")}{" "}
                  rest-of-season points.
                </p>
              ) : null}
            </>
          )}
        </ApiStateView>
      </div>
    </div>
  );
}

function RosTable({ data, showRoster }: { data: RosResponse; showRoster: boolean }) {
  const max = niceMax(Math.max(...data.players.map((p) => p.ros_ceiling)));
  return (
    <div className="card overflow-x-auto">
      <table className="data-table" data-testid="ros-table">
        <caption className="sr-only">Rest-of-season rankings with the range of simulated season totals</caption>
        <thead>
          <tr>
            <th scope="col" className="w-10 text-right">
              #
            </th>
            <th scope="col">Player</th>
            <th scope="col" className="w-[38%]">
              Rest of season
              <StripScale max={max} />
            </th>
            <th scope="col" className="hidden text-right sm:table-cell">
              Over repl.
            </th>
            <th scope="col" className="hidden text-right md:table-cell">
              Games
            </th>
          </tr>
        </thead>
        <tbody>
          {data.players.map((p) => (
            <tr key={p.player_id} data-testid="ros-row">
              <td className="num text-right font-semibold">{p.overall_rank}</td>
              <td className="min-w-[7.5rem]">
                <div className="font-semibold leading-tight">{p.name}</div>
                <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                  <PositionTag position={p.position} rank={p.position_rank} />
                  <span>{p.team ?? "FA"}</span>
                  {p.bye_weeks.length ? <span>bye {p.bye_weeks.join(", ")}</span> : null}
                </div>
                {showRoster ? (
                  <div className="text-xs text-muted">
                    {p.roster ? (p.roster.is_mine ? <span className="font-semibold text-ink">My team</span> : p.roster.team_name) : "Free agent"}
                  </div>
                ) : null}
              </td>
              <td>
                <DistributionStrip floor={p.ros_floor} median={p.ros_median} ceiling={p.ros_ceiling} max={max} compact />
                <p className="num mt-1 text-xs text-muted sm:hidden">
                  {signedPts(p.value_over_replacement)} over replacement · {p.games_remaining} games
                </p>
              </td>
              <td className="num hidden text-right sm:table-cell">{signedPts(p.value_over_replacement)}</td>
              <td className="num hidden text-right md:table-cell">{p.games_remaining}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
