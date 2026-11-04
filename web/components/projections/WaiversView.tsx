"use client";

import { useState } from "react";
import { ApiStateView } from "@/components/states/ApiStateView";
import { EmptyState } from "@/components/states/States";
import { api } from "@/lib/api/client";
import { useApi } from "@/lib/api/hooks";
import type { League, LeagueTeam, Position, WaiversResponse } from "@/lib/api/types";
import { niceMax, pts, signedPts } from "@/lib/format";
import { BoomBust } from "./BoomBust";
import { Disclaimer } from "./Disclaimer";
import { DistributionStrip } from "./DistributionStrip";
import { FilterBar, PositionFilter } from "./Filters";
import { GatedView } from "./GatedView";
import { PlayerMeta, PositionTag } from "./PlayerBits";
import { ProjectionNotice } from "./ProjectionNotice";
import { ScoringNote } from "./ScoringNote";

export function WaiversView() {
  return (
    <GatedView feature="Waiver rankings" league="team">
      {({ league, myTeam }) => <WaiversBody league={league as League} myTeam={myTeam as LeagueTeam} />}
    </GatedView>
  );
}

function WaiversBody({ league, myTeam }: { league: League; myTeam: LeagueTeam }) {
  const [position, setPosition] = useState<Position | null>(null);
  const key = JSON.stringify({ l: league.id, t: myTeam.id, position });
  const { result, previous, loading, reload } = useApi<WaiversResponse>(key, (signal) =>
    api.waivers(league.id, { league_team_id: myTeam.id, position: position ?? undefined, limit: 25 }, { signal }),
  );
  const shown = result ?? (previous?.kind === "ok" ? previous : null);

  return (
    <div>
      <p className="mb-4 text-sm text-muted">
        For <span className="font-semibold text-ink">{myTeam.team_name}</span> in {league.name}.
      </p>
      <FilterBar>
        <PositionFilter value={position} onChange={setPosition} />
      </FilterBar>
      <div aria-busy={loading} className={loading && shown ? "opacity-60 transition-opacity" : undefined}>
        <ApiStateView result={shown} onRetry={reload} feature="Waiver rankings" loadingLabel="Loading waiver options">
          {(data, ok) => <WaiversResult data={data} approval={ok.approval} />}
        </ApiStateView>
      </div>
    </div>
  );
}

function WaiversResult({ data, approval }: { data: WaiversResponse; approval: Parameters<typeof ProjectionNotice>[0]["approval"] }) {
  const max = niceMax(Math.max(...data.options.map((o) => o.add.ceiling ?? 0)));
  const first = data.weeks[0];
  const last = data.weeks[data.weeks.length - 1];
  return (
    <div>
      <ProjectionNotice approval={approval} />
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">
          Week {data.week}
          {first !== undefined && last !== undefined ? <span className="font-normal text-muted"> · rest of season weeks {first}-{last}</span> : null}
        </h2>
        {data.open_roster_spot ? <p className="text-sm font-medium">You have an open roster spot: no drop needed.</p> : null}
      </div>
      <ScoringNote scoring={data.scoring} />

      {data.options.length ? (
        <ol className="space-y-3" data-testid="waiver-options">
          {data.options.map((o, i) => (
            <li key={`${o.add.player_id}-${o.drop?.player_id ?? "none"}`} className="card grid gap-4 p-4 md:grid-cols-[2rem_1fr_1fr_13rem]">
              <span className="num text-lg font-bold text-muted" aria-hidden="true">
                {i + 1}
              </span>
              <div className="space-y-2">
                <p>
                  <span className="text-xs font-semibold uppercase tracking-wide text-muted">Add</span>{" "}
                  <span className="font-semibold">{o.add.name}</span>
                </p>
                <PlayerMeta p={o.add} />
                <DistributionStrip floor={o.add.floor} median={o.add.median} ceiling={o.add.ceiling} max={max} compact />
                <BoomBust boom={o.add.boom_prob} bust={o.add.bust_prob} confidence={o.add.boom_bust_confidence} position={o.add.position} />
              </div>
              <div className="space-y-1">
                {o.drop ? (
                  <>
                    <p>
                      <span className="text-xs font-semibold uppercase tracking-wide text-muted">Drop</span>{" "}
                      <span className="font-semibold">{o.drop.name}</span>
                    </p>
                    <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
                      <PositionTag position={o.drop.position} />
                      <span>{o.drop.team ?? "FA"}</span>
                      <span>lineup loss {pts(o.drop.lineup_loss)} pts</span>
                    </p>
                  </>
                ) : (
                  <p className="text-sm">No drop needed: uses an open roster spot.</p>
                )}
                <p className="pt-1 text-sm">{o.reason}</p>
              </div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm md:grid-cols-1">
                <div>
                  <dt className="text-xs text-muted">Next week</dt>
                  <dd className="num font-semibold">{signedPts(o.gain_next_week)} pts</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Rest of season</dt>
                  <dd className="num font-semibold">{signedPts(o.gain_rest_of_season)} pts</dd>
                </div>
                {o.starts != null ? (
                  <div>
                    <dt className="text-xs text-muted">Would start</dt>
                    <dd className="num font-semibold">
                      {o.starts} of {data.weeks.length} weeks
                    </dd>
                  </div>
                ) : null}
              </dl>
            </li>
          ))}
        </ol>
      ) : (
        <EmptyState title="No free agent would improve your lineup">
          <p>None of the available players adds expected lineup points over your current roster. Try another position.</p>
        </EmptyState>
      )}

      {data.drop_candidates.length ? (
        <section aria-labelledby="drop-heading" className="mt-6">
          <h3 id="drop-heading" className="mb-2 font-semibold">
            Easiest players to drop
          </h3>
          <div className="card overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Player</th>
                  <th scope="col">Team</th>
                  <th scope="col" className="text-right">
                    Lineup points lost
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.drop_candidates.map((d) => (
                  <tr key={d.player_id}>
                    <td>
                      <span className="font-medium">{d.name}</span> <PositionTag position={d.position} />
                    </td>
                    <td>{d.team ?? "FA"}</td>
                    <td className="num text-right">{pts(d.lineup_loss)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <Disclaimer text={data.disclaimer} />
    </div>
  );
}
