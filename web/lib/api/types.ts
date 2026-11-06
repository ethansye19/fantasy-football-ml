/**
 * Types for the API contract in docs/API.md (contract v1, 2026-11-04).
 *
 * Field names follow the JSON exactly. Optional (`?`) marks fields the
 * contract lists as absent in some cases or that belong to planned endpoints
 * whose shapes may be refined in the contract changelog.
 */

/* ------------------------------------------------------------------ */
/* Approval and envelope (section 3)                                   */
/* ------------------------------------------------------------------ */

export type Environment = "production" | "private_test";

/** The only two values the API emits. "approved" is possible in production only. */
export type ApprovalStatus = "approved" | "not approved";

/** Value of the X-FFML-Approval response header. */
export type ApprovalHeader = "approved" | "not-approved" | "none";

export type RunSource = "published" | "private_test" | "replay" | "fixture";

export interface RunInfo {
  source: RunSource;
  run_id: string;
  season: number;
  week: number;
  model_version: string;
  written_at: string;
  n_sims: number;
  is_replay: boolean;
  has_rest_of_season: boolean;
}

/** Fields at the top of every projection-derived response (section 3.4). */
export interface Envelope {
  approval_status: ApprovalStatus;
  label: string;
  environment: Environment;
  run: RunInfo | null;
  notices: string[];
}

/** Gate 503 bodies (sections 3.2 and 3.3). */
export interface NoApprovedModelBody {
  status: "no_approved_model";
  message: string;
}

export interface NoPrivateTestSnapshotBody {
  status: "no_private_test_snapshot";
  message: string;
  approval_status: "not approved";
  label: "not approved";
}

/** Error envelope (section 4). */
export interface ErrorBody {
  error: { code: string; message: string; details: unknown };
}

export interface ValidationDetail {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/* ------------------------------------------------------------------ */
/* Meta (section 5)                                                    */
/* ------------------------------------------------------------------ */

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  environment: Environment;
  checks: Record<string, string>;
  time: string;
}

export interface ApprovalResponse {
  environment: Environment;
  serving: boolean;
  approval_status: ApprovalStatus;
  label: string;
  reason: string;
  message: string;
  checked_at: string;
  run: RunInfo | null;
  live_model_version?: string | null;
}

export interface AttributionSource {
  id: string;
  name: string;
  license: string;
  url?: string;
  in_use: boolean;
  text: string;
  used_for?: string;
  notes?: string[];
}

export interface AttributionResponse {
  sources: AttributionSource[];
  usage?: string;
  not_displayed?: string;
  independence?: string;
}

/** Registry status words of contract v1.3. The API never sends the bare word "approved". */
export type RegistryStatusWord = "candidate" | "rejected" | "retired" | "awaiting_release" | "live";

export interface RegistryModel {
  name: string | null;
  version: string | null;
  registry_status: RegistryStatusWord | string | null;
  /** True only for the live entry the gate is serving. */
  serving?: boolean;
  created_at: string | null;
}

/** GET /meta/model-card (api/services/meta_content.py and api/routers/meta.py). */
export interface ModelCardResponse {
  title?: string;
  what_it_predicts?: string;
  method?: string[];
  status_summary?: string;
  definitions?: Record<string, string>;
  limitations?: string[];
  documentation?: string[];
  environment?: Environment;
  approval_status?: ApprovalStatus;
  label?: string;
  serving?: boolean;
  /** Production only: registry entries. */
  models?: RegistryModel[];
  /** Private test only. */
  notice?: string;
}

export interface RunInfoResponse {
  environment: Environment;
  serving: boolean;
  approval_status: ApprovalStatus;
  label: string;
  run: (RunInfo & Record<string, unknown>) | null;
  message: string;
}

export type SeasonMode = "regular" | "postseason" | "offseason" | "unknown";

export interface SeasonResponse {
  season: number | null;
  mode: SeasonMode;
  projection_week: number | null;
  week_in_progress: boolean;
  last_completed_week?: number | null;
  next_kickoff_utc?: string | null;
  /** True in the postseason and offseason, false in the regular season, null when the mode is unknown (v1.3). */
  offseason: boolean | null;
  banner: string | null;
  as_of: string;
}

/* ------------------------------------------------------------------ */
/* Leagues (section 6)                                                 */
/* ------------------------------------------------------------------ */

export type PresetName = "ppr" | "half" | "std";

export interface LeagueScoring {
  settings: Record<string, number>;
  hash: string;
  unsupported_keys: string[];
  preset_like: PresetName | string | null;
}

export type RosterSlotKind = "starter" | "bench" | "ir" | "taxi";

export interface RosterPlayer {
  player_id: string | null;
  sleeper_id: string | null;
  name: string;
  position: string;
  team: string | null;
  slot: RosterSlotKind;
  injury_status: string | null;
}

export interface LeagueTeam {
  id: string;
  external_roster_id: string | null;
  team_name: string;
  owner_name: string | null;
  owner_external_id: string | null;
  is_me: boolean;
  players: RosterPlayer[];
}

export interface League {
  id: string;
  source: "sleeper" | "manual";
  external_id: string | null;
  name: string;
  season: number;
  num_teams: number;
  scoring: LeagueScoring;
  roster_positions: string[];
  starting_slots: Record<string, number>;
  settings: Record<string, unknown>;
  my_team_id: string | null;
  created_at: string;
  updated_at: string;
  last_synced_at: string | null;
  teams?: LeagueTeam[];
}

export interface SleeperLeagueSummary {
  league_id: string;
  name: string;
  season: number;
  num_teams: number;
  status: string;
  preset_like: string | null;
}

export interface SleeperUserLeagues {
  user: { user_id: string; username: string; display_name: string };
  season: number;
  leagues: SleeperLeagueSummary[];
}

export interface SleeperImportBody {
  league_id?: string;
  username?: string;
  season?: number;
}

export type ScoringInput =
  | { preset: PresetName; overrides?: Record<string, number> }
  | { settings: Record<string, number> };

export interface ManualLeagueBody {
  name: string;
  season: number;
  num_teams: number;
  scoring: ScoringInput;
  roster_positions: string[];
  settings?: Record<string, unknown>;
  teams?: { name: string; is_me?: boolean; players?: string[] }[];
}

export interface LeagueSettingsBody {
  name?: string;
  scoring?: ScoringInput;
  roster_positions?: string[];
  settings?: Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/* Projection routes (section 7)                                       */
/* ------------------------------------------------------------------ */

export type Position = "QB" | "RB" | "WR" | "TE" | "K" | "DST";

/** GET /players?search= (v1.3, never gated): identity only, no projections. */
export interface PlayerIdentity {
  player_id: string;
  name: string;
  position: Position | string;
  team: string | null;
}

export interface PlayerSearchResponse {
  query: string;
  position: Position | null;
  players: PlayerIdentity[];
}
export const POSITIONS: readonly Position[] = ["QB", "RB", "WR", "TE", "K", "DST"];

export type DriverGroup = "opportunity" | "efficiency" | "matchup" | "environment" | "availability";

export interface Driver {
  group: DriverGroup | string;
  points: number;
  text: string;
}

export interface RosterRef {
  league_team_id: string;
  team_name: string;
  is_mine: boolean;
}

export interface PlayerRow {
  player_id: string;
  sleeper_id: string | null;
  name: string;
  position: Position | string;
  team: string | null;
  opponent: string | null;
  game_id: string | null;
  /**
   * Quantiles and mean of simulated points over the simulations in which the
   * player is active (D-067). Null when the player is active in fewer than 20
   * simulations (v1.2).
   */
  floor: number | null;
  median: number | null;
  ceiling: number | null;
  mean: number | null;
  /** Mean including games missed (v1.2): the value used for lineups, waivers and trades. */
  expected?: number | null;
  boom_prob: number | null;
  bust_prob: number | null;
  active_prob: number;
  boom_bust_confidence: "normal" | "low" | string;
  /** Active in fewer than 100 simulations (v1.2). */
  low_sample?: boolean;
  position_rank: number | null;
  injury_status?: string | null;
  drivers: Driver[];
  roster?: RosterRef | null;
}

export interface ScoringRef {
  hash: string;
  unsupported_keys: string[];
  preset_like: string | null;
  /** Scoring the plain-language drivers are computed under (v1.2). */
  drivers_reference?: string;
  /** Note on how quantiles are computed under this scoring (v1.2). */
  quantiles?: string;
}

export type Availability = "all" | "rostered" | "free_agents" | "mine";
export type ProjectionSort = "median" | "mean" | "ceiling" | "floor" | "boom" | "bust";

export interface ProjectionsQuery {
  week?: number;
  position?: Position;
  search?: string;
  availability?: Availability;
  sort?: ProjectionSort;
  limit?: number;
  offset?: number;
}

export interface ProjectionsResponse extends Envelope {
  league_id?: string;
  season: number;
  week: number;
  /** Positions whose boom/bust calibration is not trusted in this run (v1.3, D-104). */
  boom_bust_low_confidence?: string[];
  scoring: ScoringRef;
  total: number;
  players: PlayerRow[];
}

export interface StartSitBody {
  player_ids: string[];
  week?: number;
}

export type RecommendationStrength = "toss-up" | "lean" | "clear";

export interface StartSitResponse extends Envelope {
  week: number;
  /** Positions whose boom/bust calibration is not trusted in this run (v1.3, D-104). */
  boom_bust_low_confidence?: string[];
  players: PlayerRow[];
  pairwise: { a: string; b: string; p_a_beats_b: number }[];
  p_highest: Record<string, number>;
  recommendation: { player_id: string; strength: RecommendationStrength | string; text: string };
  explanation: string[];
  disclaimer: string;
}

export interface DropRef {
  player_id: string;
  name: string;
  position: string;
  team: string | null;
  lineup_loss: number;
}

export interface WaiverOption {
  add: PlayerRow;
  /** Null when the roster has an open spot (v1.2). */
  drop: DropRef | null;
  gain_next_week: number;
  gain_rest_of_season: number;
  score: number;
  /** Weeks the added player would start (v1.2). */
  starts?: number;
  reason: string;
}

export interface WaiversQuery {
  league_team_id?: string;
  position?: Position;
  limit?: number;
}

export interface WaiversResponse extends Envelope {
  league_id?: string;
  scoring?: ScoringRef;
  open_roster_spot?: boolean;
  league_team_id: string;
  week: number;
  weeks: number[];
  options: WaiverOption[];
  drop_candidates: DropRef[];
  disclaimer: string;
}

export interface TradeBody {
  give: string[];
  receive: string[];
  league_team_id?: string;
  partner_team_id?: string;
}

export interface TradeSide {
  player: PlayerRow;
  ros_mean: number;
  value_over_replacement: number;
  /** max(0, value_over_replacement) (v1.2). */
  trade_value?: number;
}

export type FairnessBand =
  | "fair"
  | "favors_you"
  | "favors_partner"
  | "lopsided_for_you"
  | "lopsided_for_partner";

export interface TradeResponse extends Envelope {
  league_id?: string;
  scoring?: ScoringRef;
  league_team_id?: string;
  partner_team_id?: string;
  verdict_text?: string;
  weeks: number[];
  give: TradeSide[];
  receive: TradeSide[];
  value: { give: number; receive: number; net: number };
  roster_fit: {
    mine: { before: number; after: number; change: number };
    partner: { before: number; after: number; change: number } | null;
  };
  replacement_level: Record<string, number>;
  fairness: { band: FairnessBand | string; difference: number; text: string };
  disclaimer: string;
}

export interface RosRow {
  player_id: string;
  sleeper_id?: string | null;
  name: string;
  position: Position | string;
  team: string | null;
  ros_mean: number;
  ros_floor: number;
  ros_median: number;
  ros_ceiling: number;
  games_remaining: number;
  bye_weeks: number[];
  value_over_replacement: number;
  overall_rank: number;
  position_rank: number;
  roster?: RosterRef | null;
}

export interface RosQuery {
  position?: Position;
  availability?: Availability;
  limit?: number;
  offset?: number;
  include_current_week?: boolean;
}

export interface RosResponse extends Envelope {
  league_id?: string;
  scoring?: ScoringRef;
  first_week: number;
  last_week: number;
  replacement_level?: Record<string, number>;
  total?: number;
  players: RosRow[];
}
