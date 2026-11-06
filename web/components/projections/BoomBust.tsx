import { pct } from "@/lib/format";

const BOOM_N: Record<string, number> = { QB: 6, RB: 12, WR: 12, TE: 6, K: 6, DST: 6, DEF: 6 };
const BUST_N: Record<string, number> = { QB: 18, RB: 36, WR: 48, TE: 18, K: 20, DST: 20, DEF: 20 };

/**
 * Boom and bust chances. Text stays in ink; a small colored marker carries
 * the direction. The badges are dashed and say "Low confidence" when the row
 * says so (`boom_bust_confidence`) or when the response's
 * `boom_bust_low_confidence` list (contract v1.3, D-104) names the position.
 * Nothing here hardcodes which positions those are.
 */
export function BoomBust({
  boom,
  bust,
  confidence,
  position,
  lowConfidencePositions,
  stacked = false,
}: {
  boom: number | null;
  bust: number | null;
  confidence: string;
  position: string;
  lowConfidencePositions?: readonly string[] | null;
  stacked?: boolean;
}) {
  const low = confidence === "low" || Boolean(lowConfidencePositions?.includes(position));
  const boomN = BOOM_N[position];
  const bustN = BUST_N[position];
  const boomTitle = boomN ? `Chance of a top-${boomN} ${position} finish this week` : "Chance of a top finish at the position";
  const bustTitle = bustN ? `Chance of finishing outside the top ${bustN} ${position}s` : "Chance of finishing outside the startable range";
  const badge = `inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium text-ink ${
    low ? "border-dashed border-line-strong bg-transparent" : "border-line bg-surface-2"
  }`;
  return (
    <div className={`flex ${stacked ? "flex-col items-start" : "flex-wrap items-center"} gap-1`} data-testid="boom-bust" data-confidence={low ? "low" : "normal"}>
      <span className={badge} title={boomTitle}>
        <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" aria-hidden="true">
          <path d="M5 1 9.2 8.5H.8Z" fill="var(--boom)" />
        </svg>
        <span>
          Boom <span className="num font-semibold">{pct(boom)}</span>
        </span>
      </span>
      <span className={badge} title={bustTitle}>
        <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" aria-hidden="true">
          <path d="M5 9 9.2 1.5H.8Z" fill="var(--bust)" />
        </svg>
        <span>
          Bust <span className="num font-semibold">{pct(bust)}</span>
        </span>
      </span>
      {low ? (
        <span className="text-[0.6875rem] font-semibold uppercase tracking-wide text-muted" title="These probabilities did not calibrate well in testing; treat them as rough.">
          Low confidence
        </span>
      ) : null}
    </div>
  );
}

const POSITION_NAME: Record<string, string> = {
  QB: "quarterbacks",
  RB: "running backs",
  WR: "receivers",
  TE: "tight ends",
  K: "kickers",
  DST: "defenses",
};

/** Which positions' boom/bust chances this run flags as low confidence (`boom_bust_low_confidence`). */
export function LowConfidenceNote({ positions }: { positions?: readonly string[] | null }) {
  if (!positions?.length) return null;
  const sorted = [...positions].sort();
  const names = sorted.map((p) => POSITION_NAME[p] ?? p);
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return (
    <p className="mb-3 text-xs text-muted" data-testid="low-confidence-note">
      Boom and bust chances for {list} ({sorted.join(", ")}) are low confidence in this run: their calibration did not hold
      up in testing. Their badges are dashed.
    </p>
  );
}
