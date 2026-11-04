import type { ScoringRef } from "@/lib/api/types";

/** How the league's scoring was applied, and which keys were ignored. */
export function ScoringNote({ scoring }: { scoring?: ScoringRef }) {
  if (!scoring) return null;
  const parts: string[] = [];
  if (scoring.quantiles) parts.push(scoring.quantiles);
  if (scoring.unsupported_keys?.length) parts.push(`Ignored scoring keys: ${scoring.unsupported_keys.join(", ")}.`);
  if (!parts.length) return null;
  return (
    <p className="mb-3 text-xs text-muted" data-testid="scoring-note">
      {parts.join(" ")}
    </p>
  );
}
