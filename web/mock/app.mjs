// A small contract mock of the API in docs/API.md, for development and
// Playwright runs before the real endpoints land. No dependencies.
//
//   mode: "production"   gated routes answer 503 no_approved_model
//         "private_test" gated routes serve fixture data, labeled "not approved"
//
// Test controls: POST /__mock/reset clears leagues.

import { createServer } from "node:http";
import {
  ATTRIBUTION,
  FIXTURE_NOTICE,
  FIXTURE_RUN,
  MODEL_CARD,
  NO_APPROVED_MODEL_MESSAGE,
  NO_GUARANTEES,
  NO_PRIVATE_SNAPSHOT_MESSAGE,
  NOT_APPROVED_NOTICE,
  PLAYERS,
  REMAINING_WEEKS,
  SLEEPER_USERS,
  findPlayer,
  playerRow,
  rng,
} from "./fixtures.mjs";

const PRESETS = {
  std: {
    pass_yd: 0.04, pass_td: 4, pass_int: -2, pass_2pt: 2, rush_yd: 0.1, rush_td: 6, rush_2pt: 2, rec_yd: 0.1, rec_td: 6,
    rec_2pt: 2, fum_lost: -2, st_td: 6, fum_rec_td: 6, fgm_0_19: 3, fgm_20_29: 3, fgm_30_39: 3, fgm_40_49: 4, fgm_50_59: 5,
    fgm_60p: 5, fgmiss: -1, xpm: 1, xpmiss: -1, sack: 1, int: 2, fum_rec: 2, safe: 2, blk_kick: 2, def_td: 6, def_st_td: 6,
    pts_allow_0: 10, pts_allow_1_6: 7, pts_allow_7_13: 4, pts_allow_14_20: 1, pts_allow_28_34: -1, pts_allow_35p: -4,
  },
};
PRESETS.ppr = { ...PRESETS.std, rec: 1 };
PRESETS.half = { ...PRESETS.std, rec: 0.5 };

const SUPPORTED = new Set([...Object.keys(PRESETS.ppr), "pts_allow_21_27", "pass_cmp", "pass_att", "pass_inc", "rush_att", "rec_tgt",
  "bonus_rec_te", "bonus_rec_rb", "bonus_rec_wr", "fgm", "fgm_50p", "bonus_pass_yd_300", "bonus_pass_yd_400", "bonus_rush_yd_100",
  "bonus_rush_yd_200", "bonus_rec_yd_100", "bonus_rec_yd_200", "ff", "def_st_ff"]);

const SLOTS = new Set(["QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "WRRB_FLEX", "REC_FLEX", "K", "DEF", "BN", "IR", "TAXI",
  "DL", "LB", "DB", "IDP_FLEX"]);
const DEFAULT_ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN"];

function hashString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16).padStart(8, "0") + ((h * 31) >>> 0).toString(16).padStart(8, "0");
}

function normalizeScoring(input) {
  let settings;
  if (input && typeof input === "object" && input.settings && typeof input.settings === "object") settings = { ...input.settings };
  else if (input && PRESETS[input.preset]) settings = { ...PRESETS[input.preset], ...(input.overrides ?? {}) };
  else return null;
  for (const [k, v] of Object.entries(settings)) {
    if (typeof v !== "number" || !Number.isFinite(v)) return null;
    if (v === 0) delete settings[k];
  }
  const supported = Object.keys(settings).filter((k) => SUPPORTED.has(k)).sort();
  const rec = settings.rec ?? 0;
  return {
    settings,
    hash: hashString(supported.map((k) => `${k}=${settings[k]}`).join(",")).slice(0, 16),
    unsupported_keys: Object.keys(settings).filter((k) => !SUPPORTED.has(k)).sort(),
    preset_like: rec === 1 ? "ppr" : rec === 0.5 ? "half" : rec === 0 ? "std" : null,
  };
}

function startingSlots(positions) {
  const out = {};
  for (const p of positions) if (!["BN", "IR", "TAXI"].includes(p)) out[p] = (out[p] ?? 0) + 1;
  return out;
}

function err(status, code, message, details = null) {
  return { status, body: { error: { code, message, details } } };
}

function uuid(rand) {
  const hex = () => Math.floor(rand() * 16).toString(16);
  const part = (n) => Array.from({ length: n }, hex).join("");
  return `${part(8)}-${part(4)}-4${part(3)}-a${part(3)}-${part(12)}`;
}

export function createMockApi(options = {}) {
  const mode = options.mode === "production" ? "production" : "private_test";
  const opts = {
    seasonEndpoint: options.seasonEndpoint ?? true,
    seasonMode: options.seasonMode ?? "regular",
    planned: options.planned ?? true,
    snapshot: options.snapshot ?? true,
  };
  let leagues = new Map();
  let rand = rng(42);

  const privateTest = mode === "private_test";
  const serving = privateTest && opts.snapshot;

  function envelope(payload) {
    return {
      approval_status: "not approved",
      label: "not approved",
      environment: "private_test",
      run: FIXTURE_RUN,
      notices: [NOT_APPROVED_NOTICE, FIXTURE_NOTICE],
      ...payload,
    };
  }

  function leagueView(l, withTeams = true) {
    const { teams, ...rest } = l;
    return withTeams ? { ...rest, teams } : rest;
  }

  function rosterIndex(league) {
    const idx = new Map();
    if (!league) return idx;
    for (const t of league.teams) {
      for (const p of t.players) {
        if (p.player_id) idx.set(p.player_id, { league_team_id: t.id, team_name: t.team_name, is_mine: t.id === league.my_team_id });
      }
    }
    return idx;
  }

  function scaleFor(league) {
    // Fixture rows are PPR-like; other scoring scales receiving value a little.
    const rec = league?.scoring?.settings?.rec ?? 1;
    return (p) => (["WR", "TE", "RB"].includes(p.position) ? 1 - (1 - rec) * 0.18 : 1);
  }

  function scoredRows(league, scoringName) {
    const recFor = scoringName === "half" ? 0.5 : scoringName === "std" ? 0 : null;
    const scale = recFor === null ? scaleFor(league) : (p) => (["WR", "TE", "RB"].includes(p.position) ? 1 - (1 - recFor) * 0.18 : 1);
    const idx = rosterIndex(league);
    return PLAYERS.map((p) => {
      const s = scale(p);
      const row = playerRow(p, league ? (idx.get(p.player_id) ?? null) : undefined);
      if (!league) delete row.roster;
      for (const k of ["floor", "median", "ceiling", "mean"]) row[k] = Math.round(row[k] * s * 10) / 10;
      return row;
    });
  }

  function makeLeagueFromSleeper(summary, username) {
    const id = uuid(rand);
    const numTeams = summary.num_teams;
    const pool = [...PLAYERS].sort((a, b) => b.mean - a.mean);
    const teams = Array.from({ length: numTeams }, (_, i) => ({
      id: uuid(rand),
      external_roster_id: String(i + 1),
      team_name: i === 2 && username ? `Team ${username}` : `Fixture Team ${i + 1}`,
      owner_name: i === 2 && username ? username : `owner${i + 1}`,
      owner_external_id: String(1257514331365462000 + i),
      is_me: Boolean(username) && i === 2,
      players: [],
    }));
    // snake draft of the top players; the rest stay free agents
    const perTeam = 11;
    let pick = 0;
    for (let round = 0; round < perTeam; round++) {
      for (let j = 0; j < numTeams; j++) {
        const ti = round % 2 === 0 ? j : numTeams - 1 - j;
        const p = pool[pick++];
        if (!p) break;
        teams[ti].players.push({
          player_id: p.player_id,
          sleeper_id: p.sleeper_id,
          name: p.name,
          position: p.position === "DST" ? "DEF" : p.position,
          team: p.team,
          slot: round < 8 ? "starter" : "bench",
          injury_status: p.injury_status,
        });
      }
    }
    const scoring = normalizeScoring({ preset: summary.preset_like ?? "ppr" });
    const now = new Date().toISOString();
    return {
      id,
      source: "sleeper",
      external_id: summary.league_id,
      name: summary.name,
      season: summary.season ?? 2026,
      num_teams: numTeams,
      scoring,
      roster_positions: DEFAULT_ROSTER,
      starting_slots: startingSlots(DEFAULT_ROSTER),
      settings: { playoff_week_start: 15, waiver_type: 0, trade_deadline: 14 },
      my_team_id: teams.find((t) => t.is_me)?.id ?? null,
      created_at: now,
      updated_at: now,
      last_synced_at: now,
      teams,
    };
  }

  function validateManual(body) {
    const details = [];
    const add = (loc, msg, type = "value_error") => details.push({ loc: ["body", ...loc], msg, type });
    if (!body || typeof body !== "object") return [{ loc: ["body"], msg: "Field required", type: "missing" }];
    if (typeof body.name !== "string" || !body.name.trim()) add(["name"], "Field required", "missing");
    else if (body.name.length > 100) add(["name"], "String should have at most 100 characters", "string_too_long");
    if (!Number.isInteger(body.season) || body.season < 2017 || body.season > 2100) add(["season"], "Input should be between 2017 and 2100");
    if (!Number.isInteger(body.num_teams) || body.num_teams < 2 || body.num_teams > 32) add(["num_teams"], "Input should be between 2 and 32");
    if (!Array.isArray(body.roster_positions) || body.roster_positions.length < 1) add(["roster_positions"], "List should have at least 1 item");
    return details;
  }

  function checkSlots(positions) {
    const bad = positions.filter((p) => !SLOTS.has(p));
    if (bad.length) return err(422, "invalid_roster_positions", `Unknown roster slots: ${bad.join(", ")}.`, { invalid: bad });
    if (!positions.some((p) => !["BN", "IR", "TAXI"].includes(p))) return err(422, "invalid_roster_positions", "At least one starting slot is needed.");
    return null;
  }

  function gate() {
    if (!privateTest) {
      return { status: 503, headers: { "X-FFML-Approval": "none" }, body: { status: "no_approved_model", message: NO_APPROVED_MODEL_MESSAGE } };
    }
    if (!opts.snapshot) {
      return {
        status: 503,
        body: { status: "no_private_test_snapshot", message: NO_PRIVATE_SNAPSHOT_MESSAGE, approval_status: "not approved", label: "not approved" },
      };
    }
    return null;
  }

  function filterRows(rows, q, idx) {
    let out = rows;
    const position = q.get("position");
    if (position) out = out.filter((r) => r.position === position);
    const search = (q.get("search") ?? "").trim().toLowerCase();
    if (search) out = out.filter((r) => r.name.toLowerCase().includes(search));
    const availability = q.get("availability") ?? "all";
    if (idx) {
      if (availability === "rostered") out = out.filter((r) => r.roster);
      if (availability === "free_agents") out = out.filter((r) => !r.roster);
      if (availability === "mine") out = out.filter((r) => r.roster?.is_mine);
    }
    return out;
  }

  function sortRows(rows, sort) {
    const key = { median: "median", mean: "mean", ceiling: "ceiling", floor: "floor", boom: "boom_prob", bust: "bust_prob" }[sort] ?? "median";
    return [...rows].sort((a, b) => b[key] - a[key]);
  }

  function page(rows, q, maxLimit = 500, defLimit = 100) {
    const limit = Math.max(1, Math.min(maxLimit, Number(q.get("limit") ?? defLimit) || defLimit));
    const offset = Math.max(0, Number(q.get("offset") ?? 0) || 0);
    return { total: rows.length, players: rows.slice(offset, offset + limit) };
  }

  function projections(league, q, scoringName) {
    const week = q.get("week");
    if (week && Number(week) !== FIXTURE_RUN.week) {
      return err(404, "week_not_available", `Week ${week} is not in this run.`, { available_weeks: [FIXTURE_RUN.week] });
    }
    const rows = scoredRows(league, scoringName);
    const filtered = sortRows(filterRows(rows, q, league), q.get("sort"));
    const scoring = league ? { hash: league.scoring.hash, unsupported_keys: league.scoring.unsupported_keys, preset_like: league.scoring.preset_like } : normalizeScoring({ preset: scoringName ?? "ppr" });
    if (!league) delete scoring.settings;
    return {
      status: 200,
      body: envelope({ ...(league ? { league_id: league.id } : {}), season: FIXTURE_RUN.season, week: FIXTURE_RUN.week, scoring, ...page(filtered, q) }),
    };
  }

  function sdOf(r) {
    return Math.max(0.5, (r.ceiling - r.floor) / 2.5632);
  }

  function phi(x) {
    // Abramowitz-Stegun approximation of the standard normal CDF
    const t = 1 / (1 + 0.2316419 * Math.abs(x));
    const d = 0.3989423 * Math.exp((-x * x) / 2);
    const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
    return x > 0 ? 1 - p : p;
  }

  function startSit(league, body) {
    const ids = body?.player_ids;
    if (!Array.isArray(ids) || ids.length < 2 || ids.length > 4) {
      return err(422, "validation_error", "The request is not valid.", [{ loc: ["body", "player_ids"], msg: "List should have 2 to 4 items", type: "too_short" }]);
    }
    if (new Set(ids).size !== ids.length) {
      return err(422, "validation_error", "The request is not valid.", [{ loc: ["body", "player_ids"], msg: "Value error, player ids must be distinct", type: "value_error" }]);
    }
    const rows = scoredRows(league);
    const chosen = ids.map((id) => rows.find((r) => r.player_id === id));
    const missing = ids.filter((_, i) => !chosen[i]);
    if (missing.length) return err(404, "player_not_found", `No projection for ${missing.join(", ")}.`, { player_ids: missing });

    const pairwise = [];
    for (let i = 0; i < chosen.length; i++) {
      for (let j = i + 1; j < chosen.length; j++) {
        const a = chosen[i];
        const b = chosen[j];
        const p = phi((a.mean - b.mean) / Math.sqrt(sdOf(a) ** 2 + sdOf(b) ** 2));
        pairwise.push({ a: a.player_id, b: b.player_id, p_a_beats_b: Math.round(p * 1000) / 1000 });
      }
    }
    const r = rng(ids.join("").length * 7919);
    const wins = Object.fromEntries(chosen.map((c) => [c.player_id, 0]));
    const N = 2000;
    for (let s = 0; s < N; s++) {
      let best = null;
      let bestScore = -Infinity;
      for (const c of chosen) {
        const u1 = Math.max(1e-9, r());
        const u2 = r();
        const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
        const score = c.mean + z * sdOf(c);
        if (score > bestScore) {
          bestScore = score;
          best = c.player_id;
        }
      }
      wins[best] += 1;
    }
    const p_highest = Object.fromEntries(Object.entries(wins).map(([k, v]) => [k, Math.round((v / N) * 1000) / 1000]));
    const ranked = [...chosen].sort((a, b) => p_highest[b.player_id] - p_highest[a.player_id]);
    const top = ranked[0];
    const second = ranked[1];
    const pTop = p_highest[top.player_id];
    const strength = pTop < 0.55 ? "toss-up" : pTop <= 0.7 ? "lean" : "clear";
    const pct = Math.round(pTop * 100);
    const text =
      strength === "toss-up"
        ? `Toss-up: ${top.name} scores the most in ${pct}% of simulations, too close to call.`
        : `${strength === "lean" ? "Lean" : "Clear edge:"} ${top.name}: he scores the most in ${pct}% of simulations.`;
    const explanation = [
      `${top.name}'s edge over ${second.name} comes mostly from ${top.drivers[0]?.group ?? "opportunity"} (${(top.drivers[0]?.points ?? 0) >= 0 ? "+" : ""}${(top.drivers[0]?.points ?? 0).toFixed(1)} pts).`,
      `${second.name} has the ${second.ceiling > top.ceiling ? "higher" : "lower"} ceiling (${second.ceiling.toFixed(1)} vs ${top.ceiling.toFixed(1)}).`,
    ];
    return {
      status: 200,
      body: envelope({ week: FIXTURE_RUN.week, players: chosen, pairwise, p_highest, recommendation: { player_id: top.player_id, strength, text }, explanation, disclaimer: NO_GUARANTEES }),
    };
  }

  function waivers(league, q) {
    const teamId = q.get("league_team_id") ?? league.my_team_id;
    if (!teamId) return err(409, "no_team_selected", "Choose your team first, or pass league_team_id.");
    const team = league.teams.find((t) => t.id === teamId);
    if (!team) return err(404, "team_not_found", `No team ${teamId} in this league.`);
    const rows = scoredRows(league);
    const mineIds = new Set(team.players.map((p) => p.player_id));
    const mine = rows.filter((r) => mineIds.has(r.player_id));
    const position = q.get("position");
    const fas = rows.filter((r) => !r.roster && (!position || r.position === position)).sort((a, b) => b.mean - a.mean);
    const drops = [...mine].sort((a, b) => a.mean - b.mean).slice(0, 4).map((r, i) => ({ player_id: r.player_id, name: r.name, position: r.position, team: r.team, lineup_loss: Math.round((0.2 + i * 0.6) * 10) / 10 }));
    const limit = Math.max(1, Math.min(100, Number(q.get("limit") ?? 25) || 25));
    const options = fas.slice(0, limit).map((add, i) => {
      const drop = drops[i % Math.max(1, drops.length)] ?? null;
      const gainNext = Math.max(0, Math.round((add.mean - (drop ? 6 + drop.lineup_loss : 6)) * 0.4 * 10) / 10);
      const gainRos = Math.round(gainNext * 6.2 * 10) / 10;
      const weeksStarted = Math.max(0, Math.min(9, Math.round(gainRos / 1.4)));
      return {
        add,
        drop,
        gain_next_week: gainNext,
        gain_rest_of_season: gainRos,
        score: Math.round((gainNext + gainRos) * 10) / 10,
        reason:
          weeksStarted > 0
            ? `Would start in ${weeksStarted} of ${REMAINING_WEEKS.length} remaining weeks; adds ${gainRos.toFixed(1)} expected lineup points.`
            : "Would rarely crack your lineup; depth only.",
      };
    }).sort((a, b) => b.score - a.score);
    return {
      status: 200,
      body: envelope({ league_team_id: teamId, week: FIXTURE_RUN.week, weeks: REMAINING_WEEKS, options, drop_candidates: drops, disclaimer: NO_GUARANTEES }),
    };
  }

  const REPLACEMENT = { QB: 160.2, RB: 80.1, WR: 85.3, TE: 60.0, K: 70.2, DST: 65.1 };

  function trade(league, body) {
    const give = body?.give;
    const receive = body?.receive;
    if (!Array.isArray(give) || !Array.isArray(receive) || give.length < 1 || receive.length < 1) {
      return err(422, "validation_error", "The request is not valid.", [{ loc: ["body", give?.length ? "receive" : "give"], msg: "List should have at least 1 item", type: "too_short" }]);
    }
    const myId = body.league_team_id ?? league.my_team_id;
    if (!myId) return err(409, "no_team_selected", "Choose your team first, or pass league_team_id.");
    const rows = scoredRows(league);
    const side = (ids) => ids.map((id) => rows.find((r) => r.player_id === id));
    const g = side(give);
    const rcv = side(receive);
    const missing = [...give, ...receive].filter((id) => !rows.some((r) => r.player_id === id));
    if (missing.length) return err(404, "player_not_found", `No projection for ${missing.join(", ")}.`, { player_ids: missing });
    const val = (r) => {
      const ros = Math.round(r.mean * 8.4 * 10) / 10;
      return { player: r, ros_mean: ros, value_over_replacement: Math.round((ros - (REPLACEMENT[r.position] ?? 70)) * 10) / 10 };
    };
    const gv = g.map(val);
    const rv = rcv.map(val);
    const sum = (xs) => Math.round(xs.reduce((s, x) => s + Math.max(0, x.value_over_replacement), 0) * 10) / 10;
    const vg = sum(gv);
    const vr = sum(rv);
    const net = Math.round((vr - vg) * 10) / 10;
    const band = Math.abs(net) < 8 ? "fair" : net > 0 ? (net > 30 ? "lopsided_for_you" : "favors_you") : net < -30 ? "lopsided_for_partner" : "favors_partner";
    const text =
      band === "fair"
        ? `This trade is close to even: the difference is about ${Math.abs(Math.round(net))} rest-of-season points over replacement.`
        : `This trade ${net > 0 ? "favors you" : "favors your partner"} by about ${Math.abs(Math.round(net))} rest-of-season points over replacement.`;
    const before = 1040.2;
    const change = Math.round(net * 0.7 * 10) / 10;
    return {
      status: 200,
      body: envelope({
        weeks: REMAINING_WEEKS,
        give: gv,
        receive: rv,
        value: { give: vg, receive: vr, net },
        roster_fit: {
          mine: { before, after: Math.round((before + change) * 10) / 10, change },
          partner: { before: 990.1, after: Math.round((990.1 - change * 0.6) * 10) / 10, change: Math.round(-change * 0.6 * 10) / 10 },
        },
        replacement_level: REPLACEMENT,
        fairness: { band, difference: net, text },
        disclaimer: NO_GUARANTEES,
      }),
    };
  }

  function ros(league, q, scoringName) {
    const rows = scoredRows(league, scoringName);
    const idx = league ? rosterIndex(league) : null;
    let list = rows.map((r) => {
      const p = PLAYERS.find((x) => x.player_id === r.player_id);
      const games = REMAINING_WEEKS.filter((w) => w !== p.bye_week).length;
      const m = Math.round(r.mean * games * 10) / 10;
      const spread = Math.sqrt(games) * (r.ceiling - r.floor) * 0.39;
      const out = {
        player_id: r.player_id,
        name: r.name,
        position: r.position,
        team: r.team,
        ros_mean: m,
        ros_floor: Math.round((m - spread) * 10) / 10,
        ros_median: Math.round((m - spread * 0.05) * 10) / 10,
        ros_ceiling: Math.round((m + spread * 1.05) * 10) / 10,
        games_remaining: games,
        bye_weeks: REMAINING_WEEKS.includes(p.bye_week) ? [p.bye_week] : [],
        value_over_replacement: Math.round((m - (REPLACEMENT[r.position] ?? 70) * games / 9) * 10) / 10,
        overall_rank: 0,
        position_rank: 0,
      };
      if (league) out.roster = idx.get(r.player_id) ?? null;
      return out;
    });
    list.sort((a, b) => b.value_over_replacement - a.value_over_replacement);
    list.forEach((r, i) => (r.overall_rank = i + 1));
    const byPos = {};
    for (const r of [...list].sort((a, b) => b.ros_mean - a.ros_mean)) r.position_rank = byPos[r.position] = (byPos[r.position] ?? 0) + 1;
    const position = q.get("position");
    if (position) list = list.filter((r) => r.position === position);
    const availability = q.get("availability") ?? "all";
    if (league) {
      if (availability === "rostered") list = list.filter((r) => r.roster);
      if (availability === "free_agents") list = list.filter((r) => !r.roster);
      if (availability === "mine") list = list.filter((r) => r.roster?.is_mine);
    }
    const { total, players } = page(list, q);
    return { status: 200, body: envelope({ first_week: REMAINING_WEEKS[0], last_week: REMAINING_WEEKS.at(-1), total, players }) };
  }

  function seasonBody() {
    const m = opts.seasonMode;
    return {
      season: 2026,
      mode: m,
      projection_week: m === "regular" ? 10 : null,
      week_in_progress: false,
      offseason: m === "offseason",
      banner: m === "regular" ? null : m === "offseason" ? "Offseason: weekly features are paused; rankings are season-level." : "Postseason: weekly features are paused; rankings are season-level.",
      as_of: new Date().toISOString(),
    };
  }

  function approvalBody() {
    const reason = privateTest ? (opts.snapshot ? "private_test_fixture" : "no_private_test_snapshot") : "no_live_model";
    const body = {
      environment: mode,
      serving,
      approval_status: "not approved",
      label: "not approved",
      reason,
      message: privateTest ? (opts.snapshot ? NOT_APPROVED_NOTICE : NO_PRIVATE_SNAPSHOT_MESSAGE) : NO_APPROVED_MODEL_MESSAGE,
      checked_at: new Date().toISOString(),
      run: serving ? FIXTURE_RUN : null,
    };
    if (!privateTest) body.live_model_version = null;
    return body;
  }

  /** Route one request. Returns {status, body, headers?}. */
  function handle(method, pathname, q, body) {
    const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
    const [a, b, c, d] = parts;

    if (method === "POST" && a === "__mock" && b === "reset") {
      leagues = new Map();
      rand = rng(42);
      return { status: 204, body: null };
    }

    if (a === "meta" && method === "GET") {
      if (b === "health") return { status: 200, body: { status: "ok", version: "0.1.0-mock", environment: mode, checks: { database: "ok", projections: serving ? "serving" : "off" }, time: new Date().toISOString() } };
      if (b === "approval") return { status: 200, body: approvalBody() };
      if (b === "season" && opts.seasonEndpoint) return { status: 200, body: seasonBody() };
      if (b === "attribution" && opts.planned) return { status: 200, body: ATTRIBUTION };
      if (b === "model-card" && opts.planned) return { status: 200, body: MODEL_CARD };
      if (b === "run-info" && opts.planned) {
        const ap = approvalBody();
        return { status: 200, body: { environment: mode, serving, approval_status: "not approved", label: "not approved", run: serving ? { ...FIXTURE_RUN, seeds: [1, 2], as_of: FIXTURE_RUN.written_at, rest_of_season_weeks: REMAINING_WEEKS } : null, message: ap.message } };
      }
      return err(404, "not_found", "Not Found");
    }

    if (a === "sleeper" && b === "users" && d === "leagues" && method === "GET") {
      const user = SLEEPER_USERS[(c ?? "").toLowerCase()];
      if ((c ?? "").toLowerCase() === "sleeperdown") return err(502, "sleeper_unavailable", "Sleeper did not respond after 3 tries.");
      if (!user) return err(404, "sleeper_not_found", `Sleeper has no user named ${c}.`);
      const season = Number(q.get("season") ?? 2026);
      return { status: 200, body: { user: user.user, season, leagues: user.leagues.map((l) => ({ ...l, season })) } };
    }

    // gated routes (section 3.1): the gate runs before anything else
    const gated =
      (method === "GET" && (pathname === "/projections" || pathname === "/ros")) ||
      (a === "leagues" && b && b !== "import" && ((method === "GET" && ["projections", "waivers", "ros"].includes(c)) || (method === "POST" && (c === "start-sit" || (c === "trades" && d === "evaluate")))));
    if (gated) {
      const blocked = gate();
      if (blocked) return blocked;
      if (pathname === "/projections") return projections(null, q, q.get("scoring") ?? "ppr");
      if (pathname === "/ros") return ros(null, q, q.get("scoring") ?? "ppr");
      const league = leagues.get(b);
      if (!league) return err(404, "league_not_found", `No league with id ${b}.`);
      if (c === "projections") return projections(league, q);
      if (c === "waivers") return waivers(league, q);
      if (c === "ros") return ros(league, q);
      if (c === "start-sit") return startSit(league, body);
      if (c === "trades") return trade(league, body);
    }

    if (a === "leagues") {
      if (!b) {
        if (method === "GET") return { status: 200, body: { leagues: [...leagues.values()].map((l) => leagueView(l, false)) } };
        if (method === "POST") {
          const details = validateManual(body);
          if (details.length) return err(422, "validation_error", "The request is not valid.", details);
          const scoring = normalizeScoring(body.scoring);
          if (!scoring) return err(422, "invalid_scoring", "Scoring must be a preset (ppr, half, std) with numeric overrides, or a map of numeric settings.");
          const bad = checkSlots(body.roster_positions);
          if (bad) return bad;
          const id = uuid(rand);
          const now = new Date().toISOString();
          const teams = (body.teams?.length ? body.teams : [{ name: "My team", is_me: true, players: [] }]).map((t) => ({
            id: uuid(rand),
            external_roster_id: null,
            team_name: t.name,
            owner_name: null,
            owner_external_id: null,
            is_me: Boolean(t.is_me),
            players: (t.players ?? []).map((pid) => findPlayer(pid)).filter(Boolean).map((p) => ({ player_id: p.player_id, sleeper_id: p.sleeper_id, name: p.name, position: p.position, team: p.team, slot: "bench", injury_status: null })),
          }));
          const league = {
            id, source: "manual", external_id: null, name: body.name.trim(), season: body.season, num_teams: body.num_teams, scoring,
            roster_positions: body.roster_positions, starting_slots: startingSlots(body.roster_positions), settings: body.settings ?? {},
            my_team_id: teams.find((t) => t.is_me)?.id ?? null, created_at: now, updated_at: now, last_synced_at: null, teams,
          };
          leagues.set(id, league);
          return { status: 201, body: leagueView(league) };
        }
      }
      if (b === "import" && c === "sleeper" && method === "POST") {
        const username = typeof body?.username === "string" ? body.username.toLowerCase() : null;
        const leagueId = body?.league_id ?? null;
        if (!username && !leagueId) return err(422, "validation_error", "The request is not valid.", [{ loc: ["body"], msg: "Give a league_id or a username", type: "value_error" }]);
        if (username === "sleeperdown") return err(502, "sleeper_unavailable", "Sleeper did not respond after 3 tries.");
        let summary = null;
        if (username) {
          const user = SLEEPER_USERS[username];
          if (!user) return err(404, "sleeper_not_found", `Sleeper has no user named ${body.username}.`);
          if (!leagueId && user.leagues.length > 1) {
            return err(409, "multiple_leagues", "This user has several leagues; pick one.", { leagues: user.leagues.map((l) => ({ ...l, season: body.season ?? 2026 })) });
          }
          summary = leagueId ? user.leagues.find((l) => l.league_id === leagueId) : user.leagues[0];
        } else {
          summary = Object.values(SLEEPER_USERS).flatMap((u) => u.leagues).find((l) => l.league_id === leagueId) ?? null;
        }
        if (!summary) return err(404, "sleeper_not_found", `Sleeper has no league ${leagueId}.`);
        const existing = [...leagues.values()].find((l) => l.external_id === summary.league_id);
        if (existing) {
          existing.last_synced_at = new Date().toISOString();
          return { status: 200, body: leagueView(existing) };
        }
        const league = makeLeagueFromSleeper({ ...summary, season: body.season ?? 2026 }, username);
        leagues.set(league.id, league);
        return { status: 201, body: leagueView(league) };
      }
      const league = leagues.get(b);
      if (!league) return err(404, "league_not_found", `No league with id ${b}.`);
      if (!c) {
        if (method === "GET") return { status: 200, body: leagueView(league) };
        if (method === "DELETE") {
          leagues.delete(b);
          return { status: 204, body: null };
        }
      }
      if (c === "settings" && method === "PUT") {
        if (body?.name !== undefined) {
          if (typeof body.name !== "string" || !body.name.trim()) return err(422, "validation_error", "The request is not valid.", [{ loc: ["body", "name"], msg: "String should have at least 1 character", type: "string_too_short" }]);
          league.name = body.name.trim();
        }
        if (body?.scoring !== undefined) {
          const scoring = normalizeScoring(body.scoring);
          if (!scoring) return err(422, "invalid_scoring", "Scoring must be a preset (ppr, half, std) with numeric overrides, or a map of numeric settings.");
          league.scoring = scoring;
        }
        if (body?.roster_positions !== undefined) {
          const bad = checkSlots(body.roster_positions);
          if (bad) return bad;
          league.roster_positions = body.roster_positions;
          league.starting_slots = startingSlots(body.roster_positions);
        }
        if (body?.settings !== undefined) league.settings = { ...league.settings, ...body.settings };
        league.updated_at = new Date().toISOString();
        return { status: 200, body: leagueView(league) };
      }
      if (c === "my-team" && method === "PUT") {
        const team = league.teams.find((t) => t.id === body?.league_team_id);
        if (!team) return err(404, "team_not_found", `No team ${body?.league_team_id} in this league.`);
        league.my_team_id = team.id;
        for (const t of league.teams) t.is_me = t.id === team.id;
        return { status: 200, body: leagueView(league) };
      }
      if (c === "sync" && method === "POST") {
        if (league.source !== "sleeper") return err(409, "not_a_sleeper_league", "Manual leagues cannot be synced with Sleeper.");
        league.last_synced_at = new Date().toISOString();
        return { status: 200, body: leagueView(league) };
      }
    }

    return err(404, "not_found", "Not Found");
  }

  return { mode, options: opts, handle };
}

export function startMockServer({ port = 8765, host = "127.0.0.1", ...options } = {}) {
  const mock = createMockApi(options);
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", `http://${host}`);
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      let body;
      const raw = Buffer.concat(chunks).toString("utf8");
      if (raw) {
        try {
          body = JSON.parse(raw);
        } catch {
          return send(res, mock.mode, err(422, "validation_error", "The request body is not valid JSON.", [{ loc: ["body"], msg: "JSON decode error", type: "json_invalid" }]));
        }
      }
      let out;
      try {
        out = mock.handle((req.method ?? "GET").toUpperCase(), url.pathname, url.searchParams, body);
      } catch (e) {
        console.error(e);
        out = err(500, "internal_error", "Something went wrong on the server.");
      }
      send(res, mock.mode, out);
    });
  });
  server.listen(port, host);
  return server;
}

function send(res, mode, out) {
  const headers = { "Content-Type": "application/json", "Cache-Control": "no-store", ...(out.headers ?? {}) };
  if (mode === "private_test") headers["X-FFML-Approval"] = "not-approved";
  res.writeHead(out.status, headers);
  res.end(out.body === null || out.status === 204 ? undefined : JSON.stringify(out.body));
}
