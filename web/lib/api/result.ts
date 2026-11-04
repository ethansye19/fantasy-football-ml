/**
 * The one place that turns an HTTP response from the API into a UI state.
 *
 * Every request in the app goes through `toResult`. Pages never inspect
 * status codes themselves: they switch on `ApiResult.kind`, and
 * components/states/ApiStateView.tsx renders the non-ok kinds.
 *
 * Approval rules (D-097, D-099, docs/API.md section 3):
 * - A response counts as approved only when its body says
 *   `approval_status: "approved"` and the X-FFML-Approval header (when
 *   present) does not say otherwise. Anything missing or contradictory is
 *   treated as "not approved".
 * - 503 `no_approved_model` and 503 `no_private_test_snapshot` are states,
 *   not errors.
 */
import type {
  ApprovalHeader,
  ApprovalStatus,
  Environment,
  RunInfo,
  ValidationDetail,
} from "./types";

export const NOT_APPROVED: ApprovalStatus = "not approved";
export const DEFAULT_NO_APPROVED_MODEL_MESSAGE =
  "No approved model. Projections are off until a model passes its pre-registered test.";
export const DEFAULT_NO_SNAPSHOT_MESSAGE =
  "Private test environment: no private-test, replay or fixture predictions snapshot was found. Nothing is served.";

/** What one response says about approval. */
export interface ApprovalObservation {
  /** True only when the body says "approved" and the header agrees or is absent. */
  approved: boolean;
  /** Normalized: "approved" only when `approved` is true. */
  status: ApprovalStatus;
  /** Text for the label, e.g. "not approved". Never "approved" unless `approved`. */
  label: string;
  environment: Environment | null;
  header: ApprovalHeader | null;
  run: RunInfo | null;
  notices: string[];
}

export type ApiResult<T> =
  | { kind: "ok"; status: number; data: T; approval: ApprovalObservation | null }
  | { kind: "no_approved_model"; status: number; message: string }
  | {
      kind: "no_private_test_snapshot";
      status: number;
      message: string;
      approval: ApprovalObservation;
    }
  | {
      kind: "error";
      status: number;
      code: string;
      message: string;
      details: unknown;
      approval: ApprovalObservation | null;
    }
  | { kind: "unreachable"; status: number; message: string }
  | { kind: "aborted"; status: 0 };

export type ResultKind = ApiResult<unknown>["kind"];

/** Error code the web app's own proxy uses when the API cannot be reached. */
export const PROXY_UNREACHABLE_CODE = "api_unreachable";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseApprovalHeader(value: string | null | undefined): ApprovalHeader | null {
  if (value == null) return null;
  const v = value.trim().toLowerCase();
  if (v === "approved" || v === "not-approved" || v === "none") return v;
  // An unknown header value is never read as approval.
  return "not-approved";
}

function asEnvironment(value: unknown): Environment | null {
  return value === "production" || value === "private_test" ? value : null;
}

function asRun(value: unknown): RunInfo | null {
  if (!isRecord(value)) return null;
  if (typeof value.run_id !== "string") return null;
  return value as unknown as RunInfo;
}

function asNotices(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((n): n is string => typeof n === "string" && n.trim().length > 0);
}

/**
 * Read the approval fields of a body and the approval header.
 *
 * Returns null when neither the body nor the header carries approval
 * information (for example league routes in production), unless
 * `expectEnvelope` is set: projection-derived routes must carry the
 * envelope, so a missing one is read as "not approved".
 */
export function readApproval(
  body: unknown,
  headerValue: string | null | undefined,
  expectEnvelope = false,
): ApprovalObservation | null {
  const header = parseApprovalHeader(headerValue);
  const hasBodyStatus = isRecord(body) && "approval_status" in body;
  if (!hasBodyStatus && header === null && !expectEnvelope) return null;

  const rec = isRecord(body) ? body : {};
  const bodyApproved = rec.approval_status === "approved";
  const headerAllows = header === null || header === "approved";
  const approved = bodyApproved && headerAllows;
  const status: ApprovalStatus = approved ? "approved" : NOT_APPROVED;

  let label = typeof rec.label === "string" && rec.label.trim() ? rec.label.trim() : status;
  // The label may only read "approved" when the response is approved.
  if (!approved && /^approved$/i.test(label)) label = NOT_APPROVED;

  return {
    approved,
    status,
    label,
    environment: asEnvironment(rec.environment),
    header,
    run: asRun(rec.run),
    notices: asNotices(rec.notices),
  };
}

function parseBody(text: string): { ok: true; value: unknown } | { ok: false } {
  if (text.trim() === "") return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

export interface ToResultOptions {
  /** The route is projection-derived and must carry the approval envelope. */
  expectEnvelope?: boolean;
}

/** Map one HTTP response to an `ApiResult`. Pure, so it is unit tested directly. */
export function toResult<T>(
  status: number,
  headerValue: string | null | undefined,
  bodyText: string,
  options: ToResultOptions = {},
): ApiResult<T> {
  const parsed = parseBody(bodyText);
  const body = parsed.ok ? parsed.value : null;
  const header = parseApprovalHeader(headerValue);
  const rec = isRecord(body) ? body : null;

  // Gate states first (sections 3.2 and 3.3).
  if (status === 503) {
    if (rec?.status === "no_private_test_snapshot") {
      const approval = readApproval(body, headerValue, true) as ApprovalObservation;
      return {
        kind: "no_private_test_snapshot",
        status,
        message: typeof rec.message === "string" ? rec.message : DEFAULT_NO_SNAPSHOT_MESSAGE,
        approval,
      };
    }
    if (rec?.status === "no_approved_model" || header === "none") {
      return {
        kind: "no_approved_model",
        status,
        message:
          typeof rec?.message === "string" && rec.message ? rec.message : DEFAULT_NO_APPROVED_MODEL_MESSAGE,
      };
    }
  }

  if (status >= 200 && status < 300) {
    if (!parsed.ok) {
      return {
        kind: "error",
        status,
        code: "bad_response",
        message: "The API sent a response that could not be read.",
        details: null,
        approval: readApproval(null, headerValue, options.expectEnvelope),
      };
    }
    return {
      kind: "ok",
      status,
      data: body as T,
      approval: readApproval(options.expectEnvelope ? body : null, headerValue, options.expectEnvelope),
    };
  }

  const err = rec && isRecord(rec.error) ? rec.error : null;
  const code = err && typeof err.code === "string" ? err.code : `http_${status}`;
  const message =
    err && typeof err.message === "string" && err.message
      ? err.message
      : status >= 500
        ? "The API had a problem answering this request."
        : "The request could not be completed.";

  if (code === PROXY_UNREACHABLE_CODE) {
    return { kind: "unreachable", status, message };
  }

  return {
    kind: "error",
    status,
    code,
    message,
    details: err ? (err.details ?? null) : null,
    approval: readApproval(options.expectEnvelope ? body : null, headerValue),
  };
}

/** Result for a request that never got a response (network down, timeout). */
export function unreachable(message?: string): ApiResult<never> {
  return {
    kind: "unreachable",
    status: 0,
    message: message ?? "The app could not reach its API. Check that the API is running, then try again.",
  };
}

/** `details` of a 422 validation_error as a list, or an empty list. */
export function validationDetails(result: ApiResult<unknown>): ValidationDetail[] {
  if (result.kind !== "error" || !Array.isArray(result.details)) return [];
  return result.details.filter(
    (d): d is ValidationDetail => isRecord(d) && Array.isArray(d.loc) && typeof d.msg === "string",
  );
}

/** Plain-language title and hint for an error code (section 4). */
export function describeError(code: string, status: number): { title: string; hint: string } {
  switch (code) {
    case "league_not_found":
      return { title: "League not found", hint: "It may have been deleted. Pick or import a league on the setup page." };
    case "team_not_found":
      return { title: "Team not found", hint: "Choose a team from the league again." };
    case "player_not_found":
      return { title: "Player not found", hint: "One of the chosen players is not in this projection run." };
    case "week_not_available":
      return { title: "Week not available", hint: "Pick one of the weeks this run covers." };
    case "sleeper_not_found":
      return { title: "Not found on Sleeper", hint: "Check the spelling of the username or league id." };
    case "multiple_leagues":
      return { title: "Several leagues found", hint: "Pick the league you want to import." };
    case "no_team_selected":
      return { title: "No team selected", hint: "Choose your team on the setup page first." };
    case "not_a_sleeper_league":
      return { title: "Not a Sleeper league", hint: "Manual leagues cannot be synced with Sleeper." };
    case "offseason":
      return { title: "No games left this season", hint: "Weekly views return when the next season's schedule is out." };
    case "validation_error":
    case "invalid_scoring":
    case "invalid_roster_positions":
      return { title: "Some settings need attention", hint: "Fix the highlighted fields and try again." };
    case "sleeper_unavailable":
      return { title: "Sleeper is not responding", hint: "Sleeper did not answer after several tries. Try again in a minute." };
    case "not_implemented":
      return { title: "Not available yet", hint: "The API does not serve this view yet." };
    case "not_found":
      return { title: "Not found", hint: "The API has no such resource." };
    case "bad_response":
      return { title: "Unreadable response", hint: "Try again. If it keeps happening, the API may be misconfigured." };
    default:
      if (status >= 500) return { title: "Something went wrong", hint: "The API had a problem. Try again in a moment." };
      return { title: "Request failed", hint: "Try again." };
  }
}
