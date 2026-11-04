/** The "Not approved" label. Text comes from the API's `label`, never "approved". */
export function NotApprovedPill({ label, className = "" }: { label?: string; className?: string }) {
  const raw = (label ?? "not approved").trim();
  const text = /^approved$/i.test(raw) || raw === "" ? "not approved" : raw;
  return (
    <span
      data-testid="not-approved-label"
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border-2 border-warn-line bg-warn-bg px-2.5 py-0.5 text-xs font-extrabold uppercase tracking-wide text-warn-ink ${className}`}
    >
      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true" fill="currentColor">
        <path d="M8 1.2 15.2 14H.8L8 1.2Zm0 4.3a.8.8 0 0 0-.8.8v3.4a.8.8 0 0 0 1.6 0V6.3a.8.8 0 0 0-.8-.8Zm0 6.9a.95.95 0 1 0 0-1.9.95.95 0 0 0 0 1.9Z" />
      </svg>
      {text}
    </span>
  );
}
