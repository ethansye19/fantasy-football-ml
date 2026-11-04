/** Number formatting for the projection views. */

export function pts(x: number | null | undefined, digits = 1): string {
  return x == null || !Number.isFinite(x) ? "–" : x.toFixed(digits);
}

export function signedPts(x: number | null | undefined, digits = 1): string {
  if (x == null || !Number.isFinite(x)) return "–";
  const s = x.toFixed(digits);
  return x > 0 ? `+${s}` : s;
}

export function pct(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "–";
  const v = p * 100;
  if (v > 0 && v < 1) return "<1%";
  if (v < 100 && v > 99) return ">99%";
  return `${Math.round(v)}%`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** A clean upper bound for a shared scale: the next multiple of 5 above max. */
export function niceMax(max: number): number {
  if (!Number.isFinite(max) || max <= 0) return 10;
  const step = max > 100 ? 25 : 5;
  return Math.ceil(max / step) * step;
}
