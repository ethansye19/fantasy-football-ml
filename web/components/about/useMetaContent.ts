"use client";

import { useApi } from "@/lib/api/hooks";
import type { ApiResult } from "@/lib/api/result";

export type ContentSource = "loading" | "api" | "fallback";

/**
 * Load a /meta document; use built-in copy when the API is unreachable,
 * the endpoint is missing, or the answer is empty.
 */
export function useMetaContent<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<ApiResult<T>>,
  fallback: T,
  isEmpty: (data: T) => boolean = () => false,
): { data: T | null; source: ContentSource; reason: string | null; reload: () => void } {
  const { result, reload } = useApi<T>(key, fetcher);
  if (result === null) return { data: null, source: "loading", reason: null, reload };
  if (result.kind === "ok" && result.data && !isEmpty(result.data)) {
    return { data: result.data, source: "api", reason: null, reload };
  }
  let reason = "The API returned no content.";
  if (result.kind === "unreachable") reason = "The API could not be reached.";
  else if (result.kind === "error") reason = result.status === 404 ? "The API does not provide this page yet." : result.message;
  return { data: fallback, source: "fallback", reason, reload };
}
