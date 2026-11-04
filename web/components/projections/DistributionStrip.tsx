import { pts } from "@/lib/format";

/**
 * Floor-median-ceiling strip on a scale shared by every row in a view.
 * A recessive track, the 10th-90th percentile band in one hue, and the
 * median as an ink tick with a surface ring. Values are printed beside it,
 * so the strip never carries a number on its own.
 */
export function DistributionStrip({
  floor,
  median,
  ceiling,
  max,
  min = 0,
  unit = "pts",
  compact = false,
}: {
  floor: number | null;
  median: number | null;
  ceiling: number | null;
  max: number;
  min?: number;
  unit?: string;
  compact?: boolean;
}) {
  if (floor == null || median == null || ceiling == null) {
    return <span className="text-xs text-muted">Too few simulations for a range</span>;
  }
  const span = Math.max(1e-9, max - min);
  const pos = (x: number) => Math.max(0, Math.min(100, ((x - min) / span) * 100));
  const left = pos(floor);
  const right = pos(ceiling);
  const mid = pos(median);
  const label = `Floor ${pts(floor)}, median ${pts(median)}, ceiling ${pts(ceiling)} ${unit}`;

  return (
    <div className={compact ? "min-w-[7.5rem]" : "min-w-[9rem]"} data-testid="distribution-strip">
      <div role="img" aria-label={label} title={label} className="relative h-4">
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full" style={{ background: "var(--strip-track)" }} />
        <div
          className="absolute top-1/2 h-2.5 -translate-y-1/2 rounded"
          style={{ left: `${left}%`, width: `${Math.max(1, right - left)}%`, background: "var(--strip-band)" }}
        />
        <div
          className="absolute top-1/2 h-4 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-sm"
          style={{ left: `${mid}%`, background: "var(--strip-median)", boxShadow: "0 0 0 2px var(--surface)" }}
        />
      </div>
      <div className="num mt-0.5 flex justify-between gap-2 text-[0.6875rem] leading-tight text-muted" aria-hidden="true">
        <span>{pts(floor)}</span>
        <span className="font-semibold text-ink">{pts(median)}</span>
        <span>{pts(ceiling)}</span>
      </div>
    </div>
  );
}

/** Scale caption for a column header: 0 to the shared maximum. */
export function StripScale({ max, min = 0, unit = "pts" }: { max: number; min?: number; unit?: string }) {
  return (
    <span className="num block text-[0.6875rem] font-normal text-muted">
      {min} to {max} {unit}, floor · median · ceiling
    </span>
  );
}
