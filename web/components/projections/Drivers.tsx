import type { Driver } from "@/lib/api/types";
import { signedPts } from "@/lib/format";

const GROUP_LABEL: Record<string, string> = {
  opportunity: "Opportunity",
  efficiency: "Efficiency",
  matchup: "Matchup",
  environment: "Game environment",
  availability: "Availability",
};

/** Plain-language reasons from the API, largest first, with their point effect. */
export function Drivers({ drivers, reference }: { drivers: Driver[]; reference?: string }) {
  if (!drivers.length) {
    return <p className="text-sm text-muted">No single factor stands out; this projection is close to an average player at the position.</p>;
  }
  return (
    <div className="space-y-2" data-testid="drivers">
      <ul className="space-y-1.5">
        {drivers.map((d, i) => (
          <li key={`${d.group}-${i}`} className="flex gap-2 text-sm">
            <span
              className={`num mt-0.5 inline-block min-w-[3.25rem] shrink-0 rounded px-1.5 text-center text-xs font-semibold ${
                d.points >= 0 ? "bg-boom-bg text-ink" : "bg-bust-bg text-ink"
              }`}
              aria-label={`${signedPts(d.points)} points`}
            >
              {signedPts(d.points)}
            </span>
            <span>
              <span className="font-medium">{GROUP_LABEL[d.group] ?? d.group}:</span> {d.text}
            </span>
          </li>
        ))}
      </ul>
      {reference ? <p className="text-xs text-muted">Point effects are measured under {reference.toUpperCase()} scoring against an average player at the position.</p> : null}
    </div>
  );
}
