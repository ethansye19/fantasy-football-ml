/**
 * Scoring keys and presets for the settings form. Key names are Sleeper's
 * (docs/API.md section 6); presets mirror ffml/scoring/rules.py. The API
 * is the authority: it normalizes, hashes and reports unsupported keys.
 */
import type { PresetName } from "./api/types";

export interface ScoringField {
  key: string;
  label: string;
  group: "Passing" | "Rushing" | "Receiving" | "Kicking" | "Defense" | "Other";
  step: number;
  /** Shown under "More scoring options". */
  advanced?: boolean;
}

export const SCORING_FIELDS: ScoringField[] = [
  { key: "pass_yd", label: "Per passing yard", group: "Passing", step: 0.01 },
  { key: "pass_td", label: "Passing TD", group: "Passing", step: 0.5 },
  { key: "pass_int", label: "Interception thrown", group: "Passing", step: 0.5 },
  { key: "pass_2pt", label: "Passing 2-pt conversion", group: "Passing", step: 0.5, advanced: true },
  { key: "pass_cmp", label: "Completion", group: "Passing", step: 0.1, advanced: true },
  { key: "pass_inc", label: "Incompletion", group: "Passing", step: 0.1, advanced: true },
  { key: "bonus_pass_yd_300", label: "300-399 passing yards bonus", group: "Passing", step: 0.5, advanced: true },
  { key: "bonus_pass_yd_400", label: "400+ passing yards bonus", group: "Passing", step: 0.5, advanced: true },

  { key: "rush_yd", label: "Per rushing yard", group: "Rushing", step: 0.01 },
  { key: "rush_td", label: "Rushing TD", group: "Rushing", step: 0.5 },
  { key: "rush_2pt", label: "Rushing 2-pt conversion", group: "Rushing", step: 0.5, advanced: true },
  { key: "rush_att", label: "Rush attempt", group: "Rushing", step: 0.1, advanced: true },
  { key: "bonus_rush_yd_100", label: "100-199 rushing yards bonus", group: "Rushing", step: 0.5, advanced: true },
  { key: "bonus_rush_yd_200", label: "200+ rushing yards bonus", group: "Rushing", step: 0.5, advanced: true },

  { key: "rec", label: "Reception", group: "Receiving", step: 0.25 },
  { key: "rec_yd", label: "Per receiving yard", group: "Receiving", step: 0.01 },
  { key: "rec_td", label: "Receiving TD", group: "Receiving", step: 0.5 },
  { key: "bonus_rec_te", label: "Extra per TE reception", group: "Receiving", step: 0.25 },
  { key: "rec_2pt", label: "Receiving 2-pt conversion", group: "Receiving", step: 0.5, advanced: true },
  { key: "bonus_rec_rb", label: "Extra per RB reception", group: "Receiving", step: 0.25, advanced: true },
  { key: "bonus_rec_wr", label: "Extra per WR reception", group: "Receiving", step: 0.25, advanced: true },
  { key: "bonus_rec_yd_100", label: "100-199 receiving yards bonus", group: "Receiving", step: 0.5, advanced: true },
  { key: "bonus_rec_yd_200", label: "200+ receiving yards bonus", group: "Receiving", step: 0.5, advanced: true },

  { key: "fum_lost", label: "Fumble lost", group: "Other", step: 0.5 },
  { key: "st_td", label: "Return TD (offense)", group: "Other", step: 0.5, advanced: true },
  { key: "fum_rec_td", label: "Fumble recovery TD (offense)", group: "Other", step: 0.5, advanced: true },

  { key: "fgm_0_19", label: "FG made 0-19 yds", group: "Kicking", step: 0.5, advanced: true },
  { key: "fgm_20_29", label: "FG made 20-29 yds", group: "Kicking", step: 0.5, advanced: true },
  { key: "fgm_30_39", label: "FG made 30-39 yds", group: "Kicking", step: 0.5 },
  { key: "fgm_40_49", label: "FG made 40-49 yds", group: "Kicking", step: 0.5 },
  { key: "fgm_50_59", label: "FG made 50-59 yds", group: "Kicking", step: 0.5 },
  { key: "fgm_60p", label: "FG made 60+ yds", group: "Kicking", step: 0.5, advanced: true },
  { key: "fgmiss", label: "FG missed", group: "Kicking", step: 0.5 },
  { key: "xpm", label: "Extra point made", group: "Kicking", step: 0.5 },
  { key: "xpmiss", label: "Extra point missed", group: "Kicking", step: 0.5, advanced: true },

  { key: "sack", label: "Sack", group: "Defense", step: 0.5 },
  { key: "int", label: "Interception", group: "Defense", step: 0.5 },
  { key: "fum_rec", label: "Fumble recovery", group: "Defense", step: 0.5 },
  { key: "def_td", label: "Defensive TD", group: "Defense", step: 0.5 },
  { key: "safe", label: "Safety", group: "Defense", step: 0.5, advanced: true },
  { key: "blk_kick", label: "Blocked kick", group: "Defense", step: 0.5, advanced: true },
  { key: "def_st_td", label: "Special teams TD", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_0", label: "0 points allowed", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_1_6", label: "1-6 points allowed", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_7_13", label: "7-13 points allowed", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_14_20", label: "14-20 points allowed", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_21_27", label: "21-27 points allowed", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_28_34", label: "28-34 points allowed", group: "Defense", step: 0.5, advanced: true },
  { key: "pts_allow_35p", label: "35+ points allowed", group: "Defense", step: 0.5, advanced: true },
];

const BASE: Record<string, number> = {
  pass_yd: 0.04, pass_td: 4, pass_int: -2, pass_2pt: 2,
  rush_yd: 0.1, rush_td: 6, rush_2pt: 2,
  rec_yd: 0.1, rec_td: 6, rec_2pt: 2,
  fum_lost: -2, st_td: 6, fum_rec_td: 6,
  fgm_0_19: 3, fgm_20_29: 3, fgm_30_39: 3, fgm_40_49: 4, fgm_50_59: 5, fgm_60p: 5, fgmiss: -1, xpm: 1, xpmiss: -1,
  sack: 1, int: 2, fum_rec: 2, safe: 2, blk_kick: 2, def_td: 6, def_st_td: 6,
  pts_allow_0: 10, pts_allow_1_6: 7, pts_allow_7_13: 4, pts_allow_14_20: 1, pts_allow_21_27: 0, pts_allow_28_34: -1, pts_allow_35p: -4,
};

export const PRESETS: Record<PresetName, Record<string, number>> = {
  ppr: { ...BASE, rec: 1 },
  half: { ...BASE, rec: 0.5 },
  std: { ...BASE },
};

export const PRESET_LABELS: Record<PresetName, string> = {
  ppr: "PPR (1 per reception)",
  half: "Half PPR (0.5 per reception)",
  std: "Standard (no reception points)",
};

export function presetValue(preset: PresetName, key: string): number {
  return PRESETS[preset][key] ?? 0;
}

/** Overrides relative to a preset: keys whose value differs. */
export function overridesFor(preset: PresetName, values: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(values)) {
    if (v !== presetValue(preset, k)) out[k] = v;
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Roster slots                                                        */
/* ------------------------------------------------------------------ */

export interface SlotInfo {
  slot: string;
  label: string;
  hint?: string;
  starting: boolean;
}

export const SLOT_INFO: SlotInfo[] = [
  { slot: "QB", label: "Quarterback", starting: true },
  { slot: "RB", label: "Running back", starting: true },
  { slot: "WR", label: "Wide receiver", starting: true },
  { slot: "TE", label: "Tight end", starting: true },
  { slot: "FLEX", label: "Flex", hint: "RB, WR or TE", starting: true },
  { slot: "SUPER_FLEX", label: "Superflex", hint: "QB, RB, WR or TE", starting: true },
  { slot: "WRRB_FLEX", label: "WR/RB flex", hint: "WR or RB", starting: true },
  { slot: "REC_FLEX", label: "Receiver flex", hint: "WR or TE", starting: true },
  { slot: "K", label: "Kicker", starting: true },
  { slot: "DEF", label: "Team defense", starting: true },
  { slot: "BN", label: "Bench", starting: false },
  { slot: "IR", label: "Injured reserve", starting: false },
  { slot: "TAXI", label: "Taxi squad", starting: false },
];

export const IDP_SLOTS = ["DL", "LB", "DB", "IDP_FLEX"];
export const MAX_ROSTER_SLOTS = 40;
export const MAX_PER_SLOT = 15;

export const DEFAULT_ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN", "BN"];

export function countSlots(positions: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of positions) out[p] = (out[p] ?? 0) + 1;
  return out;
}

/** Counts back to a roster list in a stable order; unknown slots (IDP) are kept at the end. */
export function slotsToPositions(counts: Record<string, number>): string[] {
  const order = [...SLOT_INFO.map((s) => s.slot), ...IDP_SLOTS];
  const extra = Object.keys(counts).filter((k) => !order.includes(k));
  const out: string[] = [];
  for (const slot of [...order, ...extra]) {
    for (let i = 0; i < (counts[slot] ?? 0); i++) out.push(slot);
  }
  return out;
}
