// Fixture data that follows docs/API.md. Test-only: fictional players and
// made-up numbers, always served with the "not approved" envelope and a
// "Fixture data for testing" notice. Never used by the app itself.

export const NOT_APPROVED_NOTICE =
  "Not approved: these numbers come from a model that has not passed its pre-registered test. Private test environment only; not a recommendation.";
export const FIXTURE_NOTICE = "Fixture data for testing; not real projections.";
export const NO_GUARANTEES =
  "No guarantees: these are probabilities from simulations of possible outcomes, not predictions of what will happen. Use them as one input to your own decision.";
export const NO_APPROVED_MODEL_MESSAGE =
  "No approved model. Projections are off until a model passes its pre-registered test.";
export const NO_PRIVATE_SNAPSHOT_MESSAGE =
  "Private test environment: no private-test, replay or fixture predictions snapshot was found. Nothing is served.";

export const FIXTURE_RUN = {
  source: "fixture",
  run_id: "fixture-2026w10-20261104T000000Z",
  season: 2026,
  week: 10,
  model_version: "chain_v2-fixture",
  written_at: "2026-11-04T00:00:00Z",
  n_sims: 2000,
  is_replay: false,
  has_rest_of_season: true,
};

export const REMAINING_WEEKS = [10, 11, 12, 13, 14, 15, 16, 17, 18];

const TEAMS = [
  "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
  "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
];

const FIRST = [
  "Avery", "Blake", "Casey", "Dante", "Elliot", "Finley", "Gage", "Harper", "Isaiah", "Jalen", "Kai", "Logan",
  "Marlon", "Nico", "Oren", "Pierce", "Quinn", "Reese", "Soren", "Tobias", "Uriah", "Vance", "Wade", "Xavier",
  "Yusuf", "Zane", "Rowan", "Emery", "Desmond", "Calvin",
];
const LAST = [
  "Ashford", "Brightwater", "Calloway", "Dunmore", "Ellery", "Fairbanks", "Garrow", "Holloway", "Ingram", "Jessup",
  "Kettering", "Langley", "Merriweather", "Northcott", "Oakes", "Pemberton", "Quarles", "Radcliffe", "Stroud",
  "Thornbury", "Underhill", "Vickers", "Whitlock", "Yardley", "Zeller", "Ashby", "Blackwood", "Crowley", "Dorsey",
  "Easton", "Fenwick",
];

/** Small deterministic PRNG (mulberry32). */
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const round1 = (x) => Math.round(x * 10) / 10;
const round2 = (x) => Math.round(x * 100) / 100;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

// position -> [count, top mean, slope, sd fraction, boom top-N, bust cut]
const SPEC = {
  QB: [14, 24, 0.75, 0.33, 6, 18],
  RB: [28, 19, 0.42, 0.45, 12, 36],
  WR: [34, 18, 0.33, 0.47, 12, 48],
  TE: [14, 13, 0.5, 0.5, 6, 18],
  K: [10, 10, 0.22, 0.38, 6, 20],
  DST: [10, 10, 0.3, 0.55, 6, 20],
};

const DRIVER_TEXT = {
  opportunity: (pos, pts) =>
    pts >= 0
      ? `Gets a large share of his team's ${pos === "QB" ? "dropbacks" : pos === "RB" ? "carries and targets" : "targets"} (+${pts.toFixed(1)} pts vs an average ${pos}).`
      : `Sees fewer ${pos === "RB" ? "carries" : "targets"} than an average ${pos} (${pts.toFixed(1)} pts).`,
  efficiency: (pos, pts) =>
    pts >= 0
      ? `Gains more yards per touch than most ${pos}s (+${pts.toFixed(1)} pts).`
      : `Has been less efficient per touch than an average ${pos} (${pts.toFixed(1)} pts).`,
  matchup: (pos, pts) =>
    pts >= 0
      ? `Faces a defense that has allowed more points to ${pos}s (+${pts.toFixed(1)} pts).`
      : `Faces a defense that has been hard on ${pos}s (${pts.toFixed(1)} pts).`,
  environment: (_pos, pts) =>
    pts >= 0
      ? `His team is expected to run more plays and score more than usual (+${pts.toFixed(1)} pts).`
      : `A low expected game total limits the upside (${pts.toFixed(1)} pts).`,
  availability: (_pos, pts) =>
    `Listed as questionable; the simulation includes a chance he does not play (${pts.toFixed(1)} pts).`,
};

const KICKER_TEXT = {
  opportunity: (pts) => `His offense should reach field-goal range often (+${pts.toFixed(1)} pts).`,
  environment: (pts) =>
    pts >= 0 ? `Indoor game with a high expected total (+${pts.toFixed(1)} pts).` : `Wind and a low total cut into kicks (${pts.toFixed(1)} pts).`,
};
const DST_TEXT = {
  matchup: (pts) =>
    pts >= 0 ? `Opponent has allowed many sacks and turnovers (+${pts.toFixed(1)} pts).` : `Opponent rarely turns the ball over (${pts.toFixed(1)} pts).`,
  environment: (pts) =>
    pts >= 0 ? `A low expected total for the opponent (+${pts.toFixed(1)} pts).` : `The opponent is expected to score often (${pts.toFixed(1)} pts).`,
};

function buildPlayers() {
  const rand = rng(20261104);
  const players = [];
  let n = 0;
  const usedNames = new Set();
  for (const [pos, [count, top, slope, sdFrac, boomN, bustN]] of Object.entries(SPEC)) {
    for (let i = 0; i < count; i++) {
      n += 1;
      const team = TEAMS[(n * 7 + i * 3) % TEAMS.length];
      let opponent = TEAMS[(n * 11 + 5) % TEAMS.length];
      if (opponent === team) opponent = TEAMS[(n * 11 + 6) % TEAMS.length];
      let name;
      if (pos === "DST") {
        name = `${team} Defense`;
      } else {
        do {
          name = `${FIRST[Math.floor(rand() * FIRST.length)]} ${LAST[Math.floor(rand() * LAST.length)]}`;
        } while (usedNames.has(name));
        usedNames.add(name);
      }
      const noise = (rand() - 0.5) * 1.6;
      const mean = clamp(top - slope * i + noise, 2, 40);
      const sd = mean * sdFrac;
      const median = mean - sd * 0.08;
      const floor = Math.max(pos === "DST" ? -2 : 0, mean - 1.2816 * sd);
      const ceiling = mean + 1.2816 * sd * 1.08;
      const rankFrac = i / count;
      const boom = clamp(0.62 - rankFrac * 0.75 + (rand() - 0.5) * 0.08, 0.01, 0.75);
      const bust = clamp(0.03 + rankFrac * 0.7 * (count / bustN) * 0.6 + (rand() - 0.5) * 0.05, 0.01, 0.9);
      const questionable = pos !== "DST" && pos !== "K" && rand() < 0.08;
      const id = pos === "DST" ? team : `00-00${String(36000 + n * 17).padStart(5, "0")}`;

      const groups =
        pos === "K" ? ["opportunity", "environment"] : pos === "DST" ? ["matchup", "environment"] : ["opportunity", "efficiency", "matchup", "environment"];
      const drivers = groups
        .map((group, gi) => {
          const base = (mean - top * 0.55) * (gi === 0 ? 0.6 : 0.18);
          const pts = round1(base + (rand() - 0.5) * 2.4);
          const text =
            pos === "K"
              ? KICKER_TEXT[group](pts)
              : pos === "DST"
                ? DST_TEXT[group](pts)
                : DRIVER_TEXT[group](pos, pts);
          return { group, points: pts, text };
        })
        .filter((d) => Math.abs(d.points) >= 0.3);
      if (questionable) {
        const pts = round1(-0.6 - rand() * 1.5);
        drivers.push({ group: "availability", points: pts, text: DRIVER_TEXT.availability(pos, pts) });
      }
      drivers.sort((a, b) => Math.abs(b.points) - Math.abs(a.points));

      players.push({
        player_id: id,
        sleeper_id: pos === "DST" ? team : String(5000 + n * 13),
        name,
        position: pos,
        team,
        opponent,
        game_id: `2026_10_${[team, opponent].sort().join("_")}`,
        floor: round1(floor),
        median: round1(median),
        ceiling: round1(ceiling),
        mean: round1(mean),
        boom_prob: round2(boom),
        bust_prob: round2(bust),
        active_prob: questionable ? 0.78 : 0.99,
        // K and DST are low confidence (D-089); the fixture flags QB too (D-097).
        boom_bust_confidence: pos === "K" || pos === "DST" || pos === "QB" ? "low" : "normal",
        position_rank: i + 1,
        drivers,
        injury_status: questionable ? "Questionable" : null,
        bye_week: 5 + ((n * 3) % 10),
        _boomN: boomN,
      });
    }
  }
  return players;
}

export const PLAYERS = buildPlayers();

/** Public PlayerRow (contract shape) without internal fields. */
export function playerRow(p, roster = null) {
  const { injury_status: _i, bye_week: _b, _boomN, ...row } = p;
  return { ...row, roster };
}

export function findPlayer(id) {
  return PLAYERS.find((p) => p.player_id === id || p.sleeper_id === id) ?? null;
}

export const SLEEPER_USERS = {
  fixtureuser: {
    user: { user_id: "900000000000000001", username: "fixtureuser", display_name: "Fixture User" },
    leagues: [
      { league_id: "1100000000000000001", name: "Fixture Dynasty League", num_teams: 8, status: "in_season", preset_like: "ppr" },
      { league_id: "1100000000000000002", name: "Fixture Office League", num_teams: 10, status: "in_season", preset_like: "half" },
    ],
  },
  oneleague: {
    user: { user_id: "900000000000000002", username: "oneleague", display_name: "One League" },
    leagues: [{ league_id: "1100000000000000003", name: "Fixture Solo League", num_teams: 8, status: "in_season", preset_like: "ppr" }],
  },
};

export const ATTRIBUTION = {
  sources: [
    {
      id: "nflverse",
      name: "nflverse",
      license: "CC-BY-4.0",
      url: "https://github.com/nflverse/nflverse-data",
      in_use: true,
      text: "Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0. Modified: aggregated and modeled by this project.",
      used_for: "Play-by-play, player and team stats, schedules, rosters, injuries, depth charts, snap counts, Next Gen Stats.",
      notes: [
        "Next Gen Stats originate from NFL Next Gen Stats and are redistributed via nflverse.",
        "Snap counts come from Pro Football Reference and are redistributed via nflverse.",
      ],
    },
    {
      id: "ffopportunity",
      name: "ffopportunity",
      license: "not stated",
      url: "https://github.com/ffverse/ffopportunity",
      in_use: true,
      text: "Expected fantasy points from ffverse/ffopportunity via nflverse.",
      notes: [],
    },
    {
      id: "sleeper",
      name: "Sleeper",
      license: "Sleeper API terms (non-commercial use)",
      url: "https://docs.sleeper.com",
      in_use: true,
      text: "League and player data from Sleeper.",
      notes: [],
    },
    {
      id: "ftn",
      name: "FTN charting",
      license: "CC-BY-SA-4.0",
      url: "https://ftndata.com",
      in_use: false,
      text: "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0.",
      notes: ["FTN features are off by default (D-009)."],
    },
  ],
  usage: "Personal, non-commercial use (D-013).",
  independence: "An original project. Not affiliated with or endorsed by the NFL, nflverse, FTN, Sleeper or any fantasy football company.",
  not_displayed: "Expert ranking data used only in private model evaluation is never shown in this app.",
};

export const MODEL_CARD = {
  title: "Fixture projection chain (test data)",
  what_it_predicts:
    "Fixture model card used to test the page layout. Distributions of player stat lines are simulated, then each league’s scoring is applied.",
  method: [
    "Team plays and pass/run mix, then opportunity shares, efficiency and touchdowns, then fantasy points.",
    "Monte Carlo simulation with shared game draws.",
  ],
  status_summary:
    "Fixture: no model is approved. The first candidate failed its pre-registered holdout test on quarterback interval coverage.",
  definitions: {
    floor: "10th percentile of simulated points under your scoring.",
    median: "50th percentile.",
    ceiling: "90th percentile.",
    boom: "Chance of a top finish at the position that week.",
    bust: "Chance of finishing outside the startable range.",
  },
  limitations: [
    "Kicker and defense boom/bust probabilities are low confidence (D-089).",
    "Quarterback boom/bust probabilities were miscalibrated on the 2025 holdout (D-097).",
  ],
  documentation: ["docs/MODELING.md", "docs/DECISIONS.md (D-097 to D-099)"],
};
