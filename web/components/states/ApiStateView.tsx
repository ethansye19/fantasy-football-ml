"use client";

import type { ReactNode } from "react";
import type { ApiResult } from "@/lib/api/result";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  NeedsTeamState,
  NoApprovedModelState,
  NoPrivateSnapshotState,
  UnreachableState,
} from "./States";

export interface ApiStateViewProps<T> {
  result: ApiResult<T> | null;
  loading?: boolean;
  onRetry?: () => void;
  /** Name of the feature, used in the "No approved model" text. */
  feature?: string;
  loadingLabel?: string;
  /** Override for specific error codes (for example a friendlier empty state). */
  renderError?: (result: Extract<ApiResult<T>, { kind: "error" }>) => ReactNode | undefined;
  children: (data: T, result: Extract<ApiResult<T>, { kind: "ok" }>) => ReactNode;
}

/** Renders loading, gate states, errors, or the data. Every page uses this. */
export function ApiStateView<T>({ result, loading, onRetry, feature, loadingLabel, renderError, children }: ApiStateViewProps<T>) {
  if (result === null || result.kind === "aborted") {
    return loading === false ? null : <LoadingState label={loadingLabel} />;
  }
  switch (result.kind) {
    case "ok":
      return <>{children(result.data, result)}</>;
    case "no_approved_model":
      return <NoApprovedModelState message={result.message} feature={feature} />;
    case "no_private_test_snapshot":
      return <NoPrivateSnapshotState message={result.message} label={result.approval.label} />;
    case "unreachable":
      return <UnreachableState message={result.message} onRetry={onRetry} />;
    case "error": {
      const custom = renderError?.(result);
      if (custom !== undefined) return <>{custom}</>;
      // Expected states, shown calmly rather than as failures.
      if (result.code === "offseason") {
        return (
          <EmptyState title="No games left this season">
            <p>{result.message}</p>
            <p>Weekly views return when the next season&apos;s schedule is out. Rest-of-season rankings stay available.</p>
          </EmptyState>
        );
      }
      if (result.code === "no_team_selected") return <NeedsTeamState feature={feature ?? "This view"} />;
      return <ErrorState code={result.code} status={result.status} message={result.message} onRetry={onRetry} />;
    }
  }
}
