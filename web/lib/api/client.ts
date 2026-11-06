/**
 * Typed client for docs/API.md. Every function resolves to an `ApiResult`
 * and never throws: the mapping lives in ./result.ts.
 *
 * In the browser, requests go to this app's own `/api/*` route, which
 * forwards them to the API (app/api/[...path]/route.ts). On the server they
 * go straight to FFML_API_URL.
 */
import {
  type ApiResult,
  type ApprovalObservation,
  toResult,
  unreachable,
} from "./result";
import type {
  ApprovalResponse,
  AttributionResponse,
  HealthResponse,
  League,
  LeagueSettingsBody,
  ManualLeagueBody,
  ModelCardResponse,
  PlayerSearchResponse,
  Position,
  ProjectionsQuery,
  ProjectionsResponse,
  RosQuery,
  RosResponse,
  RunInfoResponse,
  SeasonResponse,
  SleeperImportBody,
  SleeperUserLeagues,
  StartSitBody,
  StartSitResponse,
  TradeBody,
  TradeResponse,
  WaiversQuery,
  WaiversResponse,
} from "./types";

export const APPROVAL_HEADER = "x-ffml-approval";
const TIMEOUT_MS = 20_000;

/* ------------------------------------------------------------------ */
/* Approval observers: the global banner listens to every response.    */
/* ------------------------------------------------------------------ */

export type ApprovalEvent =
  | { type: "observation"; observation: ApprovalObservation }
  | { type: "no_approved_model"; message: string }
  | { type: "unreachable" };

type Listener = (event: ApprovalEvent) => void;
const listeners = new Set<Listener>();

export function subscribeApproval(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emit(result: ApiResult<unknown>): void {
  let event: ApprovalEvent | null = null;
  if (result.kind === "no_approved_model") event = { type: "no_approved_model", message: result.message };
  else if (result.kind === "no_private_test_snapshot") event = { type: "observation", observation: result.approval };
  else if ((result.kind === "ok" || result.kind === "error") && result.approval)
    event = { type: "observation", observation: result.approval };
  else if (result.kind === "unreachable") event = { type: "unreachable" };
  if (!event) return;
  for (const listener of listeners) {
    try {
      listener(event);
    } catch {
      // a broken listener must not break the request
    }
  }
}

/* ------------------------------------------------------------------ */
/* Core request                                                        */
/* ------------------------------------------------------------------ */

export function apiBase(): string {
  if (typeof window !== "undefined") return "/api";
  return (process.env.FFML_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

type QueryValue = string | number | boolean | null | undefined;

export function buildPath(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  /** Projection-derived route: the body must carry the approval envelope. */
  envelope?: boolean;
}

function combineSignals(signal: AbortSignal | undefined): { signal: AbortSignal; cleanup: () => void; timedOut: () => boolean } {
  const controller = new AbortController();
  let didTimeout = false;
  const timer = setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, TIMEOUT_MS);
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
    timedOut: () => didTimeout,
  };
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { method = "GET", body, query, signal, envelope = false } = options;
  const url = `${apiBase()}${buildPath(path, query)}`;
  const combined = combineSignals(signal);
  let result: ApiResult<T>;
  try {
    const response = await fetch(url, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: combined.signal,
    });
    const text = response.status === 204 ? "" : await response.text();
    result = toResult<T>(response.status, response.headers.get(APPROVAL_HEADER), text, { expectEnvelope: envelope });
  } catch {
    if (signal?.aborted) return { kind: "aborted", status: 0 };
    result = combined.timedOut()
      ? unreachable("The API took too long to answer. Try again.")
      : unreachable();
  } finally {
    combined.cleanup();
  }
  emit(result);
  return result;
}

/* ------------------------------------------------------------------ */
/* Endpoints                                                           */
/* ------------------------------------------------------------------ */

const enc = encodeURIComponent;
type Opt = { signal?: AbortSignal };

export const api = {
  meta: {
    health: (o: Opt = {}) => request<HealthResponse>("/meta/health", o),
    approval: (o: Opt = {}) => request<ApprovalResponse>("/meta/approval", o),
    season: (o: Opt = {}) => request<SeasonResponse>("/meta/season", o),
    attribution: (o: Opt = {}) => request<AttributionResponse>("/meta/attribution", o),
    modelCard: (o: Opt = {}) => request<ModelCardResponse>("/meta/model-card", o),
    runInfo: (o: Opt = {}) => request<RunInfoResponse>("/meta/run-info", o),
  },
  sleeper: {
    userLeagues: (username: string, season?: number, o: Opt = {}) =>
      request<SleeperUserLeagues>(`/sleeper/users/${enc(username)}/leagues`, { ...o, query: { season } }),
  },
  players: {
    /** Identity-only lookup for league setup (never gated). `search` needs 2 to 60 characters. */
    search: (search: string, position?: Position, limit = 50, o: Opt = {}) =>
      request<PlayerSearchResponse>("/players", { ...o, query: { search, position, limit } }),
  },
  leagues: {
    list: (o: Opt = {}) => request<{ leagues: League[] }>("/leagues", o),
    get: (id: string, o: Opt = {}) => request<League>(`/leagues/${enc(id)}`, o),
    importSleeper: (body: SleeperImportBody, o: Opt = {}) =>
      request<League>("/leagues/import/sleeper", { ...o, method: "POST", body }),
    createManual: (body: ManualLeagueBody, o: Opt = {}) => request<League>("/leagues", { ...o, method: "POST", body }),
    updateSettings: (id: string, body: LeagueSettingsBody, o: Opt = {}) =>
      request<League>(`/leagues/${enc(id)}/settings`, { ...o, method: "PUT", body }),
    setMyTeam: (id: string, leagueTeamId: string, o: Opt = {}) =>
      request<League>(`/leagues/${enc(id)}/my-team`, { ...o, method: "PUT", body: { league_team_id: leagueTeamId } }),
    sync: (id: string, o: Opt = {}) => request<League>(`/leagues/${enc(id)}/sync`, { ...o, method: "POST" }),
    remove: (id: string, o: Opt = {}) => request<null>(`/leagues/${enc(id)}`, { ...o, method: "DELETE" }),
  },
  projections: {
    league: (id: string, q: ProjectionsQuery = {}, o: Opt = {}) =>
      request<ProjectionsResponse>(`/leagues/${enc(id)}/projections`, { ...o, query: { ...q }, envelope: true }),
    preset: (scoring: string, q: ProjectionsQuery = {}, o: Opt = {}) =>
      request<ProjectionsResponse>("/projections", { ...o, query: { scoring, ...q }, envelope: true }),
  },
  startSit: (id: string, body: StartSitBody, o: Opt = {}) =>
    request<StartSitResponse>(`/leagues/${enc(id)}/start-sit`, { ...o, method: "POST", body, envelope: true }),
  waivers: (id: string, q: WaiversQuery = {}, o: Opt = {}) =>
    request<WaiversResponse>(`/leagues/${enc(id)}/waivers`, { ...o, query: { ...q }, envelope: true }),
  trade: (id: string, body: TradeBody, o: Opt = {}) =>
    request<TradeResponse>(`/leagues/${enc(id)}/trades/evaluate`, { ...o, method: "POST", body, envelope: true }),
  ros: {
    league: (id: string, q: RosQuery = {}, o: Opt = {}) =>
      request<RosResponse>(`/leagues/${enc(id)}/ros`, { ...o, query: { ...q }, envelope: true }),
    preset: (scoring: string, q: RosQuery = {}, o: Opt = {}) =>
      request<RosResponse>("/ros", { ...o, query: { scoring, ...q }, envelope: true }),
  },
};
