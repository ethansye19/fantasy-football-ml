"use client";

import { useState } from "react";
import { ApiStateView } from "@/components/states/ApiStateView";
import { EmptyState } from "@/components/states/States";
import { api } from "@/lib/api/client";
import { useApi, useMutation } from "@/lib/api/hooks";
import { playerNotFoundIds } from "@/lib/api/result";
import type { League, LeagueTeam, ProjectionsResponse, StartSitResponse } from "@/lib/api/types";
import { niceMax, pct } from "@/lib/format";
import { BoomBust, LowConfidenceNote } from "./BoomBust";
import { ChanceBars } from "./ChanceBars";
import { Disclaimer } from "./Disclaimer";
import { DistributionStrip } from "./DistributionStrip";
import { Drivers } from "./Drivers";
import { SearchFilter, useDebounced } from "./Filters";
import { GatedView } from "./GatedView";
import { PlayerMeta, PositionTag } from "./PlayerBits";
import { ProjectionNotice } from "./ProjectionNotice";

const MAX = 4;

interface Pick {
  id: string;
  name: string;
  position: string;
  team: string | null;
}

const STRENGTH: Record<string, string> = { "toss-up": "Toss-up", lean: "Lean", clear: "Clear edge" };

export function StartSitView() {
  return (
    <GatedView feature="Start/sit" league="required">
      {({ league, myTeam }) => <StartSitBody league={league as League} myTeam={myTeam} />}
    </GatedView>
  );
}

function StartSitBody({ league, myTeam }: { league: League; myTeam: LeagueTeam | null }) {
  const [picks, setPicks] = useState<Pick[]>([]);
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 300);
  const compare = useMutation((ids: string[]) => api.startSit(league.id, { player_ids: ids }));

  const found = useApi<ProjectionsResponse>(q.length >= 2 ? `ss-search:${league.id}:${q}` : null, (signal) =>
    api.projections.league(league.id, { search: q, limit: 8 }, { signal }),
  );

  const add = (p: Pick) => {
    setPicks((cur) => (cur.length >= MAX || cur.some((c) => c.id === p.id) ? cur : [...cur, p]));
    compare.reset();
  };
  const remove = (id: string) => {
    setPicks((cur) => cur.filter((c) => c.id !== id));
    compare.reset();
  };

  // Quick picks: my roster players that this run projects (others would be
  // refused with player_not_found, for example rookies in a historical replay).
  const mine = useApi<ProjectionsResponse>(myTeam ? `ss-mine:${league.id}:${myTeam.id}` : null, (signal) =>
    api.projections.league(league.id, { availability: "mine", limit: 60 }, { signal }),
  );
  const roster =
    mine.result?.kind === "ok" ? mine.result.data.players.map((p) => ({ player_id: p.player_id, name: p.name, position: p.position, team: p.team })) : [];
  const unprojected = mine.result?.kind === "ok" ? (myTeam?.players ?? []).filter((p) => p.slot !== "ir").length - roster.length : 0;
  const full = picks.length >= MAX;

  return (
    <div className="space-y-6">
      <section aria-labelledby="ss-pick" className="card space-y-4 p-4 sm:p-5">
        <h2 id="ss-pick" className="text-lg font-semibold">
          Choose 2 to 4 players
        </h2>

        <div aria-live="polite">
          {picks.length ? (
            <ul className="flex flex-wrap gap-2" aria-label="Chosen players">
              {picks.map((p) => (
                <li key={p.id} className="flex items-center gap-2 rounded-full border border-accent bg-accent-soft py-1 pl-3 pr-1 text-sm">
                  <span className="font-medium">{p.name}</span>
                  <PositionTag position={p.position} />
                  <button type="button" className="btn btn-ghost h-7 min-h-0 w-7 rounded-full p-0" aria-label={`Remove ${p.name}`} onClick={() => remove(p.id)}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No players chosen yet.</p>
          )}
        </div>

        {roster.length ? (
          <fieldset>
            <legend className="mb-2 text-sm font-medium">From {myTeam?.team_name ?? "my team"}</legend>
            <div className="flex flex-wrap gap-1.5">
              {roster.map((p) => {
                const chosen = picks.some((c) => c.id === p.player_id);
                return (
                  <button
                    key={p.player_id}
                    type="button"
                    aria-pressed={chosen}
                    disabled={!chosen && full}
                    onClick={() => (chosen ? remove(p.player_id) : add({ id: p.player_id, name: p.name, position: p.position, team: p.team }))}
                    className={`rounded-md border px-2 py-1 text-sm ${chosen ? "border-accent bg-accent-soft font-semibold" : "border-line-strong bg-surface hover:bg-surface-2"} disabled:opacity-50`}
                  >
                    {p.name} <span className="text-xs text-muted">{p.position}</span>
                  </button>
                );
              })}
            </div>
            {unprojected > 0 ? (
              <p className="mt-2 text-xs text-muted">
                {unprojected} {unprojected === 1 ? "player on your roster has" : "players on your roster have"} no projection in this run.
              </p>
            ) : null}
          </fieldset>
        ) : null}

        <div>
          <SearchFilter value={search} onChange={setSearch} label="Find any player" />
          {q.length >= 2 && found.result?.kind === "ok" ? (
            found.result.data.players.length ? (
              <ul className="mt-2 grid gap-1 sm:grid-cols-2" aria-label="Search results">
                {found.result.data.players.map((p) => {
                  const chosen = picks.some((c) => c.id === p.player_id);
                  return (
                    <li key={p.player_id}>
                      <button
                        type="button"
                        disabled={chosen || full}
                        onClick={() => add({ id: p.player_id, name: p.name, position: p.position, team: p.team })}
                        className="flex w-full items-center justify-between gap-2 rounded-md border border-line bg-surface px-3 py-2 text-left text-sm hover:bg-surface-2 disabled:opacity-50"
                      >
                        <span>
                          <span className="font-medium">Add {p.name}</span>
                          <span className="block">
                            <PlayerMeta p={p} />
                          </span>
                        </span>
                        <span className="text-xs text-muted">{chosen ? "Chosen" : p.roster ? (p.roster.is_mine ? "Mine" : p.roster.team_name) : "Free agent"}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted">No players match “{q}”.</p>
            )
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn-primary"
            disabled={picks.length < 2 || compare.pending}
            onClick={() => void compare.mutate(picks.map((p) => p.id))}
          >
            {compare.pending ? "Comparing…" : "Compare"}
          </button>
          {picks.length < 2 ? <p className="text-sm text-muted">Choose at least two players.</p> : null}
          {full ? <p className="text-sm text-muted">Four players is the most.</p> : null}
        </div>
      </section>

      {compare.result ? (
        <section aria-labelledby="ss-result" aria-live="polite">
          <h2 id="ss-result" className="sr-only">
            Comparison
          </h2>
          <ApiStateView
            result={compare.result}
            feature="Start/sit"
            onRetry={() => void compare.mutate(picks.map((p) => p.id))}
            renderError={(r) => {
              // 422 player_not_found with details.player_ids (contract v1.3)
              const ids = playerNotFoundIds(r);
              if (!ids.length) return undefined;
              const names = ids.map((id) => picks.find((p) => p.id === id)?.name ?? id);
              return (
                <EmptyState title="No projection for some of these players">
                  <p>
                    {names.join(", ")}: not in this run (no game this week, or unknown to the model). Remove them and compare
                    again.
                  </p>
                </EmptyState>
              );
            }}
          >
            {(data, ok) => <StartSitResult data={data} approval={ok.approval} />}
          </ApiStateView>
        </section>
      ) : null}
    </div>
  );
}

function StartSitResult({ data, approval }: { data: StartSitResponse; approval: Parameters<typeof ProjectionNotice>[0]["approval"] }) {
  const byId = new Map(data.players.map((p) => [p.player_id, p]));
  const name = (id: string) => byId.get(id)?.name ?? id;
  const max = niceMax(Math.max(...data.players.map((p) => p.ceiling ?? 0)));
  const strength = STRENGTH[data.recommendation.strength] ?? data.recommendation.strength;

  return (
    <div className="space-y-6" data-testid="start-sit-result">
      <ProjectionNotice approval={approval} />
      <LowConfidenceNote positions={data.boom_bust_low_confidence} />

      <div className="card space-y-2 p-4 sm:p-5" data-testid="recommendation">
        <p className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-line-strong bg-surface-2 px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide">{strength}</span>
          <span className="text-sm text-muted">Week {data.week}</span>
        </p>
        <p className="text-lg font-semibold leading-snug">{data.recommendation.text}</p>
        {data.explanation.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {data.explanation.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="card p-4 sm:p-5">
          <ChanceBars
            label="Chance of scoring the most"
            items={Object.entries(data.p_highest).map(([id, value]) => ({ id, name: name(id), value }))}
          />
        </div>
        {data.pairwise.length ? (
          <div className="card p-4 sm:p-5">
            <h3 className="mb-2 text-sm font-semibold">Head to head</h3>
            <ul className="space-y-1.5 text-sm" data-testid="pairwise">
              {data.pairwise.map((pw) => {
                const aWins = pw.p_a_beats_b >= 0.5;
                const [lead, other, p] = aWins ? [pw.a, pw.b, pw.p_a_beats_b] : [pw.b, pw.a, 1 - pw.p_a_beats_b];
                return (
                  <li key={`${pw.a}-${pw.b}`}>
                    <span className="font-medium">{name(lead)}</span> outscores {name(other)} in{" "}
                    <span className="num font-semibold">{pct(p)}</span> of simulations
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </div>

      <ul className="grid gap-4 md:grid-cols-2">
        {data.players.map((p) => (
          <li key={p.player_id} className="card space-y-3 p-4" data-testid="compare-card">
            <div>
              <p className="font-semibold">{p.name}</p>
              <PlayerMeta p={p} />
            </div>
            <DistributionStrip floor={p.floor} median={p.median} ceiling={p.ceiling} max={max} />
            <BoomBust
              boom={p.boom_prob}
              bust={p.bust_prob}
              confidence={p.boom_bust_confidence}
              position={p.position}
              lowConfidencePositions={data.boom_bust_low_confidence}
            />
            <Drivers drivers={p.drivers} />
          </li>
        ))}
      </ul>

      <Disclaimer text={data.disclaimer} />
    </div>
  );
}
