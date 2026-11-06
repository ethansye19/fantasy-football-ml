import { describe, expect, it } from "vitest";
import type { League } from "@/lib/api/types";
import {
  initialCreateValues,
  initialEditValues,
  serverErrors,
  toManualBody,
  toSettingsBody,
  validateLeagueForm,
} from "@/lib/league-form";
import { slotsToPositions } from "@/lib/scoring";

const pick = (i: number) => ({ player_id: `00-${String(i).padStart(7, "0")}`, name: `Player ${i}`, position: "WR", team: "MIN" });

const LEAGUE: League = {
  id: "L1",
  source: "sleeper",
  external_id: "1",
  name: "Test League",
  season: 2026,
  num_teams: 8,
  scoring: { settings: { pass_td: 4, rec: 1, rec_yd: 0.1, st_fum_rec: 1 }, hash: "h", unsupported_keys: ["st_fum_rec"], preset_like: "ppr" },
  roster_positions: ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "DL"],
  starting_slots: {},
  settings: { playoff_week_start: 15 },
  my_team_id: null,
  created_at: "",
  updated_at: "",
  last_synced_at: null,
};

describe("validateLeagueForm", () => {
  it("accepts the defaults once a name is given", () => {
    const v = { ...initialCreateValues(2026), name: "Office league" };
    expect(validateLeagueForm(v, "create")).toEqual({});
  });

  it("flags missing name, bad season and team count", () => {
    const v = { ...initialCreateValues(2026), name: " ", season: "1999", numTeams: "40" };
    const e = validateLeagueForm(v, "create");
    expect(Object.keys(e).sort()).toEqual(["name", "num_teams", "season"]);
  });

  it("flags non-numeric and out-of-range scoring values", () => {
    const v = initialCreateValues(2026);
    v.name = "x";
    v.scoring = { ...v.scoring, pass_td: "four", rec: "500" };
    const e = validateLeagueForm(v, "create");
    expect(e["scoring.pass_td"]).toMatch(/number/);
    expect(e["scoring.rec"]).toMatch(/between/);
  });

  it("needs a starting slot and caps the roster size", () => {
    const v = { ...initialCreateValues(2026), name: "x", slots: { BN: 5 } };
    expect(validateLeagueForm(v, "create").roster).toMatch(/starting slot/);
    const big = { ...initialCreateValues(2026), name: "x", slots: { QB: 15, RB: 15, WR: 15 } };
    expect(validateLeagueForm(big, "create").roster).toMatch(/at most 40/);
  });

  it("checks the playoff week and the player list", () => {
    const v = { ...initialCreateValues(2026), name: "x", playoffWeekStart: "19", players: Array.from({ length: 61 }, (_, i) => pick(i)) };
    const e = validateLeagueForm(v, "create");
    expect(e.playoff_week_start).toBeDefined();
    expect(e.players).toMatch(/at most 60/);
    expect(validateLeagueForm({ ...v, playoffWeekStart: "", players: [pick(1), pick(1)] }, "create").players).toMatch(/once/);
  });

  it("does not validate create-only fields in edit mode", () => {
    const v = { ...initialEditValues(LEAGUE), season: "nope", numTeams: "nope", players: [pick(1), pick(1)] };
    expect(validateLeagueForm(v, "edit")).toEqual({});
  });
});

describe("request bodies", () => {
  it("sends a preset with only the changed keys as overrides", () => {
    const v = { ...initialCreateValues(2026), name: " Office ", preset: "half" as const };
    v.scoring = { ...v.scoring, rec: "0.5", pass_td: "6" };
    const body = toManualBody(v);
    expect(body.name).toBe("Office");
    expect(body.scoring).toEqual({ preset: "half", overrides: { pass_td: 6 } });
    expect(body.roster_positions).toContain("FLEX");
    expect(body.teams?.[0]).toEqual({ name: "My team", is_me: true, players: [] });
    expect(body.settings).toEqual({ playoff_week_start: 15 });
  });

  it("sends picked players as ids only", () => {
    const v = { ...initialCreateValues(2026), name: "x", teamName: " Mine ", players: [pick(1), { ...pick(2), player_id: "KC", position: "DST", team: "KC" }] };
    expect(toManualBody(v).teams?.[0]).toEqual({ name: "Mine", is_me: true, players: ["00-0000001", "KC"] });
  });

  it("sends only what changed when editing, keeping unknown keys", () => {
    const v = initialEditValues(LEAGUE);
    expect(toSettingsBody(v, LEAGUE)).toEqual({});
    v.scoring = { ...v.scoring, pass_td: "6" };
    v.slots = { ...v.slots, BN: 3 };
    const body = toSettingsBody(v, LEAGUE);
    expect(body.scoring && "settings" in body.scoring ? body.scoring.settings.pass_td : null).toBe(6);
    expect(body.scoring && "settings" in body.scoring ? body.scoring.settings.st_fum_rec : null).toBe(1);
    expect(body.roster_positions?.filter((p) => p === "BN")).toHaveLength(3);
    expect(body.roster_positions).toContain("DL");
    expect(body.name).toBeUndefined();
  });

  it("orders roster slots", () => {
    expect(slotsToPositions({ BN: 1, QB: 1, FLEX: 1 })).toEqual(["QB", "FLEX", "BN"]);
  });
});

describe("serverErrors", () => {
  it("maps 422 details to fields", () => {
    const r = {
      kind: "error" as const,
      status: 422,
      code: "validation_error",
      message: "invalid",
      details: [
        { loc: ["body", "num_teams"], msg: "must be at least 2", type: "x" },
        { loc: ["body", "scoring", "pass_td"], msg: "not a number", type: "x" },
      ],
      approval: null,
    };
    expect(serverErrors(r)).toEqual({ fields: { num_teams: "must be at least 2", "scoring.pass_td": "not a number" }, form: null });
  });

  it("maps invalid_roster_positions, invalid_scoring and player_not_found", () => {
    const base = { kind: "error" as const, status: 422, details: null, approval: null };
    expect(serverErrors({ ...base, code: "invalid_roster_positions", message: "no starting slot" }).fields.roster).toBe("no starting slot");
    expect(serverErrors({ ...base, code: "invalid_scoring", message: "bad" }).fields.scoring).toBe("bad");
    expect(
      serverErrors({ ...base, code: "player_not_found", message: "Unknown", details: { player_ids: ["x1"] } }).fields.players,
    ).toMatch(/x1/);
  });

  it("falls back to a form-level message", () => {
    expect(serverErrors({ kind: "error", status: 500, code: "internal_error", message: "boom", details: null, approval: null })).toEqual({
      fields: {},
      form: "boom",
    });
  });
});
