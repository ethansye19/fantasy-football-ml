"""Tests for the core processed tables: dims, fact_team_game, fact_dst_game, fact_player_game.

The unit tests build a tiny raw lake (one fabricated game of 22 plays) in a
temporary ``SnapshotStore`` and run the real builders through ``run_process``.
The integration test at the bottom runs against the real raw lake for 2025 and
is skipped when the lake is not present.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from ffml.config import PROJECT_ROOT, get_settings
from ffml.data.process import dims as dims_mod
from ffml.data.process import run_process
from ffml.data.process.base import ProcessContext
from ffml.data.process.dims import build_dim_game, normalized_name
from ffml.data.process.player_game import PBP_PLAYER_COLUMNS, STATS_COLUMNS, _remap_post_weeks
from ffml.data.process.team_game import PBP_COLUMNS, prepare_pbp
from ffml.data.sleeper import SleeperPlayersCache, players_frame
from ffml.data.store import SnapshotStore

SEASON = 2025
GAME = "2025_01_KC_BUF"
NOW = datetime(2025, 9, 8, 17, 0, tzinfo=UTC)  # Monday 13:00 ET after the opening week
TABLES = [
    "dim_team",
    "dim_player",
    "dim_game",
    "fact_team_game",
    "fact_dst_game",
    "fact_player_game",
]
CURRENT_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND",
    "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF",
    "TB", "TEN", "WAS",
]  # fmt: skip

QB1, RB1, WR1, TE1, K01, FB1, WR3, LB1 = (
    "00-QB1",
    "00-RB1",
    "00-WR1",
    "00-TE1",
    "00-K01",
    "00-FB1",
    "00-WR3",
    "00-LB1",
)
QB2, WR2, DUP2 = "00-QB2", "00-WR2", "00-DUP2"


# --------------------------------------------------------------------------- #
# Synthetic raw datasets
# --------------------------------------------------------------------------- #


def _players() -> pl.DataFrame:
    rows = [
        (
            QB1,
            "Josh Allen",
            "Josh",
            "Allen",
            "QB",
            "1996-05-21",
            2018,
            2018,
            1,
            7,
            "3918298",
            "AlleJo02",
            "BUF",
        ),
        (
            RB1,
            "James Cook",
            "James",
            "Cook",
            "RB",
            "1999-09-25",
            2022,
            2022,
            2,
            63,
            "4379399",
            "CookJa01",
            "BUF",
        ),
        (
            WR1,
            "Khalil Shakir",
            "Khalil",
            "Shakir",
            "WR",
            "2000-02-03",
            2022,
            2022,
            5,
            148,
            None,
            "ShakKh00",
            "BUF",
        ),
        (
            TE1,
            "Dalton Kincaid Jr.",
            "Dalton",
            "Kincaid",
            "TE",
            "1999-10-18",
            2023,
            2023,
            1,
            25,
            None,
            "KincDa00",
            "BUF",
        ),
        (
            K01,
            "Tyler Bass",
            "Tyler",
            "Bass",
            "K",
            "1997-02-14",
            2020,
            2020,
            6,
            188,
            None,
            "BassTy00",
            "BUF",
        ),
        (
            FB1,
            "Reggie Gilliam",
            "Reggie",
            "Gilliam",
            "FB",
            "1997-09-19",
            2020,
            None,
            None,
            None,
            None,
            "GillRe00",
            "BUF",
        ),
        (
            WR3,
            "Curtis Samuel",
            "Curtis",
            "Samuel",
            "WR",
            "1996-08-11",
            2017,
            2017,
            2,
            40,
            None,
            "SamuCu00",
            "BUF",
        ),
        (
            LB1,
            "Matt Milano",
            "Matt",
            "Milano",
            "LB",
            "1994-07-28",
            2017,
            2017,
            5,
            163,
            None,
            "MilaMa00",
            "BUF",
        ),
        (
            QB2,
            "Patrick Mahomes",
            "Patrick",
            "Mahomes",
            "QB",
            "1995-09-17",
            2017,
            2017,
            1,
            10,
            None,
            "MahoPa00",
            "KC",
        ),
        (
            WR2,
            "Rashee Rice",
            "Rashee",
            "Rice",
            "WR",
            "2000-04-22",
            2023,
            2023,
            2,
            55,
            None,
            "RiceRa00",
            "KC",
        ),
        (
            DUP2,
            "Rashee Rice",
            "Rashee",
            "Rice",
            "WR",
            "1990-01-01",
            2012,
            None,
            None,
            None,
            None,
            None,
            "KC",
        ),
    ]
    df = pl.DataFrame(
        rows,
        schema={
            "gsis_id": pl.String,
            "display_name": pl.String,
            "first_name": pl.String,
            "last_name": pl.String,
            "position": pl.String,
            "birth_date": pl.String,
            "rookie_season": pl.Int32,
            "draft_year": pl.Int32,
            "draft_round": pl.Int32,
            "draft_pick": pl.Int32,
            "espn_id": pl.String,
            "pfr_id": pl.String,
            "latest_team": pl.String,
        },  # fmt: skip
        orient="row",
    )
    return df.with_columns(
        pl.lit(None, dtype=pl.String).alias("nfl_id"),
        pl.lit("https://img/x.png").alias("headshot"),
        pl.lit("ACT").alias("status"),
        pl.lit(72, dtype=pl.Int32).alias("height"),
        pl.lit(210, dtype=pl.Int32).alias("weight"),
        pl.lit("State").alias("college_name"),
        pl.lit(2025, dtype=pl.Int32).alias("last_season"),
    )


def _ff_playerids() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "gsis_id": [QB1, WR2, DUP2, None],
            "sleeper_id": [101, 999, 999, 5],
            "espn_id": [3918298, 4430000, None, 1],
            "yahoo_id": ["31833", "40100", None, None],
            "fantasypros_id": [17298, 24000, None, None],
            "pfr_id": ["AlleJo02", "RiceRa00", None, None],
            "mfl_id": [13593, 16000, 1, 2],
            "name": ["Josh Allen", "Rashee Rice", "Rashee Rice", "Nobody"],
            "position": ["QB", "WR", "WR", "WR"],
            "team": ["BUF", "KC", "FA", "FA"],
            "db_season": [2025, 2025, 2024, 2025],
        },
        schema_overrides={
            "sleeper_id": pl.Int64,
            "espn_id": pl.Int64,
            "fantasypros_id": pl.Int64,
            "mfl_id": pl.Int64,
            "db_season": pl.Int64,
        },
    )


def _rosters() -> pl.DataFrame:
    def row(pid, team, pos, status="ACT", week=1, sleeper=None):
        return (SEASON, week, team, pos, status, pid, sleeper, None, None, None, "REG")

    rows = [
        row(QB1, "BUF", "QB"),
        row(RB1, "BUF", "RB", sleeper="102"),
        row(WR1, "BUF", "WR"),
        row(TE1, "BUF", "TE"),
        row(K01, "BUF", "K"),
        row(FB1, "BUF", "RB"),
        row(WR3, "BUF", "WR"),
        row(WR3, "BUF", "WR", week=2),  # BUF has no week-2 game: must not produce a row
        row(LB1, "BUF", "LB"),
        row("00-IR1", "BUF", "WR", status="RES"),  # not ACT/INA: ignored
        row(QB2, "KC", "QB"),
        row(WR2, "KC", "WR"),
    ]
    return pl.DataFrame(
        rows,
        schema={
            "season": pl.Int32,
            "week": pl.Int32,
            "team": pl.String,
            "position": pl.String,
            "status": pl.String,
            "gsis_id": pl.String,
            "sleeper_id": pl.String,
            "espn_id": pl.String,
            "yahoo_id": pl.String,
            "pfr_id": pl.String,
            "game_type": pl.String,
        },  # fmt: skip
        orient="row",
    )


def _sleeper_raw() -> dict[str, dict]:
    def p(pid, first, last, pos, team, gsis=None, fps=None):
        return {
            "player_id": pid, "first_name": first, "last_name": last, "full_name": f"{first} {last}",
            "position": pos, "fantasy_positions": fps or [pos], "team": team, "status": "Active",
            "active": True, "gsis_id": gsis, "search_rank": 100,
        }  # fmt: skip

    return {
        "101": p("101", "Josh", "Allen", "QB", "BUF", gsis="00-QB1"),
        "102": p("102", "James", "Cook", "RB", "BUF"),
        "103": p("103", "Khalil", "Shakir", "WR", "BUF", gsis=" 00-WR1"),  # padded gsis
        "104": p("104", "Dalton", "Kincaid", "TE", "BUF"),  # name match target
        "1001": p("1001", "Tyler", "Bass", "K", "BUF"),
        "105": p("105", "Reggie", "Gilliam", "FB", "BUF", gsis="00-FB1", fps=["RB"]),
        "106": p("106", "Curtis", "Samuel", "WR", "BUF"),  # no opportunities: no name match
        "999": p("999", "Rashee", "Rice", "WR", "KC", gsis="00-WR2"),
        "201": p("201", "Patrick", "Mahomes", "QB", "KC", gsis="00-QB2"),
        "BUF": {
            "player_id": "BUF",
            "first_name": "Buffalo",
            "last_name": "Bills",
            "position": "DEF",
            "team": "BUF",
        },
    }


def _teams() -> pl.DataFrame:
    codes = CURRENT_TEAMS + ["OAK"]
    return pl.DataFrame(
        {
            "team_abbr": codes,
            "team_name": [f"{c} Football Club" for c in codes],
            "team_id": [str(i) for i in range(len(codes))],
            "team_nick": codes,
            "team_conf": ["AFC"] * len(codes),
            "team_division": ["AFC East"] * len(codes),
            "team_color": ["#000000"] * len(codes),
            "team_color2": ["#ffffff"] * len(codes),
            "team_color3": [None] * len(codes),
            "team_color4": [None] * len(codes),
            "team_logo_wikipedia": ["w"] * len(codes),
            "team_logo_espn": ["e"] * len(codes),
            "team_wordmark": ["m"] * len(codes),
            "team_conference_logo": ["c"] * len(codes),
            "team_league_logo": ["l"] * len(codes),
            "team_logo_squared": ["s"] * len(codes),
        }
    )


def _schedules() -> pl.DataFrame:
    rows = [
        # final: BUF 24, KC 21 (result = home - away)
        (GAME, SEASON, "REG", 1, "2025-09-07", "20:20", "KC", "BUF", 21, 24, 3, 45),
        # kicked off 60 minutes before NOW, no result: in progress
        (
            "2025_01_SD_STL",
            SEASON,
            "REG",
            1,
            "2025-09-08",
            "12:00",
            "SD",
            "STL",
            None,
            None,
            None,
            None,
        ),
        # kicked off 28 hours before NOW, no result: scheduled (outside the window)
        (
            "2025_01_OAK_DEN",
            SEASON,
            "REG",
            1,
            "2025-09-07",
            "13:00",
            "OAK",
            "DEN",
            None,
            None,
            None,
            None,
        ),
        # next week
        (
            "2025_02_KC_PHI",
            SEASON,
            "REG",
            2,
            "2025-09-14",
            "13:00",
            "KC",
            "PHI",
            None,
            None,
            None,
            None,
        ),
    ]
    df = pl.DataFrame(
        rows,
        schema={
            "game_id": pl.String,
            "season": pl.Int32,
            "game_type": pl.String,
            "week": pl.Int32,
            "gameday": pl.String,
            "gametime": pl.String,
            "away_team": pl.String,
            "home_team": pl.String,
            "away_score": pl.Int32,
            "home_score": pl.Int32,
            "result": pl.Int32,
            "total": pl.Int32,
        },  # fmt: skip
        orient="row",
    )
    return df.with_columns(
        pl.lit(-2.5).alias("spread_line"),
        pl.lit(47.5).alias("total_line"),
        pl.lit(120, dtype=pl.Int32).alias("away_moneyline"),
        pl.lit(-140, dtype=pl.Int32).alias("home_moneyline"),
        pl.lit(7, dtype=pl.Int32).alias("away_rest"),
        pl.lit(7, dtype=pl.Int32).alias("home_rest"),
        pl.lit(0, dtype=pl.Int32).alias("div_game"),
        pl.lit("outdoors").alias("roof"),
        pl.lit("grass").alias("surface"),
        pl.lit(68, dtype=pl.Int32).alias("temp"),
        pl.lit(5, dtype=pl.Int32).alias("wind"),
        pl.lit("BUF00").alias("stadium_id"),
        pl.lit("Highmark Stadium").alias("stadium"),
        pl.lit("Patrick Mahomes").alias("away_qb_name"),
        pl.lit("Josh Allen").alias("home_qb_name"),
    )


_PBP_STRINGS = {
    "game_id", "posteam", "defteam", "play_type", "two_point_conv_result", "td_team", "field_goal_result",
    "extra_point_result", "fumbled_1_team", "fumble_recovery_1_team", "receiver_player_id", "rusher_player_id",
    "passer_player_id", "passer_id", "forced_fumble_player_1_team", "forced_fumble_player_2_team",
}  # fmt: skip
_PBP_NULL_DEFAULT = {"pass_oe", "air_yards", "passing_yards", "down"}


def _pbp() -> pl.DataFrame:
    """A fabricated KC at BUF game, 22 plays, BUF on offense first."""
    plays: list[dict] = []

    def play(**kw) -> None:
        base: dict = {
            c: None if c in _PBP_STRINGS or c in _PBP_NULL_DEFAULT else 0.0 for c in PBP_COLUMNS
        }
        base.update(
            game_id=GAME, season=SEASON, week=1, posteam="BUF", defteam="KC", qtr=1.0, down=1.0, wp=0.5,
            fixed_drive=1.0, yardline_100=50.0, play_id=float(len(plays) + 1), epa=0.0, success=0.0,
        )  # fmt: skip
        base.update(kw)
        if (
            base.get("half_seconds_remaining") == 0.0
            and base.get("game_seconds_remaining") is not None
        ):
            gsr = base["game_seconds_remaining"]
            base["half_seconds_remaining"] = gsr - 1800.0 if gsr > 1800 else gsr
        plays.append(base)

    def pas(receiver, passer, air, yards, complete=True, **kw):
        d = dict(
            play_type="pass", **{"pass": 1.0}, pass_attempt=1.0, qb_dropback=1.0, receiver_player_id=receiver,
            passer_player_id=passer, passer_id=passer, air_yards=air, yards_gained=yards,
            complete_pass=1.0 if complete else 0.0, passing_yards=yards if complete else None,
        )  # fmt: skip
        d.update(kw)
        play(**d)

    def run(rusher, yards, **kw):
        d = dict(
            play_type="run", rush=1.0, rush_attempt=1.0, rusher_player_id=rusher, yards_gained=yards
        )
        d.update(kw)
        play(**d)

    # Drive 1 (BUF): TD drive with a red-zone and end-zone target.
    pas(
        WR1, QB1, 10.0, 12.0, yardline_100=75.0, game_seconds_remaining=3600.0, epa=1.0, success=1.0
    )
    run(RB1, 5.0, yardline_100=63.0, game_seconds_remaining=3570.0, epa=0.2, success=1.0)
    pas(
        TE1,
        QB1,
        8.0,
        0.0,
        complete=False,
        yardline_100=58.0,
        down=2.0,
        game_seconds_remaining=3540.0,
        epa=-0.5,
    )
    pas(
        RB1,
        QB1,
        -2.0,
        40.0,
        yardline_100=58.0,
        down=3.0,
        game_seconds_remaining=3500.0,
        epa=2.0,
        success=1.0,
    )
    run(RB1, 10.0, yardline_100=18.0, game_seconds_remaining=3470.0, epa=0.5, success=1.0)
    pas(
        WR1,
        QB1,
        8.0,
        8.0,
        yardline_100=8.0,
        game_seconds_remaining=3440.0,
        epa=3.0,
        success=1.0,
        pass_touchdown=1.0,
        touchdown=1.0,
        td_team="BUF",
    )
    play(
        play_type="extra_point",
        extra_point_result="good",
        yardline_100=15.0,
        game_seconds_remaining=3440.0,
    )
    # Drive 2 (KC): kickoff, completion, sack, interception.
    play(
        play_type="kickoff",
        posteam="KC",
        defteam="BUF",
        fixed_drive=2.0,
        yardline_100=35.0,
        game_seconds_remaining=3440.0,
    )
    pas(
        WR2,
        QB2,
        5.0,
        5.0,
        posteam="KC",
        defteam="BUF",
        fixed_drive=2.0,
        yardline_100=75.0,
        wp=0.45,
        game_seconds_remaining=3300.0,
        epa=0.1,
        success=1.0,
    )
    play(
        play_type="pass",
        **{"pass": 1.0},
        sack=1.0,
        qb_dropback=1.0,
        passer_player_id=QB2,
        passer_id=QB2,
        yards_gained=-7.0,
        posteam="KC",
        defteam="BUF",
        fixed_drive=2.0,
        yardline_100=70.0,
        down=2.0,
        wp=0.45,
        game_seconds_remaining=3270.0,
        epa=-1.5,
    )
    pas(
        WR2,
        QB2,
        15.0,
        0.0,
        complete=False,
        interception=1.0,
        posteam="KC",
        defteam="BUF",
        fixed_drive=2.0,
        yardline_100=77.0,
        down=3.0,
        wp=0.45,
        game_seconds_remaining=3240.0,
        epa=-3.0,
    )
    # Drive 3 (BUF): scramble, then two goal-line carries ending in a rushing TD (non-neutral wp).
    play(
        play_type="run",
        **{"pass": 1.0},
        qb_dropback=1.0,
        qb_scramble=1.0,
        rush_attempt=1.0,
        rusher_player_id=QB1,
        passer_id=QB1,
        yards_gained=6.0,
        fixed_drive=3.0,
        yardline_100=40.0,
        qtr=2.0,
        wp=0.6,
        game_seconds_remaining=3200.0,
        epa=0.3,
        success=1.0,
    )
    run(
        RB1,
        2.0,
        fixed_drive=3.0,
        yardline_100=4.0,
        qtr=2.0,
        wp=0.9,
        game_seconds_remaining=3170.0,
        epa=0.4,
        success=1.0,
    )
    run(
        RB1,
        2.0,
        fixed_drive=3.0,
        yardline_100=2.0,
        qtr=2.0,
        wp=0.9,
        game_seconds_remaining=3140.0,
        epa=2.5,
        success=1.0,
        rush_touchdown=1.0,
        touchdown=1.0,
        td_team="BUF",
    )
    # Drive 4 (KC): red-zone trip that ends in a field goal, then a kneel.
    play(
        play_type="field_goal",
        field_goal_attempt=1.0,
        field_goal_result="made",
        posteam="KC",
        defteam="BUF",
        fixed_drive=4.0,
        yardline_100=13.0,
        qtr=2.0,
        game_seconds_remaining=2000.0,
    )
    play(
        play_type="qb_kneel",
        rush_attempt=1.0,
        rusher_player_id=QB2,
        yards_gained=-1.0,
        posteam="KC",
        defteam="BUF",
        fixed_drive=4.0,
        yardline_100=60.0,
        qtr=2.0,
        game_seconds_remaining=1900.0,
    )
    # Drive 5 (BUF): nullified play, two-minute target, fumble lost.
    play(
        play_type="no_play",
        **{"pass": 1.0},
        fixed_drive=5.0,
        qtr=2.0,
        game_seconds_remaining=1880.0,
    )
    pas(
        TE1,
        QB1,
        5.0,
        5.0,
        fixed_drive=5.0,
        yardline_100=50.0,
        down=2.0,
        qtr=2.0,
        game_seconds_remaining=1860.0,
        half_seconds_remaining=60.0,
        epa=0.2,
        success=1.0,
    )
    run(
        RB1,
        3.0,
        fixed_drive=5.0,
        yardline_100=45.0,
        qtr=2.0,
        game_seconds_remaining=1830.0,
        half_seconds_remaining=30.0,
        epa=-2.0,
        fumble=1.0,
        fumble_lost=1.0,
        fumbled_1_team="BUF",
        fumble_recovery_1_team="KC",
    )
    # Special teams: BUF punt returned for a KC touchdown; KC punt blocked by BUF.
    play(
        play_type="punt",
        posteam="BUF",
        defteam="KC",
        fixed_drive=6.0,
        qtr=3.0,
        game_seconds_remaining=1500.0,
        return_touchdown=1.0,
        touchdown=1.0,
        td_team="KC",
    )
    play(
        play_type="punt",
        posteam="KC",
        defteam="BUF",
        fixed_drive=7.0,
        qtr=3.0,
        game_seconds_remaining=1200.0,
        punt_blocked=1.0,
    )
    # KC two-point pass, failed: counts as a play but not as a pass attempt or target.
    play(
        play_type="pass",
        **{"pass": 1.0},
        pass_attempt=1.0,
        qb_dropback=1.0,
        two_point_attempt=1.0,
        two_point_conv_result="failure",
        receiver_player_id=WR2,
        passer_player_id=QB2,
        passer_id=QB2,
        posteam="KC",
        defteam="BUF",
        fixed_drive=8.0,
        yardline_100=2.0,
        down=None,
        qtr=4.0,
        wp=0.3,
        game_seconds_remaining=600.0,
        epa=-0.9,
    )

    return pl.DataFrame(plays, orient="row").select(PBP_COLUMNS)


def _player_stats() -> pl.DataFrame:
    def row(pid, pos, team, opp, **kw):
        base = {c: 0 for c in STATS_COLUMNS}
        base.update(
            player_id=pid, position=pos, season=SEASON, week=1, season_type="REG", game_id=GAME, team=team,
            opponent_team=opp, passing_epa=None, passing_cpoe=None, rushing_epa=None, receiving_epa=None,
            target_share=None, air_yards_share=None, wopr=None, fantasy_points=0.0, fantasy_points_ppr=0.0,
        )  # fmt: skip
        base.update(kw)
        return base

    rows = [
        row(
            QB1,
            "QB",
            "BUF",
            "KC",
            attempts=5,
            completions=4,
            passing_yards=65,
            passing_tds=1,
            carries=1,
            rushing_yards=6,
            passing_epa=5.5,
            fantasy_points=8.2,
            fantasy_points_ppr=8.2,
        ),
        row(
            RB1,
            "RB",
            "BUF",
            "KC",
            carries=5,
            rushing_yards=22,
            rushing_tds=1,
            targets=1,
            receptions=1,
            receiving_yards=40,
            receiving_air_yards=-2,
            rushing_fumbles_lost=1,
            fantasy_points=10.2,
            fantasy_points_ppr=11.2,
        ),
        row(
            WR1,
            "WR",
            "BUF",
            "KC",
            targets=2,
            receptions=2,
            receiving_yards=20,
            receiving_tds=1,
            receiving_air_yards=18,
            target_share=0.4,
        ),
        row(
            TE1,
            "TE",
            "BUF",
            "KC",
            targets=2,
            receptions=1,
            receiving_yards=5,
            receiving_air_yards=13,
        ),
        row(
            K01,
            "K",
            "BUF",
            "KC",
            pat_att=1,
            pat_made=1,
            fg_att=2,
            fg_made=1,
            fg_made_30_39=1,
            fg_missed=1,
        ),
        row(FB1, "FB", "BUF", "KC", carries=1, rushing_yards=2),
        row(LB1, "LB", "BUF", "KC"),
        row(
            QB2,
            "QB",
            "KC",
            "BUF",
            attempts=2,
            completions=1,
            passing_yards=5,
            passing_interceptions=1,
            sacks_suffered=1,
            sack_yards_lost=7,
        ),
        row(
            WR2,
            "WR",
            "KC",
            "BUF",
            targets=2,
            receptions=1,
            receiving_yards=5,
            receiving_air_yards=20,
        ),
        # a second (traded) row for WR2 in the same week with fewer touches: must be dropped
        row(WR2, "WR", "BUF", "KC", targets=1),
    ]
    return pl.DataFrame(rows, orient="row").select(STATS_COLUMNS)


def _snap_counts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [GAME] * 6,
            "pfr_game_id": ["202509070buf"] * 6,
            "season": [SEASON] * 6,
            "game_type": ["REG"] * 6,
            "week": [1] * 6,
            "player": [
                "Josh Allen",
                "James Cook",
                "Khalil Shakir",
                "Dalton Kincaid",
                "Curtis Samuel",
                "Reggie Gilliam",
            ],
            "pfr_player_id": [
                "AlleJo02",
                "CookJa01",
                "ShakKh00",
                "KincDa00",
                "SamuCu00",
                "GillRe00",
            ],
            "position": ["QB", "RB", "WR", "TE", "WR", "FB"],
            "team": ["BUF"] * 6,
            "opponent": ["KC"] * 6,
            "offense_snaps": [60.0, 40.0, 50.0, 45.0, 0.0, 10.0],
            "offense_pct": [1.0, 0.67, 0.83, 0.75, 0.0, 0.17],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    )


def _ff_opportunity() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [SEASON, SEASON],
            "posteam": ["BUF", "BUF"],
            "week": [1, 1],
            "game_id": [GAME, GAME],
            "player_id": [RB1, None],
            "full_name": ["James Cook", None],
            "position": ["RB", None],
            "receptions_exp": [1.2, 0.5],
            "rec_yards_gained_exp": [9.5, 0.0],
            "rec_touchdown_exp": [0.1, 0.0],
            "rush_yards_gained_exp": [25.0, 0.0],
            "rush_touchdown_exp": [0.4, 0.0],
            "pass_yards_gained_exp": [0.0, 0.0],
            "pass_touchdown_exp": [0.0, 0.0],
            "total_fantasy_points_exp": [12.3, 0.0],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    )


def _nextgen() -> dict[str, pl.DataFrame]:
    common = {
        "season": pl.Int32,
        "season_type": pl.String,
        "week": pl.Int32,
        "player_gsis_id": pl.String,
    }
    passing = pl.DataFrame(
        {"season": [SEASON, SEASON], "season_type": ["REG", "REG"], "week": [1, 0], "player_gsis_id": [QB1, QB1],
         "avg_time_to_throw": [2.7, 2.9], "aggressiveness": [15.0, 16.0], "avg_intended_air_yards": [8.0, 8.5]},
        schema_overrides=common,
    )  # fmt: skip
    rushing = pl.DataFrame(
        {"season": [SEASON], "season_type": ["REG"], "week": [1], "player_gsis_id": [RB1],
         "efficiency": [3.9], "rush_yards_over_expected": [4.0]},
        schema_overrides=common,
    )  # fmt: skip
    receiving = pl.DataFrame(
        {"season": [SEASON], "season_type": ["REG"], "week": [1], "player_gsis_id": [WR1],
         "avg_separation": [3.1], "avg_cushion": [6.0], "avg_yac_above_expectation": [1.2], "avg_intended_air_yards": [9.0]},
        schema_overrides=common,
    )  # fmt: skip
    return {"nextgen_passing": passing, "nextgen_rushing": rushing, "nextgen_receiving": receiving}


def _write_lake(raw_root: Path) -> None:
    store = SnapshotStore(raw_root)
    store.write_snapshot("players", {"all": _players()})
    store.write_snapshot("ff_playerids", {"all": _ff_playerids()})
    store.write_snapshot("teams", {"all": _teams()})
    store.write_snapshot("schedules", {SEASON: _schedules()})
    store.write_snapshot("rosters_weekly", {SEASON: _rosters()})
    store.write_snapshot("player_stats", {SEASON: _player_stats()})
    store.write_snapshot("pbp", {SEASON: _pbp()})
    store.write_snapshot("snap_counts", {SEASON: _snap_counts()})
    store.write_snapshot("ff_opportunity", {SEASON: _ff_opportunity()})
    for name, frame in _nextgen().items():
        store.write_snapshot(name, {SEASON: frame})
    cache = SleeperPlayersCache(root=raw_root / "sleeper_players")
    today = datetime.now(UTC).date()
    cache._write(
        today, players_frame(_sleeper_raw(), snapshot_date=today), endpoint="test://players"
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> ProcessContext:
    root = tmp_path_factory.mktemp("core")
    raw_root = root / "raw"
    _write_lake(raw_root)
    overrides = root / "overrides.csv"
    overrides.write_text(
        f"gsis_id,sleeper_id,espn_id,yahoo_id,position,note\n{K01},1001,,,,Tyler Bass manual\n",
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dims_mod, "OVERRIDES_PATH", overrides)
        mp.setattr(dims_mod, "datetime", _FrozenDatetime)
        ctx = ProcessContext(
            seasons=[SEASON],
            raw=SnapshotStore(raw_root),
            processed=SnapshotStore(root / "processed"),
            processed_restricted=SnapshotStore(root / "processed" / "restricted"),
            settings=get_settings(),
        )
        run_process([SEASON], tables=TABLES, ctx=ctx)
    return ctx


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now`` is pinned so dim_game status is deterministic."""

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return NOW if tz is None else NOW.astimezone(tz)


def _row(df: pl.DataFrame, **filters) -> dict:
    out = df
    for key, value in filters.items():
        out = out.filter(pl.col(key) == value)
    assert out.height == 1, f"expected one row for {filters}, got {out.height}"
    return out.row(0, named=True)


# --------------------------------------------------------------------------- #
# dims
# --------------------------------------------------------------------------- #


def test_dim_team_keeps_32_franchises_with_aliases(built):
    dim_team = built.tables["dim_team"]["all"]
    assert dim_team.height == 32
    assert "OAK" not in dim_team["team"].to_list()
    assert _row(dim_team, team="LV")["aliases"] == ["OAK"]
    assert set(_row(dim_team, team="LA")["aliases"]) == {"STL", "SL", "LAR"}
    assert _row(dim_team, team="BUF")["aliases"] == []


def test_dim_game_status_and_canonical_teams(built):
    dim_game = built.tables["dim_game"][SEASON]
    assert dim_game.height == 4
    assert _row(dim_game, game_id=GAME)["status"] == "final"
    stale = _row(dim_game, game_id="2025_01_SD_STL")
    assert stale["status"] == "in_progress"
    assert (stale["home_team"], stale["away_team"]) == ("LA", "LAC")
    old = _row(dim_game, game_id="2025_01_OAK_DEN")
    assert old["status"] == "scheduled"
    assert old["away_team"] == "LV"
    assert _row(dim_game, game_id="2025_02_KC_PHI")["status"] == "scheduled"
    final = _row(dim_game, game_id=GAME)
    assert final["kickoff_utc"] == datetime(2025, 9, 8, 0, 20, tzinfo=UTC)
    assert (final["home_score"], final["away_score"], final["result"]) == (24, 21, 3)


def test_dim_game_status_with_explicit_clock():
    sched = _schedules()
    df = build_dim_game(sched, now=datetime(2025, 9, 7, 23, 0, tzinfo=UTC))  # 19:00 ET Sunday
    assert _row(df, game_id="2025_01_OAK_DEN")["status"] == "scheduled"  # 17:00Z kickoff, 6 h ago
    df = build_dim_game(sched, now=datetime(2025, 9, 7, 18, 0, tzinfo=UTC))
    assert _row(df, game_id="2025_01_OAK_DEN")["status"] == "in_progress"  # 1 h ago
    df = build_dim_game(sched, now=datetime(2025, 9, 7, 16, 0, tzinfo=UTC))
    assert _row(df, game_id="2025_01_OAK_DEN")["status"] == "scheduled"  # not kicked off yet


def test_normalized_name():
    df = pl.DataFrame(
        {
            "n": [
                "Dalton Kincaid Jr.",
                "Ja'Marr Chase",
                "Kenneth Walker III",
                "D.J. Moore",
                "Odell Beckham Jr",
            ]
        }
    )
    out = df.select(normalized_name(pl.col("n")))["n"].to_list()
    assert out == ["dalton kincaid", "jamarr chase", "kenneth walker", "dj moore", "odell beckham"]


def test_dim_player_crosswalk_precedence(built):
    dp = built.tables["dim_player"]["all"]
    assert dp.height == 11
    assert dp["sleeper_id"].dtype == pl.String
    assert (_row(dp, player_id=QB1)["sleeper_id"], _row(dp, player_id=QB1)["crosswalk_source"]) == (
        "101",
        "ff_playerids",
    )
    assert _row(dp, player_id=QB1)["espn_id"] == "3918298"
    assert _row(dp, player_id=QB1)["yahoo_id"] == "31833"
    assert _row(dp, player_id=QB1)["fantasypros_id"] == "17298"
    assert (_row(dp, player_id=RB1)["sleeper_id"], _row(dp, player_id=RB1)["crosswalk_source"]) == (
        "102",
        "rosters_weekly",
    )
    assert (_row(dp, player_id=WR1)["sleeper_id"], _row(dp, player_id=WR1)["crosswalk_source"]) == (
        "103",
        "sleeper_players",
    )
    assert (_row(dp, player_id=TE1)["sleeper_id"], _row(dp, player_id=TE1)["crosswalk_source"]) == (
        "104",
        "name_match",
    )
    assert (_row(dp, player_id=K01)["sleeper_id"], _row(dp, player_id=K01)["crosswalk_source"]) == (
        "1001",
        "manual",
    )
    assert (_row(dp, player_id=QB2)["sleeper_id"], _row(dp, player_id=QB2)["crosswalk_source"]) == (
        "201",
        "sleeper_players",
    )
    # no opportunities: the Sleeper name is not used
    assert _row(dp, player_id=WR3)["sleeper_id"] is None
    assert _row(dp, player_id=WR3)["crosswalk_source"] is None
    assert _row(dp, player_id=LB1)["sleeper_id"] is None
    assert _row(dp, player_id=QB1)["birth_date"].isoformat() == "1996-05-21"


def test_dim_player_unique_sleeper_id_rule(built):
    dp = built.tables["dim_player"]["all"]
    winner, loser = _row(dp, player_id=WR2), _row(dp, player_id=DUP2)
    assert (winner["sleeper_id"], winner["crosswalk_source"]) == ("999", "ff_playerids")
    assert loser["sleeper_id"] is None and loser["crosswalk_source"] is None
    with_id = dp.filter(pl.col("sleeper_id").is_not_null())
    assert with_id["sleeper_id"].n_unique() == with_id.height


def test_dim_player_fb_maps_to_rb(built):
    dp = built.tables["dim_player"]["all"]
    fb = _row(dp, player_id=FB1)
    assert (fb["position"], fb["position_raw"]) == ("RB", "FB")
    assert fb["sleeper_id"] == "105"
    assert _row(dp, player_id=LB1)["position"] == "LB"


def test_crosswalk_coverage_metric(built):
    dp = built.tables["dim_player"]["all"]
    cov = dims_mod.crosswalk_coverage(dp, _player_stats())
    row = _row(cov, season=SEASON)
    assert row["players"] == 8  # QB1 RB1 WR1 TE1 K01 FB1 QB2 WR2
    assert row["matched"] == 8
    assert row["coverage"] == 1.0
    assert row["unmatched"] == []


# --------------------------------------------------------------------------- #
# fact_team_game and fact_dst_game
# --------------------------------------------------------------------------- #


def test_prepare_pbp_flags():
    pbp = prepare_pbp(_pbp())
    assert pbp["is_play"].sum() == 15  # 11 BUF + 4 KC
    assert pbp["is_pass_att"].sum() == 7
    assert pbp["is_rush_att"].sum() == 7  # 6 BUF + the KC kneel
    assert pbp["is_target"].sum() == 7


def test_fact_team_game_offense_counts(built):
    tg = built.tables["fact_team_game"][SEASON]
    assert tg.height == 2
    buf = _row(tg, team="BUF")
    assert (buf["opponent"], buf["home"], buf["game_type"], buf["game_id"]) == (
        "KC",
        True,
        "REG",
        GAME,
    )
    assert buf["plays"] == 11
    assert buf["pass_att"] == 5
    assert buf["rush_att"] == 6
    assert buf["dropbacks"] == 6
    assert buf["sacks"] == 0
    assert buf["neutral_plays"] == 9
    assert buf["pass_rate"] == pytest.approx(6 / 11)
    assert buf["neutral_pass_rate"] == pytest.approx(6 / 9)
    assert buf["early_down_pass_rate"] == pytest.approx(5 / 8)
    assert buf["sec_per_play"] == pytest.approx(31.25)
    assert buf["neutral_sec_per_play"] == pytest.approx(190 / 6)
    assert buf["rz_trips"] == 2
    assert buf["rz_td_rate"] == pytest.approx(1.0)
    assert buf["gl_plays"] == 2
    assert (buf["pass_td"], buf["rush_td"]) == (1, 1)
    assert (buf["points"], buf["allowed_points"]) == (24, 21)
    assert (buf["interceptions"], buf["fumbles_lost"]) == (0, 1)
    assert (buf["field_goal_att"], buf["two_pt_att"]) == (0, 0)
    assert buf["proe"] is None  # pass_oe not provided

    kc = _row(tg, team="KC")
    assert kc["home"] is False
    assert kc["plays"] == 4
    assert kc["pass_att"] == 2  # sack and two-point try excluded
    assert kc["rush_att"] == 1  # the kneel
    assert kc["dropbacks"] == 4
    assert kc["sacks"] == 1
    assert kc["interceptions"] == 1
    assert kc["rz_trips"] == 1
    assert kc["rz_td_rate"] == pytest.approx(0.0)
    assert (kc["field_goal_att"], kc["field_goal_made"]) == (1, 1)
    assert (kc["two_pt_att"], kc["two_pt_conv"]) == (1, 0)
    assert kc["fumbles_lost"] == 0


def test_fact_team_game_allowed_mirrors(built):
    tg = built.tables["fact_team_game"][SEASON]
    buf, kc = _row(tg, team="BUF"), _row(tg, team="KC")
    assert buf["allowed_plays"] == kc["plays"] == 4
    assert buf["allowed_sacks"] == 1
    assert buf["allowed_interceptions"] == 1
    assert buf["allowed_field_goal_made"] == 1
    assert buf["allowed_rz_trips"] == 1
    assert buf["allowed_wr_targets"] == 2
    assert buf["allowed_rb_targets"] == 0
    assert kc["allowed_plays"] == 11
    assert kc["allowed_pass_rate"] == pytest.approx(6 / 11)
    assert kc["allowed_rb_targets"] == 1
    assert kc["allowed_wr_targets"] == 2
    assert kc["allowed_te_targets"] == 2
    assert kc["allowed_rb_rec_yds"] == 40
    assert kc["allowed_wr_rec_yds"] == 20
    assert kc["allowed_te_rec_yds"] == 5
    assert kc["allowed_rb_rush_att"] == 5
    assert kc["allowed_rb_rush_yds"] == 22
    assert kc["allowed_rb_rush_td"] == 1
    assert kc["allowed_rec_td_wr"] == 1
    assert kc["allowed_qb_pass_yds"] == 65
    assert kc["allowed_qb_pass_td"] == 1
    assert kc["allowed_qb_rush_yds"] == 6
    assert kc["allowed_fumbles_lost"] == 1
    assert kc["allowed_rz_trips"] == 2
    assert kc["allowed_gl_plays"] == 2


def test_fact_dst_game(built):
    dst = built.tables["fact_dst_game"][SEASON]
    assert dst.height == 2
    buf = _row(dst, team="BUF")
    assert buf["sacks"] == 1
    assert buf["interceptions"] == 1
    assert buf["fumble_recoveries"] == 0
    assert buf["def_td"] == 0
    assert buf["return_td"] == 0
    assert buf["blocked_kicks"] == 1
    assert buf["points_allowed"] == 21
    assert buf["yards_allowed"] == pytest.approx(-2.0)
    kc = _row(dst, team="KC")
    assert kc["sacks"] == 0
    assert kc["fumble_recoveries"] == 1
    assert kc["return_td"] == 1
    assert kc["blocked_kicks"] == 0
    assert kc["points_allowed"] == 24
    assert kc["yards_allowed"] == pytest.approx(93.0)


# --------------------------------------------------------------------------- #
# fact_player_game
# --------------------------------------------------------------------------- #


def test_fact_player_game_rows_and_keys(built):
    pg = built.tables["fact_player_game"][SEASON]
    assert pg.select(["player_id", "season", "week"]).is_duplicated().sum() == 0
    ids = set(pg["player_id"].to_list())
    assert ids == {QB1, RB1, WR1, TE1, K01, FB1, WR3, QB2, WR2}
    assert LB1 not in ids and DUP2 not in ids
    assert _row(pg, player_id=WR2)["team"] == "KC"  # traded duplicate with fewer touches dropped
    assert _row(pg, player_id=FB1)["position"] == "RB"
    assert set(pg["game_id"].to_list()) == {GAME}
    assert set(pg["game_type"].to_list()) == {"REG"}
    assert _row(pg, player_id=QB1)["kickoff_utc"] == datetime(2025, 9, 8, 0, 20, tzinfo=UTC)
    assert _row(pg, player_id=QB1)["home"] is True and _row(pg, player_id=QB2)["home"] is False


def test_fact_player_game_situational_counts(built):
    pg = built.tables["fact_player_game"][SEASON]
    qb = _row(pg, player_id=QB1)
    assert (qb["dropbacks"], qb["scrambles"], qb["designed_rush"]) == (6, 1, 0)
    assert (qb["pass_att"], qb["completions"], qb["pass_yds"], qb["pass_td"]) == (5, 4, 65, 1)
    assert qb["pass_epa"] == pytest.approx(5.5)
    rb = _row(pg, player_id=RB1)
    assert (rb["rz_carries"], rb["gl_carries"], rb["designed_rush"], rb["scrambles"]) == (
        3,
        2,
        5,
        0,
    )
    assert (rb["rz_targets"], rb["third_down_targets"], rb["two_min_targets"]) == (0, 1, 0)
    assert rb["adot"] == pytest.approx(-2.0)
    assert rb["fumbles_lost"] == 1
    wr = _row(pg, player_id=WR1)
    assert (wr["rz_targets"], wr["ez_targets"], wr["targets"], wr["rec_td"]) == (1, 1, 2, 1)
    assert wr["adot"] == pytest.approx(9.0)
    te = _row(pg, player_id=TE1)
    assert (te["two_min_targets"], te["third_down_targets"], te["ez_targets"]) == (1, 0, 0)
    assert te["adot"] == pytest.approx(6.5)
    k = _row(pg, player_id=K01)
    assert (
        k["fg_att"],
        k["fg_made"],
        k["fg_made_0_39"],
        k["fg_made_40_49"],
        k["fg_made_50p"],
        k["fg_missed"],
    ) == (2, 1, 1, 0, 0, 1)
    assert (k["pat_att"], k["pat_made"], k["played"]) == (1, 1, True)


def test_fact_player_game_team_context(built):
    pg = built.tables["fact_player_game"][SEASON]
    for pid in (QB1, RB1, WR1, TE1, K01, WR3):
        row = _row(pg, player_id=pid)
        assert row["team_targets"] == 5, pid
        assert row["team_rz_targets"] == 1
        assert row["team_rz_carries"] == 3
        assert row["team_gl_carries"] == 2
        assert row["team_pass_att"] == 5
        assert row["team_rush_att"] == 6
        assert row["team_plays"] == 11
        assert (row["team_pass_td"], row["team_rush_td"]) == (1, 1)
    kc = _row(pg, player_id=WR2)
    assert (kc["team_targets"], kc["team_plays"], kc["team_pass_att"]) == (2, 4, 2)


def test_fact_player_game_joins_and_roster_rows(built):
    pg = built.tables["fact_player_game"][SEASON]
    rb = _row(pg, player_id=RB1)
    assert (rb["offense_snaps"], rb["offense_pct"]) == (40, pytest.approx(0.67))
    assert rb["receptions_exp"] == pytest.approx(1.2)
    assert rb["rec_yards_exp"] == pytest.approx(9.5)
    assert rb["rush_yards_exp"] == pytest.approx(25.0)
    assert rb["total_fp_exp"] == pytest.approx(12.3)
    assert rb["efficiency"] == pytest.approx(3.9)
    assert rb["rush_yards_over_expected"] == pytest.approx(4.0)
    qb = _row(pg, player_id=QB1)
    assert qb["time_to_throw"] == pytest.approx(2.7)  # week-0 season total ignored
    assert qb["aggressiveness"] == pytest.approx(15.0)
    assert qb["avg_intended_air_yards"] == pytest.approx(8.0)
    wr = _row(pg, player_id=WR1)
    assert wr["avg_separation"] == pytest.approx(3.1)
    assert wr["avg_intended_air_yards"] == pytest.approx(9.0)  # receiving value preferred
    assert wr["total_fp_exp"] is None
    # rostered, active, no stats row: appended with zeros and played = false
    bench = _row(pg, player_id=WR3)
    assert bench["row_source"] == "roster"
    assert bench["played"] is False
    assert (bench["targets"], bench["carries"], bench["offense_snaps"]) == (0, 0, 0)
    assert bench["adot"] is None and bench["rec_epa"] is None
    assert bench["fantasy_points_ppr_ref"] == 0.0
    assert pg.filter(pl.col("player_id") == WR3).height == 1  # no week-2 row (no game)
    assert _row(pg, player_id=FB1)["played"] is True  # carries from stats and snaps


def test_remap_post_weeks():
    ngs = pl.DataFrame(
        {"season_type": ["REG", "POST", "POST", "POST", "POST"], "week": [18, 19, 20, 21, 23]}
    )
    out = _remap_post_weeks(ngs, [19, 20, 21, 22])
    assert out["week"].to_list() == [18, 19, 20, 21, 22]
    assert _remap_post_weeks(ngs, [])["week"].to_list() == [18, 19, 20, 21, 23]


def test_processed_snapshots_written(built):
    for table in TABLES:
        assert built.processed.latest_id(table) is not None
        manifest = built.processed.read_manifest(table)
        assert manifest["extra"]["builder"].startswith("ffml.data.process.")
        assert "pbp" in manifest["extra"]["inputs"] or table.startswith("dim_")
    assert built.raw_snapshot_ids.get("sleeper_players") is not None


# --------------------------------------------------------------------------- #
# Integration against the real raw lake (2025)
# --------------------------------------------------------------------------- #

RAW_ROOT = PROJECT_ROOT / "data" / "raw"
needs_lake = pytest.mark.skipif(
    not (RAW_ROOT / "player_stats" / "LATEST").exists(),
    reason="raw lake not present under data/raw",
)
SKILL = ["QB", "RB", "WR", "TE", "K"]


@pytest.mark.slow
@needs_lake
def test_integration_2025(tmp_path):
    ctx = ProcessContext(
        seasons=[2025],
        raw=SnapshotStore(RAW_ROOT),
        processed=SnapshotStore(tmp_path / "processed"),
        processed_restricted=SnapshotStore(tmp_path / "processed" / "restricted"),
        settings=get_settings(),
    )
    run_process([2025], tables=TABLES, ctx=ctx)
    dim_game = ctx.tables["dim_game"][2025]
    dim_player = ctx.tables["dim_player"]["all"]
    tg = ctx.tables["fact_team_game"][2025]
    dst = ctx.tables["fact_dst_game"][2025]
    pg = ctx.tables["fact_player_game"][2025]

    # keys unique
    assert dim_game["game_id"].is_duplicated().sum() == 0
    assert dim_player["player_id"].is_duplicated().sum() == 0
    with_id = dim_player.filter(pl.col("sleeper_id").is_not_null())
    assert with_id["sleeper_id"].is_duplicated().sum() == 0
    for frame in (tg, dst):
        assert frame.select(["team", "season", "week"]).is_duplicated().sum() == 0
    assert pg.select(["player_id", "season", "week"]).is_duplicated().sum() == 0

    # two team rows per game for every 2025 game
    per_game = tg.group_by("game_id").len()
    assert set(per_game["len"].to_list()) == {2}
    assert set(per_game["game_id"].to_list()) == set(dim_game["game_id"].to_list())
    assert dst.height == tg.height

    # players with more than one team (32 from stats rows alone in 2025; roster rows add more)
    multi = (
        pg.group_by("player_id").agg(pl.col("team").n_unique().alias("n")).filter(pl.col("n") > 1)
    )
    assert multi.height >= 32

    # crosswalk coverage for players with opportunities
    stats = ctx.read_raw("player_stats", seasons=[2025], columns=dims_mod.STATS_ACTIVITY_COLUMNS)
    coverage = dims_mod.crosswalk_coverage(dim_player, stats)
    assert coverage.filter(pl.col("season") == 2025)["coverage"][0] >= 0.98

    # targets: player sums tie to team_targets once targets to non-skill receivers are added back
    reg = pg.filter(pl.col("game_type") == "REG")
    by_tw = reg.group_by(["week", "team"]).agg(
        pl.col("targets").sum().alias("player_targets"),
        pl.col("team_targets").first().alias("team_targets"),
        pl.col("rec_yds").sum().alias("player_rec_yds"),
    )
    pbp = prepare_pbp(
        ctx.read_raw(
            "pbp",
            seasons=[2025],
            columns=[*PBP_PLAYER_COLUMNS, "week", "season_type", "passing_yards", "complete_pass"],
        )
    ).filter((pl.col("season_type") == "REG") & pl.col("not_no_play"))
    pbp = pbp.join(
        dim_player.select(
            pl.col("player_id").alias("receiver_player_id"), pl.col("position").alias("recv_pos")
        ),
        on="receiver_player_id",
        how="left",
    )
    non_skill = pl.col("is_target") & ~pl.col("recv_pos").is_in(SKILL).fill_null(False)
    pbp_tw = (
        pbp.group_by(["week", "posteam"])
        .agg(
            non_skill.sum().alias("non_skill_targets"),
            pl.col("passing_yards").sum().alias("pbp_pass_yds"),
            pl.col("passing_yards")
            .filter(pl.col("recv_pos").is_in(SKILL))
            .sum()
            .alias("pbp_pass_yds_skill"),
        )
        .rename({"posteam": "team"})
    )
    check = by_tw.join(pbp_tw, on=["week", "team"], how="left")
    assert check.height == 544
    mismatch = check.filter(
        pl.col("player_targets") + pl.col("non_skill_targets") != pl.col("team_targets")
    )
    assert mismatch.is_empty(), mismatch
    assert (check["non_skill_targets"] > 0).sum() <= 25  # one-off trick plays only

    # receiving yards vs team passing yards from pbp (within 2 percent)
    rel_skill = (check["player_rec_yds"] - check["pbp_pass_yds_skill"]).abs() / check[
        "pbp_pass_yds_skill"
    ].clip(lower_bound=1)
    assert rel_skill.max() <= 0.02, (
        check.with_columns(rel_skill.alias("rel")).sort("rel", descending=True).head(5)
    )
    rel_all = (check["player_rec_yds"] - check["pbp_pass_yds"]).abs() / check["pbp_pass_yds"].clip(
        lower_bound=1
    )
    assert (rel_all <= 0.02).mean() >= 0.98
