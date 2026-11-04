import { pct } from "@/lib/format";

const BOOM_N: Record<string, number> = { QB: 6, RB: 12, WR: 12, TE: 6, K: 6, DST: 6, DEF: 6 };
const BUST_N: Record<string, number> = { QB: 18, RB: 36, WR: 48, TE: 18, K: 20, DST: 20, DEF: 20 };

/**
 * Boom and bust chances. Text stays in ink; a small colored marker carries
 * the direction. When the API marks the estimate low confidence (K and DST,
 * D-089; QB when flagged) the badges are dashed and say so in words.
 */
export function BoomBust({
  boom,
  bust,
  confidence,
  position,
  stacked = false,
}: {
  boom: number | null;
  bust: number | null;
  confidence: string;
  position: string;
  stacked?: boolean;
}) {
  const low = confidence === "low";
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
