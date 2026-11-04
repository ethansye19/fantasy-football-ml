"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, subscribeApproval } from "@/lib/api/client";
import type { ApiResult, ApprovalObservation } from "@/lib/api/result";
import type { ApprovalResponse, SeasonResponse } from "@/lib/api/types";
import { deriveApprovalMode, type ApprovalMode } from "./approval-mode";

interface StatusValue {
  approval: ApprovalMode;
  meta: ApprovalResponse | null;
  observed: ApprovalObservation | null;
  season: SeasonResponse | null;
  /** /meta/season answered (false while loading or when the endpoint is missing). */
  seasonKnown: boolean;
  refresh: () => void;
}

const StatusContext = createContext<StatusValue | null>(null);

const REFRESH_MIN_MS = 60_000;

export function StatusProvider({ children }: { children: ReactNode }) {
  const [metaResult, setMetaResult] = useState<ApiResult<ApprovalResponse> | null>(null);
  const [seasonResult, setSeasonResult] = useState<ApiResult<SeasonResponse> | null>(null);
  const [observed, setObserved] = useState<ApprovalObservation | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    return subscribeApproval((event) => {
      if (event.type === "observation") {
        // Keep the most cautious observation: one "not approved" response
        // keeps the banner up until the page reloads.
        setObserved((prev) => (prev && !prev.approved && event.observation.approved ? prev : event.observation));
      } else if (event.type === "no_approved_model") {
        setBlocked(event.message);
      }
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void api.meta.approval({ signal: controller.signal }).then((r) => {
      if (r.kind !== "aborted") setMetaResult(r);
    });
    void api.meta.season({ signal: controller.signal }).then((r) => {
      if (r.kind !== "aborted") setSeasonResult(r);
    });
    return () => controller.abort();
  }, [nonce]);

  // Re-check when the tab becomes visible again, at most once a minute.
  useEffect(() => {
    let last = Date.now();
    const onVisible = () => {
      if (document.visibilityState === "visible" && Date.now() - last > REFRESH_MIN_MS) {
        last = Date.now();
        setNonce((n) => n + 1);
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const value = useMemo<StatusValue>(() => {
    const meta = metaResult?.kind === "ok" ? metaResult.data : null;
    const season = seasonResult?.kind === "ok" ? seasonResult.data : null;
    return {
      approval: deriveApprovalMode(metaResult, observed, blocked),
      meta,
      observed,
      season,
      seasonKnown: season !== null,
      refresh,
    };
  }, [metaResult, seasonResult, observed, blocked, refresh]);

  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export function useStatus(): StatusValue {
  const ctx = useContext(StatusContext);
  if (!ctx) throw new Error("useStatus must be used inside <StatusProvider>");
  return ctx;
}
