/**
 * The app-wide approval mode behind the global banner and the gated views.
 * Inputs: the /meta/approval result, the most cautious approval seen on any
 * response, and whether any route answered 503 no_approved_model.
 *
 * "approved" needs all of: production, serving, approval_status "approved"
 * from /meta/approval, and no response that said otherwise.
 */
import type { ApiResult, ApprovalObservation } from "@/lib/api/result";
import type { ApprovalResponse, RunInfo } from "@/lib/api/types";

export type ApprovalMode =
  | { kind: "loading" }
  /** Production with no approved model: projection features are off. */
  | { kind: "no_approved_model"; message: string }
  /** Private test: labeled "not approved" data may be served. */
  | {
      kind: "not_approved";
      label: string;
      message: string;
      notices: string[];
      serving: boolean;
      reason: string | null;
      run: RunInfo | null;
    }
  /** Production, approved live model (only after a model passes its test). */
  | { kind: "approved"; liveModelVersion: string | null; run: RunInfo | null }
  /** The API is down or /meta/approval failed: treat projections as off. */
  | { kind: "unavailable"; message: string };

export function deriveApprovalMode(
  meta: ApiResult<ApprovalResponse> | null,
  observed: ApprovalObservation | null,
  blockedMessage: string | null,
): ApprovalMode {
  const data = meta?.kind === "ok" ? meta.data : null;

  // Any response that was explicitly not approved wins over everything else.
  if (observed && !observed.approved) {
    return {
      kind: "not_approved",
      label: observed.label,
      message: data?.message ?? observed.notices[0] ?? "Not approved. Private test environment only; not a recommendation.",
      notices: observed.notices,
      serving: data?.serving ?? observed.run !== null,
      reason: data?.reason ?? null,
      run: observed.run ?? data?.run ?? null,
    };
  }

  if (data) {
    if (data.environment === "private_test" || data.approval_status !== "approved") {
      if (data.environment === "private_test") {
        return {
          kind: "not_approved",
          label: data.label && !/^approved$/i.test(data.label) ? data.label : "not approved",
          message: data.message,
          notices: [],
          serving: data.serving,
          reason: data.reason,
          run: data.run,
        };
      }
      return { kind: "no_approved_model", message: data.message || blockedMessage || defaultBlocked };
    }
    // production + approval_status "approved"
    if (!data.serving || blockedMessage) {
      return { kind: "no_approved_model", message: blockedMessage ?? data.message ?? defaultBlocked };
    }
    return { kind: "approved", liveModelVersion: data.live_model_version ?? null, run: data.run };
  }

  if (blockedMessage) return { kind: "no_approved_model", message: blockedMessage };
  if (meta === null) return { kind: "loading" };
  if (meta.kind === "unreachable") return { kind: "unavailable", message: meta.message };
  return {
    kind: "unavailable",
    message: "The approval status could not be read, so projection features are treated as off.",
  };
}

const defaultBlocked = "No approved model. Projections are off until a model passes its pre-registered test.";
