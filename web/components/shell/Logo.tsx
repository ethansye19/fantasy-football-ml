/** Original mark: yard lines with a distribution curve over them. */
export function LogoMark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" focusable="false">
      <rect x="1.5" y="1.5" width="29" height="29" rx="7" fill="var(--accent)" />
      <g stroke="var(--accent-contrast)" strokeOpacity="0.45" strokeWidth="1.5">
        <line x1="9" y1="7" x2="9" y2="25" />
        <line x1="16" y1="7" x2="16" y2="25" />
        <line x1="23" y1="7" x2="23" y2="25" />
      </g>
      <path
        d="M4.5 24 C 9 24, 11 10.5, 16 10.5 S 23 24, 27.5 24"
        fill="none"
        stroke="var(--accent-contrast)"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
