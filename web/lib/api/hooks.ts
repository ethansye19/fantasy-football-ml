"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiResult } from "./result";

export interface UseApi<T> {
  /** Result for the current key, or null while it loads. */
  result: ApiResult<T> | null;
  /** Last result for any key (keeps tables on screen while a filter reloads). */
  previous: ApiResult<T> | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch when `key` changes (null means "do not fetch"). The fetcher receives
 * an AbortSignal; results of superseded requests are dropped.
 */
export function useApi<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<ApiResult<T>>): UseApi<T> {
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<{ key: string; result: ApiResult<T> } | null>(null);
  const fetcherRef = useRef(fetcher);
  const fullKey = key === null ? null : `${key}#${nonce}`;

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (fullKey === null) return;
    const controller = new AbortController();
    void fetcherRef.current(controller.signal).then((result) => {
      if (controller.signal.aborted || result.kind === "aborted") return;
      setState({ key: fullKey, result });
    });
    return () => controller.abort();
  }, [fullKey]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const current = state && state.key === fullKey ? state.result : null;
  return {
    result: current,
    previous: state?.result ?? null,
    loading: fullKey !== null && current === null,
    reload,
  };
}

/** Run one mutation at a time and keep its last result. */
export function useMutation<A extends unknown[], T>(run: (...args: A) => Promise<ApiResult<T>>) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ApiResult<T> | null>(null);
  const runRef = useRef(run);
  useEffect(() => {
    runRef.current = run;
  });
  const mutate = useCallback(async (...args: A): Promise<ApiResult<T>> => {
    setPending(true);
    try {
      const r = await runRef.current(...args);
      setResult(r);
      return r;
    } finally {
      setPending(false);
    }
  }, []);
  const reset = useCallback(() => setResult(null), []);
  return { mutate, pending, result, reset };
}
