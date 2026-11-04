"use client";

import { useId, useState } from "react";
import { ApiStateView } from "@/components/states/ApiStateView";
import { api } from "@/lib/api/client";
import { useMutation } from "@/lib/api/hooks";
import type { League, LeagueTeam, RosterPlayer, TradeResponse, TradeSide } from "@/lib/api/types";
import { pts, signedPts } from "@/lib/format";
import { Disclaimer } from "./Disclaimer";
import { GatedView } from "./GatedView";
import { PositionTag } from "./PlayerBits";
import { ProjectionNotice } from "./ProjectionNotice";

const MAX_SIDE = 8;

const BAND: Record<string, string> = {
  fair: "Close to even",
  favors_you: "Favors you",
  favors_partner: "Favors your partner",
  lopsided_for_you: "Strongly favors you",
  lopsided_for_partner: "Strongly favors your partner",
};

export function TradeView() {
  return (
    <GatedView feature="The trade analyzer" league="team">
      {({ league, myTeam }) => <TradeBody league={league as League} myTeam={myTeam as LeagueTeam} />}
    </GatedView>
  );
}

function tradeable(team: LeagueTeam | undefined): RosterPlayer[] {
  return (team?.players ?? []).filter((p) => p.player_id);
}

function TradeBody({ league, myTeam }: { league: League; myTeam: LeagueTeam }) {
  const partners = (league.teams ?? []).filter((t) => t.id !== myTeam.id);
  const [partnerId, setPartnerId] = useState<string>(partners[0]?.id ?? "");
  const [give, setGive] = useState<string[]>([]);
  const [receive, setReceive] = useState<string[]>([]);
  const evaluate = useMutation(() =>
    api.trade(league.id, { give, receive, league_team_id: myTeam.id, partner_team_id: partnerId || undefined }),
  );
  const partner = partners.find((t) => t.id === partnerId);
  const selectId = useId();

  const toggle = (list: string[], set: (v: string[]) => void, id: string) => {
    set(list.includes(id) ? list.filter((x) => x !== id) : list.length >= MAX_SIDE ? list : [...list, id]);
    evaluate.reset();
  };

  if (!partners.length) {
    return <p className="text-sm text-muted">This league has no other teams to trade with.</p>;
  }

  return (
    <div className="space-y-6">
      <section aria-labelledby="trade-build" className="card space-y-4 p-4 sm:p-5">
        <h2 id="trade-build" className="text-lg font-semibold">
          Build a trade
        </h2>
        <div className="max-w-sm">
          <label htmlFor={selectId} className="mb-1 block text-sm font-medium">
            Trade partner
          </label>
          <select
            id={selectId}
            className="field"
            value={partnerId}
            onChange={(e) => {
              setPartnerId(e.target.value);
              setReceive([]);
              evaluate.reset();
            }}
          >
            {partners.map((t) => (
              <option key={t.id} value={t.id}>
                {t.team_name}
                {t.owner_name ? ` (${t.owner_name})` : ""}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <PlayerChecklist legend={`You give (${myTeam.team_name})`} players={tradeable(myTeam)} chosen={give} onToggle={(id) => toggle(give, setGive, id)} />
          <PlayerChecklist
            legend={`You receive (${partner?.team_name ?? "partner"})`}
            players={tradeable(partner)}
            chosen={receive}
            onToggle={(id) => toggle(receive, setReceive, id)}
          />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn-primary"
            disabled={!give.length || !receive.length || evaluate.pending}
            onClick={() => void evaluate.mutate()}
          >
            {evaluate.pending ? "Evaluating…" : "Evaluate trade"}
          </button>
          {!give.length || !receive.length ? <p className="text-sm text-muted">Pick at least one player on each side.</p> : null}
        </div>
      </section>

      {evaluate.result ? (
        <section aria-labelledby="trade-result" aria-live="polite">
          <h2 id="trade-result" className="sr-only">
            Trade evaluation
          </h2>
          <ApiStateView result={evaluate.result} feature="The trade analyzer" onRetry={() => void evaluate.mutate()}>
            {(data, ok) => <TradeResult data={data} approval={ok.approval} />}
          </ApiStateView>
        </section>
      ) : null}
    </div>
  );
}

function PlayerChecklist({
  legend,
  players,
  chosen,
  onToggle,
}: {
  legend: string;
  players: RosterPlayer[];
  chosen: string[];
  onToggle: (id: string) => void;
}) {
  const base = useId();
  return (
    <fieldset className="rounded-lg border border-line p-3">
      <legend className="px-1 text-sm font-semibold">{legend}</legend>
      {players.length ? (
        <ul className="max-h-72 space-y-1 overflow-y-auto">
          {players.map((p) => {
            const id = `${base}-${p.player_id}`;
            return (
              <li key={p.player_id}>
                <label htmlFor={id} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-surface-2">
                  <input id={id} type="checkbox" checked={chosen.includes(p.player_id as string)} onChange={() => onToggle(p.player_id as string)} />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  <PositionTag position={p.position === "DEF" ? "DST" : p.position} />
                  <span className="w-9 text-xs text-muted">{p.team ?? "FA"}</span>
                </label>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-muted">No players with projections on this roster.</p>
      )}
    </fieldset>
  );
}

function SideTable({ title, side }: { title: string; side: TradeSide[] }) {
  return (
    <div className="card overflow-x-auto">
      <table className="data-table">
        <caption className="px-3 pt-3 text-left font-semibold">{title}</caption>
        <thead>
          <tr>
            <th scope="col">Player</th>
            <th scope="col" className="hidden text-right sm:table-cell">
              ROS points
            </th>
            <th scope="col" className="text-right">
              Over replacement
            </th>
            <th scope="col" className="text-right">
              Trade value
            </th>
          </tr>
        </thead>
        <tbody>
          {side.map((s) => (
            <tr key={s.player.player_id}>
              <td>
                <span className="font-medium">{s.player.name}</span> <PositionTag position={s.player.position} />
              </td>
              <td className="num hidden text-right sm:table-cell">{pts(s.ros_mean)}</td>
              <td className="num text-right">{signedPts(s.value_over_replacement)}</td>
              <td className="num text-right font-semibold">{pts(s.trade_value ?? Math.max(0, s.value_over_replacement))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The part of verdict_text that the fairness sentence does not already say. */
function verdictExtra(data: TradeResponse): string | null {
  const v = data.verdict_text?.trim();
  if (!v) return null;
  const f = data.fairness.text.trim();
  const rest = v.startsWith(f) ? v.slice(f.length).trim() : v;
  return rest || null;
}

function TradeResult({ data, approval }: { data: TradeResponse; approval: Parameters<typeof ProjectionNotice>[0]["approval"] }) {
  const first = data.weeks[0];
  const last = data.weeks[data.weeks.length - 1];
  const fit = data.roster_fit;
  return (
    <div className="space-y-5" data-testid="trade-result">
      <ProjectionNotice approval={approval} />

      <div className="card space-y-2 p-4 sm:p-5" data-testid="fairness">
        <p className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-line-strong bg-surface-2 px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide">
            {BAND[data.fairness.band] ?? data.fairness.band}
          </span>
          {first !== undefined && last !== undefined ? <span className="text-sm text-muted">Weeks {first}-{last}</span> : null}
        </p>
        <p className="text-lg font-semibold leading-snug">{data.fairness.text}</p>
        {verdictExtra(data) ? <p className="text-sm">{verdictExtra(data)}</p> : null}
      </div>

      <dl className="grid gap-3 sm:grid-cols-3">
        {[
          ["You give", pts(data.value.give)],
          ["You receive", pts(data.value.receive)],
          ["Net for you", signedPts(data.value.net)],
        ].map(([k, v]) => (
          <div key={k} className="card p-4">
            <dt className="text-sm text-muted">{k}</dt>
            <dd className="text-2xl font-semibold">{v}</dd>
            <dd className="text-xs text-muted">rest-of-season points over replacement</dd>
          </div>
        ))}
      </dl>

      <div className="card overflow-x-auto">
        <table className="data-table" data-testid="roster-fit">
          <caption className="px-3 pt-3 text-left font-semibold">Roster fit: best weekly lineups, summed</caption>
          <thead>
            <tr>
              <th scope="col">Team</th>
              <th scope="col" className="text-right">
                Before
              </th>
              <th scope="col" className="text-right">
                After
              </th>
              <th scope="col" className="text-right">
                Change
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row" className="font-medium">
                You
              </th>
              <td className="num text-right">{pts(fit.mine.before)}</td>
              <td className="num text-right">{pts(fit.mine.after)}</td>
              <td className="num text-right font-semibold">{signedPts(fit.mine.change)}</td>
            </tr>
            {fit.partner ? (
              <tr>
                <th scope="row" className="font-medium">
                  Partner
                </th>
                <td className="num text-right">{pts(fit.partner.before)}</td>
                <td className="num text-right">{pts(fit.partner.after)}</td>
                <td className="num text-right font-semibold">{signedPts(fit.partner.change)}</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <SideTable title="You give" side={data.give} />
        <SideTable title="You receive" side={data.receive} />
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">Replacement level (rest-of-season points)</h3>
        <dl className="flex flex-wrap gap-2 text-sm">
          {Object.entries(data.replacement_level).map(([pos, v]) => (
            <div key={pos} className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-2 py-1">
              <dt>
                <PositionTag position={pos} />
              </dt>
              <dd className="num">{pts(v)}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-1 text-xs text-muted">A player below replacement adds no trade value: a similar player is available in free agency.</p>
      </div>

      <Disclaimer text={data.disclaimer} />
    </div>
  );
}
