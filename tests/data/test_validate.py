"""Tests for the data validation suite (DE-2.5, QA-2.1).

Synthetic raw and processed snapshots are written into temporary
SnapshotStores: season 2025, four teams, 100 players, weeks 1-3 complete and
week 4 in progress (one game final, one still scheduled). ``NOW`` is the
Monday of week 4, so weeks 1-3 are older than the three-day grace period and
the week-4 Sunday game is not. One player is traded from KC to PHI before
week 3; the roster table reflects the move one week late, which the
team-change check must tolerate.

The last test is an integration run of the raw tier against the real lake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import polars as pl
import pytest

from ffml.config import PROJECT_ROOT
from ffml.data import validate as v
from ffml.data.ingest import check_schema, save_pins
from ffml.data.store import SnapshotStore, schema_items

SEASON = 2025
TEAMS = ("KC", "BUF", "PHI", "DAL")
PLAYERS_PER_TEAM = 25
POSITIONS = ("QB", "RB", "WR", "TE", "WR")
NOW = datetime(2025, 9, 29, 12, 0, tzinfo=UTC)
TRADED = "00-0000001"  # KC's RB, plays for PHI from week 3
TRADE_WEEK = 3

# (week, away, home, gameday, final)
GAMES: list[tuple[int, str, str, date, bool]] = [
    (1, "KC", "BUF", date(2025, 9, 7), True),
    (1, "PHI", "DAL", date(2025, 9, 7), True),
    (2, "BUF", "PHI", date(2025, 9, 14), True),
    (2, "DAL", "KC", date(2025, 9, 14), True),
    (3, "KC", "PHI", date(2025, 9, 21), True),
    (3, "BUF", "DAL", date(2025, 9, 21), True),
    (4, "DAL", "KC", date(2025, 9, 28), True),
    (4, "BUF", "PHI", date(2025, 9, 29), False),
]
PLAYS_PER_GAME = 110
STALE_GAME = "2025_02_BUF_PHI"  # week 2: older than three days at NOW
FRESH_GAME = "2025_04_DAL_KC"  # week 4 Sunday: inside the grace period at NOW
SCHEDULED_GAME = "2025_04_BUF_PHI"


def game_id(week: int, away: str, home: str) -> str:
    return f"{SEASON}_{week:02d}_{away}_{home}"


def kickoff(day: date) -> datetime:
    return datetime.combine(day, time(17, 0), tzinfo=UTC)


def players() -> list[dict]:
    rows = []
    n = 0
    for t, team in enumerate(TEAMS):
        for i in range(PLAYERS_PER_TEAM):
            n += 1
            pos = POSITIONS[i % len(POSITIONS)]
            rows.append(
                {
                    "player_id": f"00-0{t}{i:05d}",
                    "full_name": f"{team} {pos} {i}",
                    "first_name": team,
                    "last_name": f"{pos}{i}",
                    "position": pos,
                    "team": team,
                    "sleeper_id": str(1000 + n),
                    "espn_id": str(2000 + n),
                    "yahoo_id": str(3000 + n),
                }
            )
    return rows


def team_in_week(player: dict, week: int) -> str:
    """Team the player actually plays for in ``week`` (the trade is immediate)."""
    if player["player_id"] == TRADED and week >= TRADE_WEEK:
        return "PHI"
    return player["team"]


def roster_team(player: dict, week: int) -> str:
    """Team the roster table shows in ``week`` (the trade appears one week late)."""
    if player["player_id"] == TRADED and week >= TRADE_WEEK + 1:
        return "PHI"
    return player["team"]


def usage(pos: str) -> dict[str, int]:
    return {
        "targets": 6 if pos in ("WR", "TE") else 0,
        "carries": 15 if pos == "RB" else 0,
        "attempts": 30 if pos == "QB" else 0,
    }


# ----------------------------------------------------------------------------- raw frames
def schedules_frame() -> pl.DataFrame:
    rows = []
    for week, away, home, day, final in GAMES:
        rows.append(
            {
                "game_id": game_id(week, away, home),
                "season": SEASON,
                "game_type": "REG",
                "week": week,
                "gameday": day.isoformat(),
                "weekday": "Sunday",
                "gametime": "13:00",
                "away_team": away,
                "away_score": 17 if final else None,
                "home_team": home,
                "home_score": 24 if final else None,
                "result": 7 if final else None,
            }
        )
    ints = {c: pl.Int32 for c in ("season", "week", "away_score", "home_score", "result")}
    return pl.DataFrame(rows, schema_overrides=ints)


def player_stats_rows(game: tuple, roster: list[dict]) -> list[dict]:
    week, away, home, _day, _final = game
    rows = []
    for team, opponent in ((away, home), (home, away)):
        for p in roster:
            if team_in_week(p, week) != team:
                continue
            rows.append(
                {
                    "player_id": p["player_id"],
                    "player_name": p["full_name"],
                    "player_display_name": p["full_name"],
                    "position": p["position"],
                    "team": team,
                    "opponent_team": opponent,
                    "season": SEASON,
                    "week": week,
                    "season_type": "REG",
                    "game_id": game_id(week, away, home),
                    **usage(p["position"]),
                    "fantasy_points_ppr": 10.5,
                }
            )
    return rows


def player_stats_frame(roster: list[dict], *, include_scheduled: bool = False) -> pl.DataFrame:
    rows = []
    for game in GAMES:
        if game[4] or include_scheduled:
            rows.extend(player_stats_rows(game, roster))
    ints = {c: pl.Int32 for c in ("season", "week", "targets", "carries", "attempts")}
    return pl.DataFrame(rows, schema_overrides=ints)


def pbp_frame() -> pl.DataFrame:
    rows = []
    for week, away, home, _day, final in GAMES:
        if not final:
            continue
        gid = game_id(week, away, home)
        base = {
            "game_id": gid,
            "season": SEASON,
            "week": week,
            "home_team": home,
            "away_team": away,
        }
        rows.append(
            {
                **base,
                "play_id": 0.0,
                "posteam": None,
                "play_type": None,
                "desc": "GAME",
                "timeout": 0.0,
            }
        )
        for i in range(1, PLAYS_PER_GAME + 1):
            rows.append(
                {
                    **base,
                    "play_id": float(i),
                    "posteam": away if i % 2 else home,
                    "play_type": "pass" if i % 3 else "run",
                    "desc": f"({i}) play",
                    "timeout": 0.0,
                }
            )
        rows.append(
            {
                **base,
                "play_id": 200.0,
                "posteam": None,
                "play_type": "no_play",
                "desc": f"Timeout #1 by {home} at 05:00.",
                "timeout": 1.0,
            }
        )
        rows.append(
            {
                **base,
                "play_id": 201.0,
                "posteam": None,
                "play_type": None,
                "desc": "END QUARTER 2",
                "timeout": 0.0,
            }
        )
        rows.append(
            {
                **base,
                "play_id": 202.0,
                "posteam": None,
                "play_type": None,
                "desc": "END GAME",
                "timeout": 0.0,
            }
        )
    return pl.DataFrame(
        rows,
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "posteam": pl.String,
            "play_type": pl.String,
        },
    )


def rosters_frame(roster: list[dict]) -> pl.DataFrame:
    rows = []
    for week in range(1, 5):
        for p in roster:
            rows.append(
                {
                    "season": SEASON,
                    "week": week,
                    "team": roster_team(p, week),
                    "position": p["position"],
                    "full_name": p["full_name"],
                    "gsis_id": p["player_id"],
                    "sleeper_id": p["sleeper_id"],
                    "espn_id": p["espn_id"],
                    "yahoo_id": p["yahoo_id"],
                    "status": "ACT",
                }
            )
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


# ----------------------------------------------------------------------------- processed frames
def dim_player_frame(roster: list[dict]) -> pl.DataFrame:
    rows = [
        {
            "player_id": p["player_id"],
            "full_name": p["full_name"],
            "first_name": p["first_name"],
            "last_name": p["last_name"],
            "position": p["position"],
            "sleeper_id": p["sleeper_id"],
            "espn_id": p["espn_id"],
            "yahoo_id": p["yahoo_id"],
            "crosswalk_source": "ff_playerids",
            "latest_team": team_in_week(p, 4),
            "updated_at": NOW,
        }
        for p in roster
    ]
    return pl.DataFrame(rows, schema_overrides={"sleeper_id": pl.String})


def dim_game_frame() -> pl.DataFrame:
    rows = []
    for week, away, home, day, final in GAMES:
        rows.append(
            {
                "game_id": game_id(week, away, home),
                "season": SEASON,
                "week": week,
                "game_type": "REG",
                "kickoff_utc": kickoff(day),
                "home_team": home,
                "away_team": away,
                "home_score": 24 if final else None,
                "away_score": 17 if final else None,
                "result": 7 if final else None,
                "status": "final" if final else "scheduled",
            }
        )
    ints = {c: pl.Int32 for c in ("season", "week", "home_score", "away_score", "result")}
    return pl.DataFrame(rows, schema_overrides=ints)


def fact_player_game_frame(roster: list[dict]) -> pl.DataFrame:
    rows = []
    for week, away, home, _day, final in GAMES:
        if not final:
            continue
        for team, opponent in ((away, home), (home, away)):
            for p in roster:
                if team_in_week(p, week) != team:
                    continue
                u = usage(p["position"])
                rows.append(
                    {
                        "player_id": p["player_id"],
                        "season": SEASON,
                        "week": week,
                        "game_id": game_id(week, away, home),
                        "team": team,
                        "opponent": opponent,
                        "position": p["position"],
                        "played": True,
                        "pass_att": u["attempts"],
                        "carries": u["carries"],
                        "targets": u["targets"],
                    }
                )
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def fact_team_game_frame() -> pl.DataFrame:
    rows = []
    for week, away, home, _day, final in GAMES:
        if not final:
            continue
        for team, opponent, is_home in ((away, home, False), (home, away, True)):
            rows.append(
                {
                    "team": team,
                    "season": SEASON,
                    "week": week,
                    "game_id": game_id(week, away, home),
                    "opponent": opponent,
                    "home": is_home,
                    "plays": PLAYS_PER_GAME // 2,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def week_day(week: int) -> date:
    return next(day for w, _a, _h, day, _f in GAMES if w == week)


def fact_roster_week_frame(roster: list[dict]) -> pl.DataFrame:
    rows = []
    for week in range(1, 5):
        snapshot = kickoff(week_day(week) - timedelta(days=1))
        for p in roster:
            rows.append(
                {
                    "season": SEASON,
                    "week": week,
                    "team": roster_team(p, week),
                    "player_id": p["player_id"],
                    "status": "ACT",
                    "status_description": "Active",
                    "snapshot_ts": snapshot,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def fact_depth_chart_frame(roster: list[dict]) -> pl.DataFrame:
    rows = []
    for week in range(1, 5):
        snapshot = kickoff(week_day(week) - timedelta(days=1))
        for p in roster:
            if p["position"] not in ("QB", "RB") or int(p["player_id"][-5:]) > 1:
                continue
            rows.append(
                {
                    "snapshot_ts": snapshot,
                    "season": SEASON,
                    "week": week,
                    "team": team_in_week(p, week),
                    "player_id": p["player_id"],
                    "pos_grp": "offense",
                    "pos_abb": p["position"],
                    "pos_rank": 1,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def fact_injury_report_frame(roster: list[dict]) -> pl.DataFrame:
    hurt = [p for p in roster if p["position"] == "WR"][:3]
    rows = []
    for i, p in enumerate(hurt):
        day = week_day(2)
        rows.append(
            {
                "season": SEASON,
                "week": 2,
                "team": p["team"],
                "player_id": p["player_id"],
                "report_status": "Questionable",
                "practice_status": "Limited Participation in Practice",
                "report_modified_at": kickoff(day - timedelta(days=2)) if i else None,
                "snapshot_ts": kickoff(day - timedelta(days=1)),
            }
        )
    return pl.DataFrame(
        rows,
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "report_modified_at": pl.Datetime("us", "UTC"),
        },
    )


# ----------------------------------------------------------------------------- scenario builder
@dataclass
class Scenario:
    drop_player_stats_column: str | None = None
    duplicate_player_week: bool = False
    drop_pbp_game: str | None = None
    unmatched_sleeper: int = 0
    team_change_violations: int = 0
    null_snapshot_ts: bool = False
    stats_for_scheduled_game: bool = False
    processed: bool = True


@dataclass
class Built:
    raw_root: Path
    processed_root: Path
    pin_path: Path
    report_path: Path

    def run(self, **overrides) -> v.SuiteResult:
        kwargs = dict(
            seasons=[SEASON],
            report_path=self.report_path,
            raw_root=self.raw_root,
            processed_root=self.processed_root,
            pin_path=self.pin_path,
            now_utc=NOW,
        )
        kwargs.update(overrides)
        return v.run_suite(**kwargs)


def build(root: Path, scenario: Scenario | None = None) -> Built:
    sc = scenario or Scenario()
    roster = players()
    raw = {
        "schedules": schedules_frame(),
        "player_stats": player_stats_frame(roster, include_scheduled=sc.stats_for_scheduled_game),
        "pbp": pbp_frame(),
        "rosters_weekly": rosters_frame(roster),
    }
    # pins come from the clean layout, before any mutation
    pins: dict = {}
    for name, df in raw.items():
        check_schema(name, dict(schema_items(df)), pins, season=SEASON)
    pin_path = root / "expected_schemas.json"
    save_pins(pin_path, pins)

    if sc.drop_player_stats_column:
        raw["player_stats"] = raw["player_stats"].drop(sc.drop_player_stats_column)
    if sc.duplicate_player_week:
        raw["player_stats"] = pl.concat([raw["player_stats"], raw["player_stats"].head(1)])
    if sc.drop_pbp_game:
        raw["pbp"] = raw["pbp"].filter(pl.col("game_id") != sc.drop_pbp_game)

    raw_store = SnapshotStore(root / "raw")
    for name, df in raw.items():
        raw_store.write_snapshot(
            name,
            {SEASON: df},
            meta={"loader": f"test.{name}", "dataset_name": name, "key_columns": []},
        )

    processed_root = root / "processed"
    if sc.processed:
        dim_roster = [dict(p) for p in roster]
        for p in dim_roster[: sc.unmatched_sleeper]:
            p["sleeper_id"] = None
        fpg = fact_player_game_frame(roster)
        if sc.team_change_violations:
            fpg = fpg.with_columns(
                pl.when(pl.int_range(pl.len()) < sc.team_change_violations)
                .then(pl.lit("NYJ"))
                .otherwise(pl.col("team"))
                .alias("team")
            )
        fdc = fact_depth_chart_frame(roster)
        if sc.null_snapshot_ts:
            fdc = fdc.with_columns(
                pl.when(pl.int_range(pl.len()) == 0)
                .then(None)
                .otherwise(pl.col("snapshot_ts"))
                .alias("snapshot_ts")
            )
        processed = {
            "dim_player": {"all": dim_player_frame(dim_roster)},
            "dim_game": {SEASON: dim_game_frame()},
            "fact_player_game": {SEASON: fpg},
            "fact_team_game": {SEASON: fact_team_game_frame()},
            "fact_roster_week": {SEASON: fact_roster_week_frame(roster)},
            "fact_depth_chart": {SEASON: fdc},
            "fact_injury_report": {SEASON: fact_injury_report_frame(roster)},
        }
        store = SnapshotStore(processed_root)
        for table, frames in processed.items():
            store.write_snapshot(table, frames, meta={"builder": "test"})
    return Built(root / "raw", processed_root, pin_path, root / "validation.md")


def statuses(suite: v.SuiteResult) -> dict[str, str]:
    return {r.name: r.status for r in suite.results}


@pytest.fixture(scope="module")
def clean(tmp_path_factory) -> tuple[Built, v.SuiteResult]:
    built = build(tmp_path_factory.mktemp("clean"))
    return built, built.run()


# ----------------------------------------------------------------------------- tests
def test_clean_fixture_passes_every_check(clean):
    _built, suite = clean
    assert statuses(suite) == {name: v.PASS for name, _tier, _fn in v.CHECKS}
    assert suite.ok
    assert suite.summary_line == "12 passed, 0 warned, 0 failed, 0 skipped"
    by = suite.by_name()
    assert by["game_completeness"].metrics["final_games"] == 7
    assert by["weekly_volume"].metrics["completed_weeks"] == [1, 2, 3]
    assert by["id_crosswalk"].metrics["per_season"][SEASON]["sleeper_share"] == 1.0
    assert by["team_changes"].metrics["multi_team_players"] == {SEASON: 1}
    assert by["partial_week"].metrics["projection_week"] == 4
    assert by["partial_week"].metrics["scheduled_teams"] == ["BUF", "PHI"]


def test_removed_column_fails_schema_pins(tmp_path):
    suite = build(tmp_path, Scenario(drop_player_stats_column="fantasy_points_ppr")).run()
    result = suite.by_name()["schema_pins"]
    assert result.status == v.FAIL
    assert "removed ['fantasy_points_ppr']" in result.message
    assert "player_stats" in result.metrics["drift"]
    assert not suite.ok


def test_duplicate_player_week_fails(tmp_path):
    suite = build(tmp_path, Scenario(duplicate_player_week=True)).run()
    result = suite.by_name()["duplicates"]
    assert result.status == v.FAIL
    stats = result.metrics["datasets"]["player_stats"]
    assert stats["duplicate_rows"] == 1
    assert stats["examples"] == [{"player_id": "00-0000000", "season": SEASON, "week": 1}]
    assert result.metrics["datasets"]["pbp"]["duplicate_rows"] == 0


def test_missing_game_older_than_three_days_fails(tmp_path):
    suite = build(tmp_path, Scenario(drop_pbp_game=STALE_GAME)).run()
    result = suite.by_name()["game_completeness"]
    assert result.status == v.FAIL
    assert STALE_GAME in result.message
    assert result.metrics["stale_gaps"] == 1 and result.metrics["fresh_gaps"] == 0
    assert result.metrics["gaps"][0]["reasons"] == ["0 plays"]


def test_missing_recent_game_only_warns(tmp_path):
    suite = build(tmp_path, Scenario(drop_pbp_game=FRESH_GAME)).run()
    result = suite.by_name()["game_completeness"]
    assert result.status == v.WARN
    assert result.metrics["fresh_gaps"] == 1 and result.metrics["stale_gaps"] == 0
    assert suite.ok


def test_crosswalk_coverage_fails_at_90_percent(tmp_path):
    suite = build(tmp_path, Scenario(unmatched_sleeper=10)).run()
    result = suite.by_name()["id_crosswalk"]
    assert result.status == v.FAIL
    season = result.metrics["per_season"][SEASON]
    assert season["active_players"] == 100 and season["sleeper_share"] == 0.9
    assert season["espn_share"] == 1.0 and season["yahoo_share"] == 1.0
    assert len(result.metrics["unmatched"][SEASON]) == 10
    assert "KC QB 0" in result.metrics["unmatched"][SEASON]


def test_crosswalk_coverage_warns_at_99_percent(tmp_path):
    suite = build(tmp_path, Scenario(unmatched_sleeper=1)).run()
    result = suite.by_name()["id_crosswalk"]
    assert result.status == v.WARN
    assert result.metrics["per_season"][SEASON]["sleeper_share"] == 0.99
    assert result.metrics["unmatched"][SEASON] == ["KC QB 0"]
    assert suite.ok


def test_team_change_inconsistency_above_half_percent_fails(tmp_path):
    suite = build(tmp_path, Scenario(team_change_violations=3)).run()
    result = suite.by_name()["team_changes"]
    assert result.status == v.FAIL
    # 7 final games x 50 rows, minus the traded RB who sits out week 4 (PHI's game is pending)
    assert result.metrics["player_weeks"] == 349
    assert result.metrics["violations"] == 3
    assert result.metrics["violation_share"] == pytest.approx(3 / 349, abs=1e-5)
    assert len(result.metrics["examples"]) == 3
    assert all(e["team"] == "NYJ" for e in result.metrics["examples"])


def test_null_snapshot_ts_fails_point_in_time(tmp_path):
    suite = build(tmp_path, Scenario(null_snapshot_ts=True)).run()
    result = suite.by_name()["point_in_time"]
    assert result.status == v.FAIL
    assert "fact_depth_chart.snapshot_ts: 1 null(s)" in result.message
    assert result.metrics["fact_depth_chart.snapshot_ts"]["later_than_fetch"] == 0


def test_stats_for_scheduled_game_fail_partial_week(tmp_path):
    suite = build(tmp_path, Scenario(stats_for_scheduled_game=True)).run()
    result = suite.by_name()["partial_week"]
    assert result.status == v.FAIL
    assert result.metrics["offending_rows"] == 51  # 25 BUF + 26 PHI (including the traded RB)
    assert result.metrics["scheduled_games"] == 1


def test_processed_checks_skip_when_tables_are_absent(tmp_path):
    suite = build(tmp_path, Scenario(processed=False)).run()
    by_tier = {tier: {r.status for r in suite.results if r.tier == tier} for tier in v.TIERS}
    assert by_tier[v.RAW] == {v.PASS}
    assert by_tier[v.PROCESSED] == {v.SKIP}
    assert suite.ok
    assert suite.summary_line == "6 passed, 0 warned, 0 failed, 6 skipped"


def test_tiers_can_be_selected(clean):
    built, _ = clean
    suite = built.run(tiers=["raw"], report_path=built.report_path.with_name("raw_only.md"))
    assert [r.tier for r in suite.results] == [v.RAW] * 6
    with pytest.raises(ValueError):
        built.run(tiers=["nope"])


def test_report_is_written_with_summary_line(clean):
    built, suite = clean
    assert suite.report_path == built.report_path
    text = built.report_path.read_text(encoding="utf-8")
    assert text.startswith("# Data validation report")
    assert re.search(r"^\*\*\d+ passed, \d+ warned, \d+ failed, \d+ skipped\*\*$", text, re.M)
    assert "**12 passed, 0 warned, 0 failed, 0 skipped**" in text
    assert "Overall verdict: **PASS**" in text
    for name, tier, _fn in v.CHECKS:
        assert re.search(rf"^\| \d+ \| {name} \| {tier} \| PASS \| ", text, re.M), name
    assert "### 10. team_changes (processed): PASS" in text


def test_crashing_check_becomes_fail_not_exception(tmp_path, monkeypatch):
    def boom(ctx: v.ValidationContext) -> v.CheckResult:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(v, "CHECKS", [*v.CHECKS, ("boom", v.RAW, boom)])
    suite = build(tmp_path).run()
    result = suite.by_name()["boom"]
    assert result.status == v.FAIL
    assert "RuntimeError: kaboom" in result.message
    assert any("kaboom" in line for line in result.metrics["traceback"])
    assert not suite.ok
    assert suite.summary_line == "12 passed, 0 warned, 1 failed, 0 skipped"
    assert "| boom | raw | FAIL |" in suite.report_path.read_text(encoding="utf-8")


def test_run_validation_returns_bool(tmp_path):
    built = build(tmp_path, Scenario(duplicate_player_week=True))
    assert (
        v.run_validation(
            [SEASON],
            report_path=built.report_path,
            raw_root=built.raw_root,
            processed_root=built.processed_root,
            pin_path=built.pin_path,
            now_utc=NOW,
        )
        is False
    )
    assert built.report_path.exists()


def test_check_result_rejects_unknown_status():
    with pytest.raises(ValueError):
        v.CheckResult("x", v.RAW, "MAYBE", "")
    with pytest.raises(ValueError):
        v.CheckResult("x", "gold", v.PASS, "")


# ----------------------------------------------------------------------------- real lake
@pytest.mark.slow
def test_raw_tier_against_real_lake(tmp_path):
    raw_root = PROJECT_ROOT / "data" / "raw"
    if not (raw_root / "player_stats" / "LATEST").exists():
        pytest.skip("no raw lake under data/raw")
    suite = v.run_suite(
        range(2016, 2027),
        report_path=tmp_path / "validation_raw.md",
        tiers=[v.RAW],
        raw_root=raw_root,
        processed_root=PROJECT_ROOT / "data" / "processed",
    )
    print(f"\nreport: {suite.report_path}")
    for r in suite.results:
        print(f"{r.name}: {r.status} {r.message[:200]}")
    failed = [r for r in suite.results if r.failed]
    assert not failed, "; ".join(f"{r.name}: {r.message}" for r in failed)
