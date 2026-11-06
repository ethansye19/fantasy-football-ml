"use client";

import { Fragment, useState } from "react";
import { ApiStateView } from "@/components/states/ApiStateView";
import { EmptyState } from "@/components/states/States";
import { api } from "@/lib/api/client";
import { useApi } from "@/lib/api/hooks";
import type { Availability, League, Position, ProjectionSort, ProjectionsResponse } from "@/lib/api/types";
import { niceMax, pts } from "@/lib/format";
import { BoomBust, LowConfidenceNote } from "./BoomBust";
import { DistributionStrip, StripScale } from "./DistributionStrip";
import { Drivers } from "./Drivers";
import { FilterBar, PositionFilter, SearchFilter, SelectFilter, useDebounced } from "./Filters";
import { GatedView } from "./GatedView";
import { PlayerMeta } from "./PlayerBits";
import { ProjectionNotice } from "./ProjectionNotice";
import { ScoringNote } from "./ScoringNote";

const PAGE = 50;

const SORTS: { value: ProjectionSort; label: string }[] = [
  { value: "median", label: "Median" },
  { value: "mean", label: "Average" },
  { value: "ceiling", label: "Ceiling" },
  { value: "floor", label: "Floor" },
  { value: "boom", label: "Boom chance" },
  { value: "bust", label: "Bust chance (lowest first)" },
];

const AVAILABILITY: { value: Availability; label: string }[] = [
  { value: "all", label: "All players" },
  { value: "free_agents", label: "Free agents" },
  { value: "rostered", label: "Rostered" },
  { value: "mine", label: "My team" },
];

export function ProjectionsView() {
  return (
    <GatedView feature="Weekly projections" league="optional">
      {({ league }) => <ProjectionsBody league={league} />}
    </GatedView>
  );
}

function ProjectionsBody({ league }: { league: League | null }) {
  const [position, setPosition] = useState<Position | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ProjectionSort>("median");
  const [availability, setAvailability] = useState<Availability>("all");
  const [scoring, setScoring] = useState<"ppr" | "half" | "std">("ppr");
  const [week, setWeek] = useState<number | undefined>(undefined);
  const [limit, setLimit] = useState(PAGE);
  const q = useDebounced(search.trim(), 300);

  const query = { position: position ?? undefined, search: q || undefined, sort, week, limit, ...(league ? { availability } : {}) };
  const key = JSON.stringify({ l: league?.id ?? null, s: league ? null : scoring, ...query });
  const { result, previous, loading, reload } = useApi<ProjectionsResponse>(key, (signal) =>
    league ? api.projections.league(league.id, query, { signal }) : api.projections.preset(scoring, query, { signal }),
  );

  // Keep the last table on screen while a filter reloads.
  const shown = result ?? (previous?.kind === "ok" ? previous : null);
  const resetPage = () => setLimit(PAGE);

  return (
    <div>
      <FilterBar>
        <PositionFilter
          value={position}
          onChange={(p) => {
            setPosition(p);
            resetPage();
          }}
        />
        <SearchFilter
          value={search}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <SelectFilter label="Sort by" value={sort} options={SORTS} onChange={(v) => setSort(v)} />
        {league ? (
          <SelectFilter
            label="Players"
            value={availability}
            options={AVAILABILITY}
            onChange={(v) => {
              setAvailability(v);
              resetPage();
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
      </FilterBar>

      <div aria-busy={loading} className={loading && shown ? "opacity-60 transition-opacity" : undefined}>
        <ApiStateView
          result={shown}
          onRetry={reload}
          feature="Weekly projections"
          loadingLabel="Loading projections"
          renderError={(r) => {
            if (r.code !== "week_not_available") return undefined;
            const weeks = (r.details as { available_weeks?: number[] } | null)?.available_weeks ?? [];
            return (
              <EmptyState
                title={`Week ${week} is not in this run`}
                actions={weeks.map((w) => (
                  <button key={w} type="button" className="btn btn-secondary" onClick={() => setWeek(w)}>
                    Week {w}
                  </button>
                ))}
              >
                <p>Pick one of the weeks this run covers.</p>
              </EmptyState>
            );
          }}
        >
          {(data, ok) => (
            <>
              <ProjectionNotice approval={ok.approval} />
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-lg font-semibold">
                  Week {data.week}, {data.season}
                  {league ? <span className="font-normal text-muted"> · {league.name}</span> : <span className="font-normal text-muted"> · {scoring.toUpperCase()} scoring</span>}
                </h2>
                <p className="text-sm text-muted" role="status">
                  {data.total.toLocaleString()} players
                </p>
              </div>
              <ScoringNote scoring={data.scoring} />
              <LowConfidenceNote positions={data.boom_bust_low_confidence} />
              {data.players.length ? (
                <ProjectionTable data={data} showRoster={Boolean(league)} />
              ) : (
                <EmptyState title="No players match">
                  <p>Try another position, clear the search, or show all players.</p>
                </EmptyState>
              )}
              {data.players.length < data.total ? (
                <div className="mt-4 flex justify-center">
                  <button type="button" className="btn btn-secondary" onClick={() => setLimit((l) => Math.min(500, l + PAGE))} disabled={limit >= 500 || loading}>
                    {loading ? "Loading…" : `Show more (${data.players.length} of ${data.total})`}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </ApiStateView>
      </div>
    </div>
  );
}

function ProjectionTable({ data, showRoster }: { data: ProjectionsResponse; showRoster: boolean }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const max = niceMax(Math.max(...data.players.map((p) => p.ceiling ?? 0)));
  return (
    <div className="card overflow-x-auto">
      <table className="data-table" data-testid="projections-table">
        <caption className="sr-only">
          Weekly projections: floor, median and ceiling of simulated fantasy points, with boom and bust chances
        </caption>
        <thead>
          <tr>
            <th scope="col">Player</th>
            <th scope="col" className="w-[40%]">
              Range
              <StripScale max={max} />
            </th>
            <th scope="col" className="hidden sm:table-cell">
              Boom / bust
            </th>
          </tr>
        </thead>
        <tbody>
          {data.players.map((p) => {
            const isOpen = Boolean(open[p.player_id]);
            const detailId = `why-${p.player_id}`;
            return (
              <Fragment key={p.player_id}>
                <tr data-testid="player-row">
                  <td className="min-w-[7.5rem] sm:min-w-[9rem]">
                    <div className="font-semibold leading-tight">{p.name}</div>
                    <PlayerMeta p={p} />
                    {showRoster ? (
                      <div className="text-xs text-muted">
                        {p.roster ? (p.roster.is_mine ? <span className="font-semibold text-ink">My team</span> : p.roster.team_name) : "Free agent"}
                      </div>
                    ) : null}
                    <button
                      type="button"
                      className="link mt-1 text-xs"
                      aria-expanded={isOpen}
                      aria-controls={detailId}
                      onClick={() => setOpen((o) => ({ ...o, [p.player_id]: !isOpen }))}
                    >
                      {isOpen ? "Hide reasons" : "Why"}
                      <span className="sr-only"> for {p.name}</span>
                    </button>
                  </td>
                  <td>
                    <DistributionStrip floor={p.floor} median={p.median} ceiling={p.ceiling} max={max} />
                    {p.expected != null && p.mean != null && Math.abs(p.expected - p.mean) >= 0.5 ? (
                      <p className="num mt-1 text-[0.6875rem] text-muted">Expected {pts(p.expected)} counting missed games</p>
                    ) : null}
                    <div className="mt-2 sm:hidden">
                      <BoomBust boom={p.boom_prob} bust={p.bust_prob} confidence={p.boom_bust_confidence} position={p.position} lowConfidencePositions={data.boom_bust_low_confidence} />
                    </div>
                  </td>
                  <td className="hidden sm:table-cell">
                    <BoomBust boom={p.boom_prob} bust={p.bust_prob} confidence={p.boom_bust_confidence} position={p.position} lowConfidencePositions={data.boom_bust_low_confidence} stacked />
                  </td>
                </tr>
                <tr id={detailId} hidden={!isOpen}>
                  <td colSpan={3} className="bg-surface-2">
                    {isOpen ? <Drivers drivers={p.drivers} reference={data.scoring.drivers_reference} /> : null}
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
