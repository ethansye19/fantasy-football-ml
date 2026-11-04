import type { PlayerRow } from "@/lib/api/types";
import { pct } from "@/lib/format";

const POS_VAR: Record<string, string> = {
  QB: "var(--pos-qb)",
  RB: "var(--pos-rb)",
  WR: "var(--pos-wr)",
  TE: "var(--pos-te)",
  K: "var(--pos-k)",
  DST: "var(--pos-dst)",
  DEF: "var(--pos-dst)",
};

/** Position text with a colored marker beside it (identity never by color alone). */
export function PositionTag({ position, rank }: { position: string; rank?: number | null }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-ink">
      <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full" style={{ background: POS_VAR[position] ?? "var(--text-muted)" }} />
      {position}
      {rank ? <span className="num font-normal text-muted">{rank}</span> : null}
    </span>
  );
}

/** Team, opponent and availability notes under a player's name. */
export function PlayerMeta({ p }: { p: Pick<PlayerRow, "position" | "team" | "opponent" | "position_rank" | "active_prob" | "injury_status" | "low_sample"> }) {
  const notes: string[] = [];
  if (p.injury_status) notes.push(p.injury_status);
  if (p.active_prob < 0.95) notes.push(`plays in ${pct(p.active_prob)} of simulations`);
  if (p.low_sample) notes.push("few simulations");
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
      <PositionTag position={p.position} rank={p.position_rank} />
      <span>
        {p.team ?? "FA"}
        {p.opponent ? ` vs ${p.opponent}` : ""}
      </span>
      {notes.length ? <span className="font-medium text-ink">{notes.join(" · ")}</span> : null}
    </span>
  );
}
