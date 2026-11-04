import type { ReactNode } from "react";

type Tone = "neutral" | "info" | "warn" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "border-line bg-surface text-ink",
  info: "border-info-line bg-info-bg text-info-ink",
  warn: "border-warn-line bg-warn-bg text-warn-ink",
  danger: "border-danger-line bg-danger-bg text-danger-ink",
};

const ICONS: Record<Tone, ReactNode> = {
  neutral: <circle cx="12" cy="12" r="8.5" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5M12 7.6v.2" strokeLinecap="round" />
    </>
  ),
  warn: (
    <>
      <path d="M12 3.2 21.5 20H2.5L12 3.2Z" strokeLinejoin="round" />
      <path d="M12 10v4.5M12 17.3v.2" strokeLinecap="round" />
    </>
  ),
  danger: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.8 8.8l6.4 6.4M15.2 8.8l-6.4 6.4" strokeLinecap="round" />
    </>
  ),
};

export interface StatePanelProps {
  title: string;
  tone?: Tone;
  children?: ReactNode;
  actions?: ReactNode;
  testId?: string;
  /** Heading level inside the page; the page itself owns the h1. */
  headingLevel?: 2 | 3;
  role?: "status" | "alert";
}

export function StatePanel({ title, tone = "neutral", children, actions, testId, headingLevel = 2, role }: StatePanelProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      className={`rounded-xl border p-5 sm:p-6 ${TONES[tone]}`}
      data-testid={testId}
      role={role}
      aria-label={role ? undefined : title}
    >
      <div className="flex gap-3">
        <svg viewBox="0 0 24 24" className="mt-0.5 h-6 w-6 shrink-0" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8">
          {ICONS[tone]}
        </svg>
        <div className="min-w-0 flex-1 space-y-2">
          <Heading className="text-lg font-semibold leading-snug">{title}</Heading>
          {children ? <div className="space-y-2 text-[0.9375rem] leading-relaxed">{children}</div> : null}
          {actions ? <div className="flex flex-wrap gap-2 pt-2">{actions}</div> : null}
        </div>
      </div>
    </section>
  );
}
