"""A small synthetic league in the feature-dataset layout (tests and timing only).

:func:`synthetic_frames` returns ``team_week``, ``player_week`` and
``dst_week`` frames with the key columns, the features the structural
baselines and a few LightGBM inputs read, the same-week ``avail__`` inputs, and
internally consistent targets (team totals equal the sums of player stats).
"Lagged" features are each player's or team's latent rate plus noise, which is
enough to exercise every stage; they are not computed point in time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from ffml.scoring.statline import FG_BUCKETS

ROSTER = (
    # suffix, position, target, carry, dropback share, P(played)
    ("QB1", "QB", 0.0, 0.10, 0.97, 0.97),
    ("QB2", "QB", 0.0, 0.01, 0.30, 0.10),
    ("RB1", "RB", 0.10, 0.55, 0.0, 0.92),
    ("RB2", "RB", 0.05, 0.30, 0.0, 0.90),
    ("RB3", "RB", 0.02, 0.05, 0.0, 0.40),
    ("WR1", "WR", 0.26, 0.01, 0.0, 0.94),
    ("WR2", "WR", 0.20, 0.0, 0.0, 0.94),
    ("WR3", "WR", 0.13, 0.0, 0.0, 0.92),
    ("WR4", "WR", 0.05, 0.0, 0.0, 0.70),
    ("WR5", "WR", 0.02, 0.0, 0.0, 0.40),
    ("TE1", "TE", 0.14, 0.0, 0.0, 0.95),
    ("TE2", "TE", 0.04, 0.0, 0.0, 0.80),
    ("K1", "K", 0.0, 0.0, 0.0, 0.97),
)
STATUSES = (None, None, None, None, None, None, None, "Questionable", "Out", "Doubtful")


def _norm(x: np.ndarray) -> np.ndarray:
    s = x.sum()
    return x / s if s > 0 else x


def synthetic_frames(
    seasons: tuple[int, ...] = (2016, 2017),
    n_teams: int = 8,
    weeks: int = 18,
    seed: int = 0,
) -> dict[str, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 3) for t in teams}
    pace = {t: rng.normal(63, 2.5) for t in teams}
    passr = {t: float(np.clip(rng.normal(0.6, 0.04), 0.48, 0.7)) for t in teams}
    skill = {(t, s[0]): rng.normal(0, 1) for t in teams for s in ROSTER}
    team_rows, player_rows, dst_rows = [], [], []
    for season in seasons:
        for week in range(1, weeks + 1):
            order = rng.permutation(teams)
            kickoff = datetime(season, 9, 8, 17, tzinfo=UTC) + timedelta(weeks=week - 1)
            for g in range(0, n_teams, 2):
                home, away = order[g], order[g + 1]
                game_id = f"{season}_{week:02d}_{away}_{home}"
                spread_home = float(np.round((strength[away] - strength[home] - 1.5) * 2) / 2)
                total = float(np.round(rng.normal(45, 3) * 2) / 2)
                for team, opp, is_home in ((home, away, True), (away, home, False)):
                    spread = spread_home if is_home else -spread_home  # positive: underdog
                    implied = total / 2 - spread / 2
                    plays = int(np.clip(rng.normal(pace[team], 6), 45, 85))
                    pr = float(np.clip(rng.normal(passr[team] + 0.004 * spread, 0.05), 0.35, 0.85))
                    db = int(rng.binomial(plays, pr))
                    designed = plays - db
                    off_td = int(rng.poisson(max(implied, 5) / 9.4))
                    pass_td = int(rng.binomial(off_td, pr))
                    rush_td = off_td - pass_td
                    fg_att = int(rng.poisson(1.9))
                    two_att = int(rng.binomial(off_td, 0.09))
                    kneels = int(rng.poisson(0.6))
                    played = np.array([rng.random() < s[5] for s in ROSTER])
                    if not played[0] and not played[1]:
                        played[1] = True
                    tgt_s = _norm(
                        np.array([s[2] for s in ROSTER])
                        * played
                        * rng.gamma(30, 1 / 30, len(ROSTER))
                    )
                    car_s = _norm(
                        np.array([s[3] for s in ROSTER])
                        * played
                        * rng.gamma(10, 1 / 10, len(ROSTER))
                    )
                    db_s = _norm(np.array([s[4] for s in ROSTER]) * played)
                    dbs = (
                        rng.multinomial(db, db_s) if db_s.sum() > 0 else np.zeros(len(ROSTER), int)
                    )
                    sacks = rng.binomial(dbs, 0.065)
                    scr = rng.binomial(dbs - sacks, 0.04)
                    att = dbs - sacks - scr
                    team_att = int(att.sum())
                    targets = (
                        rng.multinomial(int(round(team_att * 0.965)), tgt_s)
                        if tgt_s.sum()
                        else np.zeros(len(ROSTER), int)
                    )
                    catch = np.array(
                        [
                            0.0 if s[1] in ("QB", "K") else 0.62 + 0.03 * skill[(team, s[0])]
                            for s in ROSTER
                        ]
                    ).clip(0.3, 0.9)
                    rec = rng.binomial(targets, catch)
                    ypr = np.array(
                        [10.5 if s[1] == "WR" else 9.5 if s[1] == "TE" else 7.5 for s in ROSTER]
                    )
                    rec_yds = np.round(
                        rng.gamma(np.maximum(rec, 1e-9) * 1.2, ypr / 1.2) * (rec > 0)
                    )
                    carries_d = (
                        rng.multinomial(designed, car_s)
                        if car_s.sum()
                        else np.zeros(len(ROSTER), int)
                    )
                    kneel_v = np.zeros(len(ROSTER), int)
                    qb_top = int(np.argmax(dbs))
                    kneel_v[qb_top] = kneels if dbs[qb_top] > 0 else 0
                    carries = carries_d + scr + kneel_v
                    rush_yds = (
                        np.round(
                            rng.gamma(np.maximum(carries_d + scr, 1e-9) * 0.5, 4.3 / 0.5)
                            * ((carries_d + scr) > 0)
                        )
                        - kneel_v
                    )
                    rec_td = (
                        rng.multinomial(pass_td, _norm(rec + 1e-9))
                        if rec.sum()
                        else np.zeros(len(ROSTER), int)
                    )
                    rec_td = np.minimum(rec_td, rec)
                    rush_td_p = (
                        rng.multinomial(rush_td, _norm(carries_d + scr + 1e-9))
                        if (carries_d + scr).sum()
                        else np.zeros(len(ROSTER), int)
                    )
                    cmp_team = int(rec.sum())
                    qb_cmp = np.zeros(len(ROSTER), int)
                    qb_cmp[qb_top] = cmp_team
                    qb_yds = np.zeros(len(ROSTER))
                    qb_yds[qb_top] = rec_yds.sum()
                    qb_td = np.zeros(len(ROSTER), int)
                    qb_td[qb_top] = int(rec_td.sum())
                    ints = np.minimum(rng.binomial(att, 0.023), att - np.minimum(qb_cmp, att))
                    fum = rng.binomial(dbs + carries + rec, 0.005)
                    team_rows.append(
                        {
                            "team": team,
                            "season": season,
                            "week": week,
                            "game_id": game_id,
                            "opponent": opp,
                            "game_type": "REG",
                            "home": is_home,
                            "kickoff_utc": kickoff,
                            "feature_version": "synthetic",
                            "ctx__week": float(week),
                            "ctx__spread": spread,
                            "ctx__total_line": total,
                            "ctx__implied_team_total": implied,
                            "ctx__home": float(is_home),
                            "team__plays__l5": pace[team] + rng.normal(0, 2),
                            "team__plays__ew": pace[team] + rng.normal(0, 2),
                            "team__plays__ps": pace[team] + rng.normal(0, 1),
                            "team__pass_rate__ew": passr[team] + rng.normal(0, 0.02),
                            "team__pass_rate__ps": passr[team],
                            "team__points__ew": 22 + strength[team] + rng.normal(0, 2),
                            "team__field_goal_att__ew": 1.9 + rng.normal(0, 0.4),
                            "def__points_allowed__ew": 22 - strength[opp] + rng.normal(0, 2),
                            "y_plays": float(plays),
                            "y_pass_rate": db / plays,
                            "y_pass_att": float(team_att),
                            "y_rush_att": float(carries.sum()),
                            "y_dropbacks": float(db),
                            "y_sacks": float(sacks.sum()),
                            "y_pass_td": float(rec_td.sum()),
                            "y_rush_td": float(rush_td_p.sum()),
                            "y_offensive_td": float(rec_td.sum() + rush_td_p.sum()),
                            "y_points": float(7 * off_td + 3 * fg_att),
                            "y_field_goal_att": float(fg_att),
                            "y_two_pt_att": float(two_att),
                        }
                    )
                    for i, (suffix, pos, tg, ca, dbsh, p_play) in enumerate(ROSTER):
                        pid = f"{team}-{suffix}"
                        k = pos == "K"
                        fg_made_b = {b: 0 for b in FG_BUCKETS}
                        fg_miss_b = {b: 0 for b in FG_BUCKETS}
                        if k and played[i]:
                            for _ in range(fg_att):
                                b = FG_BUCKETS[
                                    rng.choice(6, p=[0.02, 0.24, 0.25, 0.26, 0.21, 0.02])
                                ]
                                if (
                                    rng.random()
                                    < {
                                        "0_19": 0.99,
                                        "20_29": 0.97,
                                        "30_39": 0.92,
                                        "40_49": 0.84,
                                        "50_59": 0.72,
                                        "60p": 0.45,
                                    }[b]
                                ):
                                    fg_made_b[b] += 1
                                else:
                                    fg_miss_b[b] += 1
                        status = (
                            STATUSES[rng.integers(len(STATUSES))] if rng.random() < 0.2 else None
                        )
                        y_played = float(played[i])
                        tm_t = max(targets.sum(), 1)
                        row = {
                            "player_id": pid,
                            "season": season,
                            "week": week,
                            "game_id": game_id,
                            "team": team,
                            "opponent": opp,
                            "position": pos,
                            "game_type": "REG",
                            "home": is_home,
                            "kickoff_utc": kickoff,
                            "feature_version": "synthetic",
                            "ctx__week": float(week),
                            "ctx__spread": spread,
                            "ctx__total_line": total,
                            "ctx__implied_team_total": implied,
                            "ctx__home": float(is_home),
                            "team__pass_rate__ew": passr[team],
                            "team__plays__ew": pace[team],
                            "opp__target_share__ew": tg + rng.normal(0, 0.02) if not k else None,
                            "opp__rush_share__ew": ca + rng.normal(0, 0.02) if not k else None,
                            "opp__dropback_share__ew": dbsh + rng.normal(0, 0.02)
                            if pos == "QB"
                            else None,
                            "opp__gl_carry_share__ew_shrunk": ca,
                            "opp__rz_target_share__ew_shrunk": tg,
                            "opp__pass_td_share__ew_shrunk": tg,
                            "opp__rush_td_share__ew_shrunk": ca,
                            "opp__snap_share__ew": float(
                                np.clip(p_play + rng.normal(0, 0.05), 0, 1)
                            ),
                            "opp__snap_share__l1": float(
                                np.clip(p_play + rng.normal(0, 0.1), 0, 1)
                            ),
                            "opp__scramble_rate__ew": 0.04 if pos == "QB" else None,
                            "eff__catch_rate__ew_shrunk": float(catch[i])
                            if pos in ("RB", "WR", "TE")
                            else None,
                            "eff__yards_per_reception__ew_shrunk": float(ypr[i])
                            + rng.normal(0, 0.5),
                            "eff__yards_per_carry__ew_shrunk": 4.3 + rng.normal(0, 0.3),
                            "eff__completion_rate__ew_shrunk": 0.64 if pos == "QB" else None,
                            "eff__int_rate__ew_shrunk": 0.023 if pos == "QB" else None,
                            "eff__sack_rate__ew_shrunk": 0.065 if pos == "QB" else None,
                            "eff__fg_pct__car_shrunk": 0.85 + rng.normal(0, 0.02) if k else None,
                            "eff__pat_pct__car_shrunk": 0.94 if k else None,
                            "def__ypc_allowed__ew": 4.3 + rng.normal(0, 0.3),
                            "def__int_rate__std_shrunk": 0.023 + rng.normal(0, 0.003),
                            "depth__rank": float(int(suffix[-1])),
                            "depth__starter_flag": float(suffix.endswith("1")),
                            "attr__games_missed__l5": float(rng.binomial(5, 1 - p_play)),
                            "attr__weeks_since_last_game": float(1 + rng.binomial(3, 1 - p_play)),
                            "attr__inj_prev_status": float(rng.choice([0, 0, 0, 1, 3])),
                            "attr__games__std_n": float(min(week - 1, 17)),
                            "avail__report_status": status,
                            "avail__practice_status": None,
                            "avail__roster_status": "ACT",
                            "avail__report_status_ord": {
                                None: 0.0,
                                "Questionable": 1.0,
                                "Doubtful": 2.0,
                                "Out": 3.0,
                            }[status],
                            "avail__depth_chart_present": 1.0,
                            "avail__teammates_out_same_pos": 0.0,
                            "y_played": y_played,
                            "y_targets": float(targets[i]),
                            "y_receptions": float(rec[i]),
                            "y_rec_yds": float(rec_yds[i]),
                            "y_rec_td": float(rec_td[i]),
                            "y_carries": float(carries[i]),
                            "y_rush_yds": float(rush_yds[i]),
                            "y_rush_td": float(rush_td_p[i]),
                            "y_dropbacks": float(dbs[i]),
                            "y_pass_att": float(att[i]),
                            "y_completions": float(qb_cmp[i]),
                            "y_pass_yds": float(qb_yds[i]),
                            "y_pass_td": float(qb_td[i]),
                            "y_interceptions": float(ints[i]),
                            "y_sacks": float(sacks[i]),
                            "y_scrambles": float(scr[i]),
                            "y_fumbles_lost": float(fum[i]),
                            "y_target_share": float(targets[i] / tm_t),
                            "y_carry_share": float(carries[i] / max(carries.sum(), 1)),
                            "y_dropback_share": float(dbs[i] / max(db, 1)),
                            "y_gl_carries": float(rush_td_p[i]),
                            "y_rz_targets": float(rec_td[i]),
                            "y_gl_carry_share": float(rush_td_p[i] / rush_td_p.sum())
                            if rush_td_p.sum()
                            else None,
                            "y_rz_target_share": float(rec_td[i] / rec_td.sum())
                            if rec_td.sum()
                            else None,
                            "y_pass_td_share": float(rec_td[i] / rec_td.sum())
                            if rec_td.sum()
                            else None,
                            "y_rush_td_share": float(rush_td_p[i] / rush_td_p.sum())
                            if rush_td_p.sum()
                            else None,
                            "y_catch_rate": float(rec[i] / targets[i]) if targets[i] else None,
                            "y_yards_per_carry": float(rush_yds[i] / carries[i])
                            if carries[i]
                            else None,
                            "y_completion_rate": min(float(qb_cmp[i] / att[i]), 1.0)
                            if att[i]
                            else None,
                            "y_int_per_attempt": float(ints[i] / att[i]) if att[i] else None,
                            "y_fg_att": float(sum(fg_made_b.values()) + sum(fg_miss_b.values())),
                            "y_fg_made": float(sum(fg_made_b.values())),
                            "y_pat_att": float(off_td - two_att) if k and played[i] else 0.0,
                            "y_pat_made": float(off_td - two_att) if k and played[i] else 0.0,
                        }
                        for b in FG_BUCKETS:
                            row[f"y_fg_made_{b}"] = float(fg_made_b[b])
                            row[f"y_fg_missed_{b}"] = float(fg_miss_b[b])
                        if not played[i]:
                            for c in list(row):
                                if c.startswith("y_") and c != "y_played":
                                    row[c] = (
                                        0.0
                                        if not c.endswith(
                                            ("share", "rate", "per_carry", "per_attempt")
                                        )
                                        else None
                                    )
                        player_rows.append(row)
                    dst_rows.append(
                        {
                            "team": opp,
                            "season": season,
                            "week": week,
                            "game_id": game_id,
                            "opponent": team,
                            "game_type": "REG",
                            "home": not is_home,
                            "kickoff_utc": kickoff,
                            "feature_version": "synthetic",
                            "ctx__spread": -spread,
                            "ctx__total_line": total,
                            "def__dst_sack_rate__ew": 0.065 + rng.normal(0, 0.01),
                            "team__sack_rate_allowed__ew": 0.065,
                            "team__dropbacks__ew": pace[team] * passr[team],
                            "def__dst_int_rate__ew": 0.023,
                            "team__int_rate__ew": 0.023,
                            "team__pass_att__ew": pace[team] * passr[team] * 0.9,
                            "def__dst_fumble_recoveries__ew": 0.5,
                            "def__plays_faced__ew": pace[team],
                            "team__fumble_rate__ew": 0.008,
                            "team__plays__ew": pace[team],
                            "def__dst_forced_fumbles__ew": 0.8 + rng.normal(0, 0.2),
                            "def__dst_def_td__std_shrunk": 0.11,
                            "def__dst_return_td__std_shrunk": 0.035,
                            "def__dst_safeties__std_shrunk": 0.03,
                            "def__dst_blocked_kicks__std_shrunk": 0.07,
                            "y_sacks": float(sacks.sum()),
                            "y_interceptions": float(ints.sum()),
                            "y_fumble_recoveries": float(fum.sum()),
                            "y_forced_fumbles": float(fum.sum() + rng.poisson(0.35)),
                            "y_def_td": float(rng.poisson(0.1)),
                            "y_return_td": float(rng.poisson(0.03)),
                            "y_safeties": float(rng.poisson(0.03)),
                            "y_blocked_kicks": float(rng.poisson(0.07)),
                            "y_points_allowed": float(7 * off_td + 3 * fg_att),
                        }
                    )
    return {
        "team_week": pl.DataFrame(team_rows, infer_schema_length=None),
        "player_week": pl.DataFrame(player_rows, infer_schema_length=None),
        "dst_week": pl.DataFrame(dst_rows, infer_schema_length=None),
    }


__all__ = ["ROSTER", "synthetic_frames"]
