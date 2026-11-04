/** League settings form: values, validation, request bodies, server-error mapping. */
import type { ApiResult } from "./api/result";
import { validationDetails } from "./api/result";
import type { League, LeagueSettingsBody, ManualLeagueBody, PresetName } from "./api/types";
import {
  DEFAULT_ROSTER,
  IDP_SLOTS,
  MAX_PER_SLOT,
  MAX_ROSTER_SLOTS,
  PRESETS,
  SCORING_FIELDS,
  SLOT_INFO,
  countSlots,
  overridesFor,
  slotsToPositions,
} from "./scoring";

export type FormMode = "create" | "edit";

export interface LeagueFormValues {
  name: string;
  season: string;
  numTeams: string;
  /** Preset the values started from; "custom" for an imported league that matches none. */
  preset: PresetName | "custom";
  scoring: Record<string, string>;
  slots: Record<string, number>;
  playoffWeekStart: string;
  teamName: string;
  players: string;
}

export type FieldErrors = Record<string, string>;

export const SCORING_MIN = -100;
export const SCORING_MAX = 100;
const PLAYER_ID = /^[A-Za-z0-9._-]{1,32}$/;

function scoringStrings(values: Record<string, number>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of SCORING_FIELDS) out[f.key] = String(values[f.key] ?? 0);
  for (const [k, v] of Object.entries(values)) if (!(k in out)) out[k] = String(v);
  return out;
}

export function presetScoring(preset: PresetName): Record<string, string> {
  return scoringStrings(PRESETS[preset]);
}

export function initialCreateValues(season: number): LeagueFormValues {
  return {
    name: "",
    season: String(season),
    numTeams: "10",
    preset: "ppr",
    scoring: presetScoring("ppr"),
    slots: countSlots(DEFAULT_ROSTER),
    playoffWeekStart: "15",
    teamName: "My team",
    players: "",
  };
}

export function initialEditValues(league: League): LeagueFormValues {
  const like = league.scoring.preset_like;
  const pws = league.settings?.playoff_week_start;
  return {
    name: league.name,
    season: String(league.season),
    numTeams: String(league.num_teams),
    preset: like === "ppr" || like === "half" || like === "std" ? like : "custom",
    scoring: scoringStrings(league.scoring.settings ?? {}),
    slots: countSlots(league.roster_positions),
    playoffWeekStart: typeof pws === "number" ? String(pws) : "",
    teamName: "",
    players: "",
  };
}

function intIn(value: string, lo: number, hi: number): boolean {
  if (!/^-?\d+$/.test(value.trim())) return false;
  const n = Number(value);
  return n >= lo && n <= hi;
}

export function parsePlayers(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function validateLeagueForm(v: LeagueFormValues, mode: FormMode): FieldErrors {
  const e: FieldErrors = {};
  const name = v.name.trim();
  if (!name) e.name = "Enter a league name.";
  else if (name.length > 100) e.name = "Use 100 characters or fewer.";

  if (mode === "create") {
    if (!intIn(v.season, 2017, 2100)) e.season = "Enter a season between 2017 and 2100.";
    if (!intIn(v.numTeams, 2, 32)) e.num_teams = "Enter a whole number of teams from 2 to 32.";
  }

  for (const [key, raw] of Object.entries(v.scoring)) {
    const t = raw.trim();
    const n = Number(t);
    if (t === "" || !Number.isFinite(n)) e[`scoring.${key}`] = "Enter a number.";
    else if (n < SCORING_MIN || n > SCORING_MAX) e[`scoring.${key}`] = `Enter a number between ${SCORING_MIN} and ${SCORING_MAX}.`;
  }

  const starting = SLOT_INFO.filter((s) => s.starting).reduce((sum, s) => sum + (v.slots[s.slot] ?? 0), 0);
  const total = Object.values(v.slots).reduce((a, b) => a + b, 0);
  const tooMany = Object.entries(v.slots).find(([, n]) => n > MAX_PER_SLOT || n < 0);
  if (starting < 1) e.roster = "Add at least one starting slot.";
  else if (total > MAX_ROSTER_SLOTS) e.roster = `A roster can have at most ${MAX_ROSTER_SLOTS} slots (now ${total}).`;
  else if (tooMany) e.roster = `Use between 0 and ${MAX_PER_SLOT} ${tooMany[0]} slots.`;

  if (v.playoffWeekStart.trim() && !intIn(v.playoffWeekStart, 1, 18)) {
    e.playoff_week_start = "Enter a week from 1 to 18, or leave it empty.";
  }

  if (mode === "create") {
    if (v.teamName.trim().length > 100) e.team_name = "Use 100 characters or fewer.";
    const players = parsePlayers(v.players);
    const bad = players.filter((p) => !PLAYER_ID.test(p));
    if (bad.length) e.players = `These do not look like player ids: ${bad.slice(0, 5).join(", ")}.`;
    else if (players.length > 60) e.players = "List at most 60 players.";
    else if (new Set(players).size !== players.length) e.players = "Each player can be listed once.";
  }
  return e;
}

function numbers(scoring: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(scoring)) out[k] = Number(v);
  return out;
}

function settingsPart(v: LeagueFormValues): Record<string, unknown> {
  return v.playoffWeekStart.trim() ? { playoff_week_start: Number(v.playoffWeekStart) } : {};
}

export function toManualBody(v: LeagueFormValues): ManualLeagueBody {
  const preset: PresetName = v.preset === "custom" ? "ppr" : v.preset;
  const values = numbers(v.scoring);
  return {
    name: v.name.trim(),
    season: Number(v.season),
    num_teams: Number(v.numTeams),
    scoring: { preset, overrides: overridesFor(preset, values) },
    roster_positions: slotsToPositions(v.slots),
    settings: settingsPart(v),
    teams: [{ name: v.teamName.trim() || "My team", is_me: true, players: parsePlayers(v.players) }],
  };
}

/** Only the parts that changed; scoring is sent as a full key map. */
export function toSettingsBody(v: LeagueFormValues, original: League): LeagueSettingsBody {
  const body: LeagueSettingsBody = {};
  if (v.name.trim() !== original.name) body.name = v.name.trim();
  const values = numbers(v.scoring);
  const before = original.scoring.settings ?? {};
  const changed =
    Object.keys({ ...values, ...before }).some((k) => (values[k] ?? 0) !== (before[k] ?? 0));
  if (changed) body.scoring = { settings: values };
  const positions = slotsToPositions(v.slots);
  const sameRoster =
    JSON.stringify([...positions].sort()) === JSON.stringify([...original.roster_positions].sort());
  if (!sameRoster) body.roster_positions = positions;
  const pws = v.playoffWeekStart.trim() ? Number(v.playoffWeekStart) : undefined;
  if (pws !== undefined && pws !== original.settings?.playoff_week_start) body.settings = { playoff_week_start: pws };
  return body;
}

const LOC_FIELD: Record<string, string> = {
  name: "name",
  season: "season",
  num_teams: "num_teams",
  roster_positions: "roster",
  scoring: "scoring",
  settings: "playoff_week_start",
  teams: "players",
};

/** Map a failed save to field errors plus a form-level message. */
export function serverErrors(result: ApiResult<unknown>): { fields: FieldErrors; form: string | null } {
  if (result.kind !== "error") return { fields: {}, form: null };
  const fields: FieldErrors = {};
  if (result.code === "invalid_scoring") fields.scoring = result.message;
  else if (result.code === "invalid_roster_positions") fields.roster = result.message;
  else if (result.code === "player_not_found") {
    const ids = (result.details as { player_ids?: string[] } | null)?.player_ids;
    fields.players = ids?.length ? `Unknown player ids: ${ids.join(", ")}.` : result.message;
  } else {
    for (const d of validationDetails(result)) {
      const [first, second, third] = d.loc;
      const key = first === "body" ? second : first;
      if (key === "scoring" && typeof third === "string" && third !== "preset" && third !== "overrides" && third !== "settings") {
        fields[`scoring.${third}`] = d.msg;
        continue;
      }
      const field = typeof key === "string" ? LOC_FIELD[key] : undefined;
      if (field && !fields[field]) fields[field] = d.msg;
    }
  }
  const form = Object.keys(fields).length ? null : result.message;
  return { fields, form };
}

export { IDP_SLOTS };
