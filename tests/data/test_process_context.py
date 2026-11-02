"""Synthetic tests for the context builders, the ECR builder and the Sleeper players table."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from ffml.data.process import context as ctxmod
from ffml.data.process import ecr as ecrmod
from ffml.data.process.base import ProcessContext
from ffml.data.process.sleeper_players import build_sleeper_players, normalize_sleeper_players
from ffml.data.store import SnapshotStore

ET = ZoneInfo("America/New_York")
UTC_US = pl.Datetime("us", "UTC")

# Three weeks per season: week 1 Thu/Sun/Mon, week 2 Thu/Sun/Mon, week 3 Sun/Mon only.
GAMES: dict[int, list[tuple[int, str, str, str, str]]] = {
    2024: [
        (1, "2024-09-05", "20:20", "KC", "BAL"),
        (1, "2024-09-08", "13:00", "ATL", "PIT"),
        (1, "2024-09-09", "20:15", "SF", "NYJ"),
        (2, "2024-09-12", "20:15", "MIA", "BUF"),
        (2, "2024-09-15", "13:00", "KC", "CIN"),
        (2, "2024-09-16", "20:15", "PHI", "ATL"),
        (3, "2024-09-22", "13:00", "JAX", "BUF"),
        (3, "2024-09-23", "20:15", "CIN", "WAS"),
    ],
    2025: [
        (1, "2025-09-04", "20:20", "PHI", "DAL"),
        (1, "2025-09-07", "13:00", "ATL", "TB"),
        (1, "2025-09-08", "20:15", "CHI", "MIN"),
        (2, "2025-09-11", "20:15", "GB", "WAS"),
        (2, "2025-09-14", "13:00", "KC", "PHI"),
        (2, "2025-09-15", "20:15", "HOU", "TB"),
        (3, "2025-09-21", "13:00", "LA", "PHI"),
        (3, "2025-09-22", "20:15", "BAL", "DET"),
    ],
}


def et(year: int, month: int, day: int, hour: int = 13, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(UTC)


def tiny_schedule(*seasons: int) -> pl.DataFrame:
    rows = [
        {
            "season": season,
            "game_type": "REG",
            "week": week,
            "gameday": gameday,
            "gametime": gametime,
            "result": None,
            "home_team": home,
            "away_team": away,
            "game_id": f"{season}_{week:02d}_{away}_{home}",
        }
        for season in seasons
        for week, gameday, gametime, home, away in GAMES[season]
    ]
    return pl.DataFrame(
        rows,
        schema={
            "season": pl.Int32,
            "game_type": pl.String,
            "week": pl.Int32,
            "gameday": pl.String,
            "gametime": pl.String,
            "result": pl.Int32,
            "home_team": pl.String,
            "away_team": pl.String,
            "game_id": pl.String,
        },
    )


# --------------------------------------------------------------------------- #
# Schedule windows
# --------------------------------------------------------------------------- #


def test_week_windows_bounds():
    windows = ctxmod.week_windows(tiny_schedule(2024))
    assert windows["window_end_utc"].is_sorted()
    week1 = windows.filter(pl.col("week") == 1).row(0, named=True)
    assert week1["first_kickoff_utc"] == et(2024, 9, 5, 20, 20)
    assert week1["last_kickoff_utc"] == et(2024, 9, 9, 20, 15)
    assert week1["window_end_utc"] == et(2024, 9, 9, 20, 15)
    assert week1["game_type"] == "REG"
    week0 = windows.filter(pl.col("week") == 0).row(0, named=True)
    assert week0["game_type"] == ctxmod.OFFSEASON
    assert week0["window_end_utc"] == et(2024, 9, 5, 20, 20) - timedelta(days=60, microseconds=1)
    assert week0["first_kickoff_utc"] is None


def test_assign_week_rule():
    windows = ctxmod.week_windows(tiny_schedule(2024))
    last_week1 = et(2024, 9, 9, 20, 15)
    frame = pl.DataFrame(
        {
            "ts": [
                last_week1,  # exactly the last kickoff of week 1 -> week 1 (inclusive)
                last_week1 + timedelta(minutes=1),  # just after -> week 2
                datetime(2024, 9, 10, 12, tzinfo=UTC),  # mid week 2
                et(2024, 9, 16, 20, 15) + timedelta(seconds=1),  # after week 2 -> week 3
                datetime(2024, 8, 1, tzinfo=UTC),  # before week 1 within 60 days -> week 1
                datetime(2024, 5, 1, tzinfo=UTC),  # long before -> week 0 offseason
                datetime(2024, 12, 1, tzinfo=UTC),  # after the last window -> fallback
            ],
            "part": [2024] * 7,
        }
    ).with_columns(pl.col("ts").cast(UTC_US))
    out = ctxmod.assign_week(frame, windows, "ts", fallback_season_col="part")
    assert out["week"].to_list() == [1, 2, 2, 3, 1, 0, 0]
    assert out["season"].to_list() == [2024] * 7
    assert out["game_type"].to_list() == ["REG"] * 5 + [ctxmod.OFFSEASON] * 2
    # input order is preserved
    assert out["ts"].to_list() == frame["ts"].to_list()


# --------------------------------------------------------------------------- #
# fact_injury_report
# --------------------------------------------------------------------------- #


def _injury_rows(season_dtype: pl.DataType = pl.Float64) -> pl.DataFrame:
    stamp = lambda d, h: datetime(2024, 12, d, h, tzinfo=UTC)  # noqa: E731
    rows = [
        # P1: Questionable then Out one day later -> latest wins (Out)
        ("NYJ", "00-P1", "Questionable", stamp(14, 20)),
        ("NYJ", "00-P1", "Out", stamp(15, 13)),
        # P2: no timestamps -> most severe wins (Doubtful over Questionable)
        ("JAC", "00-P2", "Questionable", None),
        ("JAC", "00-P2", "Doubtful", None),
        # P3: Out first, Questionable later -> latest wins (Questionable)
        ("KC", "00-P3", "Out", stamp(13, 9)),
        ("KC", "00-P3", "Questionable", stamp(16, 9)),
        # P4: null status only
        ("KC", "00-P4", None, stamp(13, 9)),
    ]
    season_value = 2024.0 if season_dtype == pl.Float64 else 2024
    week_value = 15.0 if season_dtype == pl.Float64 else 15
    return pl.DataFrame(
        {
            "season": [season_value] * len(rows),
            "week": [week_value] * len(rows),
            "game_type": ["REG"] * len(rows),
            "team": [r[0] for r in rows],
            "gsis_id": [r[1] for r in rows],
            "position": ["WR"] * len(rows),
            "full_name": ["Someone"] * len(rows),
            "report_status": [r[2] for r in rows],
            "practice_status": [None] * len(rows),
            "report_primary_injury": ["Knee"] * len(rows),
            "report_secondary_injury": [None] * len(rows),
            "practice_primary_injury": [None] * len(rows),
            "practice_secondary_injury": [None] * len(rows),
            "date_modified": [r[3] for r in rows],
        },
        schema_overrides={"season": season_dtype, "week": season_dtype, "date_modified": UTC_US},
    )


def test_injury_dedupe_and_severity():
    snap = datetime(2026, 11, 2, 1, 59, 58, tzinfo=UTC)
    out = ctxmod.build_injury_report(_injury_rows(), snap)
    assert out.columns == ctxmod.INJURY_COLUMNS
    assert out.height == 4
    assert out.schema["season"] == pl.Int32 and out.schema["week"] == pl.Int32
    by_id = {r["player_id"]: r for r in out.iter_rows(named=True)}
    assert by_id["00-P1"]["report_status"] == "Out"
    assert by_id["00-P1"]["report_modified_at"] == datetime(2024, 12, 15, 13, tzinfo=UTC)
    assert by_id["00-P2"]["report_status"] == "Doubtful"
    assert by_id["00-P2"]["team"] == "JAX"  # canonical team code
    assert by_id["00-P3"]["report_status"] == "Questionable"
    assert by_id["00-P4"]["report_status"] is None
    assert out["snapshot_ts"].unique().to_list() == [snap]


def test_injury_new_layout_without_timestamp():
    raw = pl.DataFrame(
        {
            "season": [2025, 2025],
            "season_type": ["REG", "POST"],
            "game_type": ["REG", "WC"],
            "team": ["BUF", "BUF"],
            "week": [3, 19],
            "gsis_id": ["00-A", "00-A"],
            "position": ["QB", "QB"],
            "full_name": ["A", "A"],
            "report_primary_injury": [None, None],
            "report_secondary_injury": [None, None],
            "report_status": ["Questionable", None],
            "practice_primary_injury": [None, None],
            "practice_secondary_injury": [None, None],
            "practice_status": ["Limited", None],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    )
    out = ctxmod.build_injury_report(raw, datetime(2026, 1, 1, tzinfo=UTC))
    assert out["report_modified_at"].null_count() == 2
    assert out.schema["report_modified_at"] == UTC_US
    assert out["game_type"].to_list() == ["REG", "WC"]


# --------------------------------------------------------------------------- #
# fact_depth_chart
# --------------------------------------------------------------------------- #


def _legacy_depth_chart() -> pl.DataFrame:
    rows = [
        (2024, "JAC", 2, "REG", "1", "Offense", "00-A", "QB", "QB", "Player A"),
        (2024, "JAC", 2, "REG", "2", "Offense", "00-B", "QB", "QB", "Player B"),
        (2024, "JAC", 2, "REG", "3", "Offense", "00-C", "TE", "", "Player C"),  # blank slot
        (2024, "KC", 1, "REG", "1", "Special Teams", "00-D", "K", "K", "Player D"),
        (2024, "KC", None, "SBBYE", "1", "Offense", "00-E", "QB", "QB", "Player E"),  # dropped
    ]
    return pl.DataFrame(
        rows,
        schema={
            "season": pl.Int32,
            "club_code": pl.String,
            "week": pl.Int32,
            "game_type": pl.String,
            "depth_team": pl.String,
            "formation": pl.String,
            "gsis_id": pl.String,
            "position": pl.String,
            "depth_position": pl.String,
            "full_name": pl.String,
        },
        orient="row",
    )


def _timestamped_depth_chart() -> pl.DataFrame:
    rows = [
        # Tuesday of week 2 (after Monday night of week 1)
        ("2025-09-09T07:00:00Z", "LAR", "Player X", "00-X", "3WR 1TE", "WR", 1, 1),
        ("2025-09-09T07:00:00Z", "LAR", "Player Y", "00-Y", "3WR 1TE", "WR", 2, 2),
        (
            "2025-09-09T07:00:00Z",
            "LAR",
            "Player Y",
            "00-Y",
            "3WR 1TE",
            "WR",
            3,
            1,
        ),  # dup, better rank
        (
            "2025-09-09T07:00:00Z",
            "LAR",
            "Rookie",
            None,
            "3WR 1TE",
            "WR",
            4,
            3,
        ),  # no gsis -> dropped
        # Friday of week 3
        ("2025-09-19T07:00:00Z", "LAR", "Player X", "00-X", "3WR 1TE", "WR", 1, 1),
        # offseason
        ("2025-03-01T07:00:00Z", "LAR", "Player X", "00-X", "3WR 1TE", "WR", 1, 1),
    ]
    return pl.DataFrame(
        rows,
        schema={
            "dt": pl.String,
            "team": pl.String,
            "player_name": pl.String,
            "gsis_id": pl.String,
            "pos_grp": pl.String,
            "pos_abb": pl.String,
            "pos_slot": pl.Int32,
            "pos_rank": pl.Int32,
        },
        orient="row",
    )


def test_depth_chart_unification_and_week_assignment():
    windows = ctxmod.week_windows(tiny_schedule(2024, 2025))
    out = ctxmod.build_depth_chart(
        [(2024, _legacy_depth_chart()), (2025, _timestamped_depth_chart())], windows
    )
    assert out.columns == ctxmod.DEPTH_CHART_COLUMNS
    assert out.schema["snapshot_ts"] == UTC_US
    assert out.schema["pos_rank"] == pl.Int32
    assert out.select(ctxmod.DEPTH_CHART_KEYS).is_duplicated().sum() == 0

    legacy = out.filter(pl.col("snapshot_kind") == "weekly")
    assert legacy.height == 4  # SBBYE row dropped
    week2 = legacy.filter(pl.col("week") == 2)
    assert week2["snapshot_ts"].unique().to_list() == [
        et(2024, 9, 12, 20, 15) - timedelta(hours=48)
    ]
    assert week2["team"].unique().to_list() == ["JAX"]
    assert sorted(week2["pos_rank"].to_list()) == [1, 2, 3]
    assert week2.filter(pl.col("player_id") == "00-C")["pos_abb"][0] is None
    assert week2["formation"].unique().to_list() == ["Offense"]
    assert legacy.filter(pl.col("week") == 1)["snapshot_ts"][0] == et(
        2024, 9, 5, 20, 20
    ) - timedelta(hours=48)

    stamped = out.filter(pl.col("snapshot_kind") == "timestamped")
    assert stamped["team"].unique().to_list() == ["LA"]
    assert stamped["player_id"].null_count() == 0
    sept9 = stamped.filter(pl.col("snapshot_ts") == datetime(2025, 9, 9, 7, tzinfo=UTC))
    assert sept9.height == 2  # duplicate collapsed, rookie dropped
    assert sept9["week"].unique().to_list() == [2]
    assert sept9.filter(pl.col("player_id") == "00-Y")["pos_rank"][0] == 1
    assert sept9["formation"].sort().to_list() == ["1", "3"]  # pos_slot as string
    assert (
        stamped.filter(pl.col("snapshot_ts") == datetime(2025, 9, 19, 7, tzinfo=UTC))["week"][0]
        == 3
    )
    offseason = stamped.filter(pl.col("snapshot_ts") == datetime(2025, 3, 1, 7, tzinfo=UTC))
    assert offseason["week"][0] == 0
    assert offseason["season"][0] == 2025
    assert offseason["game_type"][0] == ctxmod.OFFSEASON


# --------------------------------------------------------------------------- #
# fact_roster_week
# --------------------------------------------------------------------------- #


def test_roster_week_id_casting():
    raw = pl.DataFrame(
        {
            "season": [2024, 2024, 2024, 2025],
            "week": [1, 1, 1, 1],
            "game_type": ["REG"] * 4,
            "team": ["JAC", "KC", "KC", "LAR"],
            "gsis_id": ["00-A", "00-A", None, "00-B"],
            "position": ["WR", "WR", "TE", "QB"],
            "full_name": ["A", "A", "Nobody", "B"],
            "status": ["ACT", "ACT", "DEV", "ACT"],
            "status_description_abbr": ["A01", "A01", "P01", "A01"],
            "depth_chart_position": ["WR", "WR", "TE", "QB"],
            "jersey_number": [11, 11, None, 9],
            "height": [73.0, 73.0, None, 76.0],
            "years_exp": [3, 3, 0, 1],
            "sleeper_id": ["4046", "4046", None, "11566"],
            "espn_id": [3139477, 3139477, None, 4431611],
            "yahoo_id": [30123.0, 30123.0, None, 40001.0],
            "pfr_id": ["MahoPa00", "MahoPa00", None, None],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32, "jersey_number": pl.Int32},
    )
    snap = datetime(2026, 11, 2, tzinfo=UTC)
    out = ctxmod.build_roster_week(raw, snap)
    assert out.columns == ctxmod.ROSTER_COLUMNS
    assert out.height == 3  # null gsis dropped; traded player keeps both teams
    assert sorted(out.filter(pl.col("player_id") == "00-A")["team"].to_list()) == ["JAX", "KC"]
    for col in ("sleeper_id", "espn_id", "yahoo_id", "pfr_id"):
        assert out.schema[col] == pl.String, col
    row = out.filter(pl.col("player_id") == "00-B").row(0, named=True)
    assert row["espn_id"] == "4431611"
    assert row["yahoo_id"] == "40001"
    assert row["team"] == "LA"
    assert row["height"] == 76 and out.schema["height"] == pl.Int32
    assert row["status_description"] == "A01"
    assert out["snapshot_ts"].unique().to_list() == [snap]


# --------------------------------------------------------------------------- #
# fact_ecr_weekly
# --------------------------------------------------------------------------- #


def test_ecr_page_name_and_scoring():
    pages = pl.DataFrame(
        {
            "fp_page": [
                "/nfl/rankings/ppr-wr.php",
                "ppr-rb",
                "/nfl/rankings/half-point-ppr-te.php",
                "qb",
                "/nfl/rankings/dst.php",
                "K",
            ]
        }
    )
    out = pages.select(
        ecrmod.page_name(pl.col("fp_page")).alias("page"),
        ecrmod.scoring_from_page(ecrmod.page_name(pl.col("fp_page"))).alias("scoring"),
    )
    assert out["page"].to_list() == ["ppr-wr", "ppr-rb", "half-point-ppr-te", "qb", "dst", "k"]
    assert out["scoring"].to_list() == ["ppr", "ppr", "half", "std", "std", "std"]


def _rankings() -> pl.DataFrame:
    rows = [
        # (fp_page, player, id, pos, team, ecr, ecr_type, scrape_date)
        ("/nfl/rankings/ppr-wr.php", "Player A", "100", "WR", "ATL", 1.0, "wp", "2024-09-04"),
        (
            "/nfl/rankings/ppr-wr.php",
            "Player A",
            "100",
            "WR",
            "ATL",
            1.0,
            "wp",
            "2024-09-04",
        ),  # dup
        ("ppr-wr", "Player A", "100", "WR", "KC", 1.5, "wp", "2024-09-06"),  # Friday after TNF (KC)
        ("ppr-wr", "Player B", "200", "WR", "ATL", 2.0, "wp", "2024-09-06"),  # ATL plays Sunday
        ("qb", "QB X", "300", "QB", "JAC", 3.0, "wp", "2024-09-13"),  # week 2, JAX on bye
        ("half-point-ppr-rb", "RB Y", "400", "RB", "FA", 4.0, "wp", "2024-09-20"),  # week 3
        ("k", "Kicker", "500", "PK", "CIN", 5.0, "wp", "2024-09-20"),
        ("dst", "Bills D/ST", "8010", "DEF", "BUF", 6.0, "wp", "2024-09-20"),
        ("db", "Safety", "600", "DB", "BUF", 7.0, "wp", "2024-09-20"),  # IDP: filtered
        ("ppr-wr", "Player A", "100", "WR", "ATL", 1.0, "ro", "2024-09-04"),  # not weekly: filtered
        ("ppr-wr", "Unknown", "999", "WR", "ATL", 9.0, "wp", "2024-09-04"),  # no crosswalk match
    ]
    return pl.DataFrame(
        rows,
        schema={
            "fp_page": pl.String,
            "player": pl.String,
            "id": pl.String,
            "pos": pl.String,
            "team": pl.String,
            "ecr": pl.Float64,
            "ecr_type": pl.String,
            "scrape_date": pl.String,
        },
        orient="row",
    ).with_columns(
        pl.col("ecr").alias("sd"),
        pl.col("ecr").floor().alias("best"),
        (pl.col("ecr") + 2).alias("worst"),
    )


def _playerids() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "fantasypros_id": [100, 200, 100, None, 500],
            "gsis_id": ["00-A", "00-B", "00-A-dup", "00-X", None],
        },
        schema_overrides={"fantasypros_id": pl.Int64},
    )


def test_ecr_week_validity_and_crosswalk():
    out = ecrmod.build_ecr_frame(_rankings(), tiny_schedule(2024), _playerids())
    assert out.columns == ecrmod.ECR_COLUMNS
    assert out.height == 8  # 11 rows - dup - IDP - non-weekly
    assert out.select(ecrmod.ECR_KEYS).is_duplicated().sum() == 0
    assert out.schema["scrape_date"] == pl.Date
    assert out.schema["fantasypros_id"] == pl.String

    def row(fp_id: str, day: str) -> dict:
        return out.filter(
            (pl.col("fantasypros_id") == fp_id) & (pl.col("scrape_date") == date.fromisoformat(day))
        ).row(0, named=True)

    wed = row("100", "2024-09-04")
    assert (wed["season"], wed["week"], wed["scoring"], wed["position"]) == (2024, 1, "ppr", "WR")
    assert wed["valid_for_week"] is True
    assert wed["player_id"] == "00-A"  # first crosswalk row wins for a duplicated id
    assert wed["fp_page"] == "ppr-wr"
    assert wed["team_game_date"] == date(2024, 9, 8) and wed["valid_for_game"] is True

    fri_kc = row("100", "2024-09-06")
    assert fri_kc["week"] == 1 and fri_kc["valid_for_week"] is False
    assert fri_kc["team_game_date"] == date(2024, 9, 5) and fri_kc["valid_for_game"] is False
    fri_atl = row("200", "2024-09-06")
    assert fri_atl["valid_for_week"] is False and fri_atl["valid_for_game"] is True

    qb = row("300", "2024-09-13")
    assert (qb["week"], qb["scoring"], qb["team"]) == (2, "std", "JAX")
    assert qb["team_game_date"] is None and qb["valid_for_game"] is None

    rb = row("400", "2024-09-20")
    assert (rb["week"], rb["scoring"], rb["team"]) == (3, "half", None)
    assert rb["valid_for_week"] is True  # week 3 has no Thursday game
    assert row("500", "2024-09-20")["position"] == "K"
    assert row("500", "2024-09-20")["player_id"] is None  # crosswalk row without gsis
    assert row("8010", "2024-09-20")["position"] == "DST"
    assert row("999", "2024-09-04")["player_id"] is None

    summary = ecrmod.ecr_summary(out)
    assert summary["season"].to_list() == [2024]
    s = summary.row(0, named=True)
    assert s["player_rows"] == 7 and s["match_rate"] == pytest.approx(3 / 7)  # ids 100, 100, 200
    assert s["week_scoring_combos"] == 4  # (1,ppr) (2,std) (3,half) (3,std)
    assert s["valid_combos"] == 3  # (1,ppr) via the Wednesday scrape, (3,half), (3,std)


# --------------------------------------------------------------------------- #
# sleeper_players
# --------------------------------------------------------------------------- #


def _sleeper_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "sleeper_id": ["4046", "4046", "KC", "11566"],
            "full_name": ["Patrick Mahomes", "Patrick Mahomes", "Kansas City Chiefs", "Puka Nacua"],
            "position": ["QB", "QB", "DEF", "WR"],
            "fantasy_positions": [["QB"], ["QB"], ["DEF"], ["WR"]],
            "team": ["KC", "KC", "KC", "LAR"],
            "status": ["Active"] * 4,
            "injury_status": [None, None, None, "Questionable"],
            "injury_body_part": [None, None, None, "Knee"],
            "gsis_id": [" 00-0033873", " 00-0033873", None, ""],
            "espn_id": ["3139477", "3139477", None, ""],
            "yahoo_id": ["30123", "30123", None, None],
            "age": [31, 31, None, 25],
            "years_exp": [9, 9, None, 3],
            "depth_chart_order": [1, 1, None, 1],
            "snapshot_date": [date(2026, 11, 2)] * 4,
        }
    )


def test_normalize_sleeper_players():
    out = normalize_sleeper_players(_sleeper_frame())
    assert out.height == 3
    assert out["sleeper_id"].is_sorted()
    mahomes = out.filter(pl.col("sleeper_id") == "4046").row(0, named=True)
    assert mahomes["gsis_id"] == "00-0033873"
    nacua = out.filter(pl.col("sleeper_id") == "11566").row(0, named=True)
    assert nacua["gsis_id"] is None and nacua["espn_id"] is None
    assert nacua["team"] == "LA"
    assert out.schema["snapshot_date"] == pl.Date
    assert out.schema["fantasy_positions"] == pl.List(pl.String)


def test_build_sleeper_players_without_cache_returns_empty(tmp_settings, monkeypatch):
    from ffml.data import sleeper as sleepermod

    def boom(self, *args, **kwargs):
        raise sleepermod.SleeperError("offline")

    monkeypatch.setattr(sleepermod.SleeperPlayersCache, "get", boom)
    ctx = ProcessContext.from_settings([2026], settings=tmp_settings)
    assert build_sleeper_players(ctx) == {}


def test_build_sleeper_players_uses_fetched_frame(tmp_settings, monkeypatch):
    from ffml.data import sleeper as sleepermod

    monkeypatch.setattr(
        sleepermod.SleeperPlayersCache, "get", lambda self, *a, **k: _sleeper_frame()
    )
    ctx = ProcessContext.from_settings([2026], settings=tmp_settings)
    result = build_sleeper_players(ctx)
    assert list(result) == ["sleeper_players"] and list(result["sleeper_players"]) == ["all"]
    assert result["sleeper_players"]["all"].height == 3
    assert ctx.raw_snapshot_ids["sleeper_players"] == "20261102"


# --------------------------------------------------------------------------- #
# End to end through the runner with a tiny raw lake
# --------------------------------------------------------------------------- #


def _write_tiny_raw_lake(raw: SnapshotStore) -> None:
    fetched = {"fetched_at": "2026-11-02T01:59:58Z", "source_license": "CC-BY-4.0 (nflverse)"}
    schedules = tiny_schedule(2024, 2025)
    raw.write_snapshot(
        "schedules",
        {s: schedules.filter(pl.col("season") == s) for s in (2024, 2025)},
        meta={"loader": "nflreadpy.load_schedules", **fetched},
    )
    raw.write_snapshot(
        "injuries",
        {
            2024: _injury_rows(pl.Int32),
            2025: pl.DataFrame(
                {
                    "season": [2025],
                    "season_type": ["REG"],
                    "game_type": ["REG"],
                    "team": ["KC"],
                    "week": [2],
                    "gsis_id": ["00-A"],
                    "position": ["QB"],
                    "full_name": ["A"],
                    "report_primary_injury": ["Ankle"],
                    "report_secondary_injury": [None],
                    "report_status": ["Questionable"],
                    "practice_primary_injury": ["Ankle"],
                    "practice_secondary_injury": [None],
                    "practice_status": ["Limited"],
                },
                schema_overrides={"season": pl.Int32, "week": pl.Int32},
            ),
        },
        meta={"loader": "nflreadpy.load_injuries", **fetched},
    )
    raw.write_snapshot(
        "depth_charts",
        {2024: _legacy_depth_chart(), 2025: _timestamped_depth_chart()},
        meta={"loader": "nflreadpy.load_depth_charts", **fetched},
    )
    rosters = pl.DataFrame(
        {
            "season": [2024, 2025],
            "week": [1, 1],
            "game_type": ["REG", "REG"],
            "team": ["KC", "LAR"],
            "gsis_id": ["00-A", "00-B"],
            "position": ["QB", "WR"],
            "full_name": ["A", "B"],
            "status": ["ACT", "ACT"],
            "status_description_abbr": ["A01", "A01"],
            "depth_chart_position": ["QB", "WR"],
            "jersey_number": [15, 17],
            "height": [75.0, 74.0],
            "years_exp": [8, 2],
            "sleeper_id": ["4046", "11566"],
            "espn_id": ["3139477", "4431611"],
            "yahoo_id": ["30123", "40001"],
            "pfr_id": ["MahoPa00", None],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32, "jersey_number": pl.Int32},
    )
    raw.write_snapshot(
        "rosters_weekly",
        {s: rosters.filter(pl.col("season") == s) for s in (2024, 2025)},
        meta={"loader": "nflreadpy.load_rosters_weekly", **fetched},
    )


def test_build_context_through_runner(tmp_settings):
    from ffml.data.process import run_process

    ctx = ProcessContext.from_settings([2024, 2025], settings=tmp_settings)
    _write_tiny_raw_lake(ctx.raw)
    tables = ["fact_injury_report", "fact_depth_chart", "fact_roster_week"]
    run_process([2024, 2025], tables=tables, ctx=ctx)

    assert set(tables) <= set(ctx.processed.list_datasets())
    manifest = ctx.processed.read_manifest("fact_injury_report")
    assert manifest["extra"]["inputs"]["injuries"] == ctx.raw.latest_id("injuries")
    assert manifest["extra"]["inputs"]["schedules"] == ctx.raw.latest_id("schedules")
    assert manifest["restricted"] is False

    injuries = ctx.processed.read("fact_injury_report")
    assert injuries.height == 5  # 4 deduplicated 2024 rows + 1 row for 2025
    assert injuries["snapshot_ts"].unique().to_list() == [
        datetime(2026, 11, 2, 1, 59, 58, tzinfo=UTC)
    ]
    depth = ctx.processed.read("fact_depth_chart")
    assert set(depth["snapshot_kind"].unique().to_list()) == {"weekly", "timestamped"}
    assert ctx.processed.partition_keys("fact_depth_chart") == [2024, 2025]
    rosters = ctx.processed.read("fact_roster_week")
    assert rosters["team"].to_list() == ["KC", "LA"]
    # the manifest of the processed table records the raw snapshot ids as JSON
    text = json.dumps(manifest["extra"]["inputs"])
    assert "injuries" in text and "schedules" in text
