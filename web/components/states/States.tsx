import Link from "next/link";
import type { ReactNode } from "react";
import { describeError } from "@/lib/api/result";
import { NotApprovedPill } from "@/components/status/NotApprovedPill";
import { StatePanel } from "./StatePanel";

export function LoadingState({ label = "Loading", rows = 4 }: { label?: string; rows?: number }) {
  return (
    <div aria-busy="true" data-testid="loading-state" className="space-y-3">
      <p className="sr-only" role="status">
        {label}…
      </p>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton h-10" style={{ opacity: 1 - i * 0.15 }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <StatePanel title={title} tone="neutral" testId="empty-state" actions={actions}>
      {children}
    </StatePanel>
  );
}

export function ErrorState({
  code,
  status,
  message,
  onRetry,
  title,
}: {
  code: string;
  status: number;
  message: string;
  onRetry?: () => void;
  title?: string;
}) {
  const described = describeError(code, status);
  return (
    <StatePanel
      title={title ?? described.title}
      tone="danger"
      testId="error-state"
      role="alert"
      actions={
        onRetry ? (
          <button type="button" className="btn btn-secondary" onClick={onRetry}>
            Try again
          </button>
        ) : null
      }
    >
      <p>{message}</p>
      <p className="text-sm opacity-90">{described.hint}</p>
      <p className="font-mono text-xs opacity-80">
        {status ? `HTTP ${status} · ` : ""}
        {code}
      </p>
    </StatePanel>
  );
}

export function UnreachableState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <StatePanel
      title="Can't reach the API"
      tone="warn"
      testId="unreachable-state"
      actions={
        onRetry ? (
          <button type="button" className="btn btn-secondary" onClick={onRetry}>
            Try again
          </button>
        ) : null
      }
    >
      <p>{message}</p>
    </StatePanel>
  );
}

/**
 * Production without an approved model. A calm, expected state, not an
 * error: projection features are intentionally off (D-097, D-099).
 */
export function NoApprovedModelState({ message, feature }: { message: string; feature?: string }) {
  return (
    <StatePanel
      title="No approved model"
      tone="info"
      testId="no-approved-model"
      actions={
        <>
          <Link href="/about/model-card" className="btn btn-secondary">
            Model status
          </Link>
          <Link href="/setup" className="btn btn-ghost">
            Set up your league
          </Link>
        </>
      }
    >
      <p>{message}</p>
      <p>
        {feature ? `${feature} ` : "This view "}
        uses model projections, so it stays off until a model passes its pre-registered test. The most recent
        candidate did not pass its test, so no projections are published.
      </p>
      <p>League setup and the information pages work as usual in the meantime.</p>
    </StatePanel>
  );
}

/** Private test environment with nothing to serve. */
export function NoPrivateSnapshotState({ message, label }: { message: string; label?: string }) {
  return (
    <StatePanel title="No private test snapshot" tone="warn" testId="no-private-test-snapshot">
      <p>
        <NotApprovedPill label={label} />
      </p>
      <p>{message}</p>
      <p>
        This is a private test environment. Once a private-test, replay or fixture snapshot is configured, labeled
        test numbers appear here.
      </p>
    </StatePanel>
  );
}

export function NeedsLeagueState({ feature }: { feature: string }) {
  return (
    <EmptyState
      title="Set up a league first"
      actions={
        <Link href="/setup" className="btn btn-primary">
          Set up a league
        </Link>
      }
    >
      <p>{feature} works with your league&apos;s scoring, roster slots and rosters. Import a Sleeper league or enter your settings by hand.</p>
    </EmptyState>
  );
}

export function NeedsTeamState({ feature }: { feature: string }) {
  return (
    <EmptyState
      title="Choose your team"
      actions={
        <Link href="/setup" className="btn btn-primary">
          Choose my team
        </Link>
      }
    >
      <p>{feature} needs to know which roster is yours.</p>
    </EmptyState>
  );
}
