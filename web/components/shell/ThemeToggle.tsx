"use client";

import { setThemeChoice, useTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { effective, hydrated } = useTheme();
  const next = effective === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="btn btn-ghost h-10 w-10 p-0"
      onClick={() => setThemeChoice(next)}
      aria-label={hydrated ? `Switch to ${next} theme` : "Switch theme"}
      title={hydrated ? `Switch to ${next} theme` : "Switch theme"}
    >
      {effective === "dark" ? (
        <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="4.5" />
          <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" strokeLinecap="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z" strokeLinejoin="round" />
        </svg>
      )}
    </button>
  );
}

export function ThemeReset() {
  const { choice, hydrated } = useTheme();
  if (!hydrated || choice === "system") return null;
  return (
    <button type="button" className="link text-sm" onClick={() => setThemeChoice("system")}>
      Use system theme
    </button>
  );
}
