/**
 * "TEST DATA: 2024 week 11 replay, not approved, not a recommendation" (D-124).
 * Shown next to the not-approved label whenever a replay or fixture run is served
 * (private test environments only; production never serves them).
 */
export function TestDataLabel({ label, className = "" }: { label: string; className?: string }) {
  return (
    <p
      data-testid="test-data-label"
      className={`rounded-md border-2 border-warn-line bg-surface px-2.5 py-1 text-sm font-extrabold text-warn-ink ${className}`}
    >
      {label}
    </p>
  );
}
