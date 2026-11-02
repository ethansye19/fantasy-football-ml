"""Tests for the season/week resolver (DE-2.7)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from freezegun import freeze_time

from ffml.season import (
    SCHEDULE_COLUMNS,
    SeasonState,
    describe_current_state,
    resolve_state,
    with_kickoff_utc,
)

SCHEMA = {
    "season": pl.Int32,
    "game_type": pl.String,
    "week": pl.Int32,
    "gameday": pl.String,
    "gametime": pl.String,
    "result": pl.Int32,
    "home_team": pl.String,
    "away_team": pl.String,
    "game_id": pl.String,
}

# 2026 regular season: week 1 Thursday night 2026-09-10 ET.
WEEK1_THURSDAY = date(2026, 9, 10)


def make_schedule(
    season: int = 2026,
    *,
    weeks: int = 18,
    finals_through: int = 0,
    partial: tuple[int, int] | None = None,
    bye_weeks: dict[int, int] | None = None,
    playoffs: str | None = None,  # None, "pending", or "final"
    week1_thursday: date = WEEK1_THURSDAY,
) -> pl.DataFrame:
    """Synthetic schedule: Thu night, Sunday slate, Mon night per week."""
    rows = []
    for week in range(1, weeks + 1):
        games = (bye_weeks or {}).get(week, 16)
        thursday = week1_thursday + timedelta(days=7 * (week - 1))
        sunday = thursday + timedelta(days=3)
        for g in range(games):
            if g == 0:
                gameday, gametime = thursday, "20:15"
            elif g == games - 1:
                gameday, gametime = sunday + timedelta(days=1), "20:15"
            else:
                gameday, gametime = sunday, ("13:00" if g % 2 else "16:25")
            result = 3 if week <= finals_through else None
            if partial and week == partial[0] and g < partial[1]:
                result = 7
            rows.append(
                {
                    "season": season,
                    "game_type": "REG",
                    "week": week,
                    "gameday": gameday.isoformat(),
                    "gametime": gametime,
                    "result": result,
                    "home_team": f"H{g}",
                    "away_team": f"A{g}",
                    "game_id": f"{season}_{week:02d}_A{g}_H{g}",
                }
            )
    if playoffs:
        wc_saturday = week1_thursday + timedelta(days=7 * weeks + 2)
        for g, (gtype, offset) in enumerate([("WC", 0), ("WC", 1), ("DIV", 7), ("SB", 29)]):
            rows.append(
                {
                    "season": season,
                    "game_type": gtype,
                    "week": weeks + 1 + (0 if gtype == "WC" else 1 if gtype == "DIV" else 4),
                    "gameday": (wc_saturday + timedelta(days=offset)).isoformat(),
                    "gametime": "16:30",
                    "result": 3 if playoffs == "final" else None,
                    "home_team": f"P{g}",
                    "away_team": f"Q{g}",
                    "game_id": f"{season}_{gtype}_{g}",
                }
            )
    return pl.DataFrame(rows, schema=SCHEMA)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def real_2026(fixtures_dir) -> pl.DataFrame:
    return pl.read_parquet(fixtures_dir / "schedules_2026.parquet")


def test_preseason_before_week_one() -> None:
    state = resolve_state(make_schedule(), utc(2026, 8, 20, 12))
    assert state.season == 2026
    assert state.mode == "regular"
    assert state.projection_week == 1
    assert state.week_in_progress is False
    assert state.completed_weeks == ()
    assert state.last_completed_week is None
    assert state.next_kickoff_utc == utc(2026, 9, 11, 0, 15)  # Thu 20:15 EDT
    assert state.last_final_kickoff_utc is None
    assert state.games_final == 0


def test_real_week_8_partially_played(real_2026: pl.DataFrame) -> None:
    # Sunday night 2026-11-01, 12 of 14 week-8 games final, PHI@WAS (20:20 ET) and MNF pending
    now = utc(2026, 11, 1, 23, 30)
    state = resolve_state(real_2026, now)
    assert state.season == 2026
    assert state.mode == "regular"
    assert state.projection_week == 8
    assert state.week_in_progress is True
    assert state.completed_weeks == (1, 2, 3, 4, 5, 6, 7)
    assert state.last_completed_week == 7
    week8 = real_2026.filter(pl.col("week") == 8)
    assert week8.height == 14
    assert week8["result"].null_count() == 2
    assert state.next_kickoff_utc == utc(2026, 11, 2, 1, 20)  # PHI@WAS 20:20 EST
    assert state.last_final_kickoff_utc == utc(2026, 11, 1, 21, 25)  # 16:25 EST slate
    assert state.games_total == 272
    assert state.games_final == real_2026.filter(pl.col("result").is_not_null()).height
    # fixture sanity: the resolver only needs these columns
    assert set(SCHEDULE_COLUMNS) <= set(real_2026.columns)


def test_clean_tuesday_after_a_week() -> None:
    schedule = make_schedule(finals_through=8)
    state = resolve_state(schedule, utc(2026, 11, 3, 12))  # Tuesday after week 8
    assert state.mode == "regular"
    assert state.projection_week == 9
    assert state.week_in_progress is False
    assert state.completed_weeks == tuple(range(1, 9))
    assert state.last_completed_week == 8
    assert state.next_kickoff_utc == utc(2026, 11, 6, 1, 15)  # week 9 Thursday 20:15 EST
    assert state.last_final_kickoff_utc == utc(2026, 11, 3, 1, 15)  # week 8 Monday night


def test_bye_weeks_with_fewer_games() -> None:
    byes = {week: 14 for week in range(5, 15)}
    schedule = make_schedule(finals_through=6, partial=(7, 13), bye_weeks=byes)
    assert schedule.filter(pl.col("week") == 7).height == 14
    state = resolve_state(schedule, utc(2026, 10, 26, 2))
    assert state.projection_week == 7
    assert state.week_in_progress is True
    assert state.completed_weeks == (1, 2, 3, 4, 5, 6)
    assert state.last_completed_week == 6
    # once the last game of the bye week finishes the week completes
    done = schedule.with_columns(
        pl.when(pl.col("week") == 7)
        .then(pl.lit(1, dtype=pl.Int32))
        .otherwise(pl.col("result"))
        .alias("result")
    )
    state = resolve_state(done, utc(2026, 10, 27, 12))
    assert state.projection_week == 8
    assert state.week_in_progress is False
    assert state.completed_weeks == (1, 2, 3, 4, 5, 6, 7)


def test_postseason_after_week_18() -> None:
    schedule = make_schedule(finals_through=18, playoffs="pending")
    state = resolve_state(schedule, utc(2027, 1, 12, 12))
    assert state.mode == "postseason"
    assert state.projection_week is None
    assert state.week_in_progress is False
    assert state.completed_weeks == tuple(range(1, 19))
    assert state.last_completed_week == 18
    assert state.next_kickoff_utc == utc(2027, 1, 16, 21, 30)  # WC Saturday 16:30 EST
    assert state.last_final_kickoff_utc == utc(2027, 1, 12, 1, 15)  # week 18 Monday slot


def test_week_18_done_but_playoff_games_not_yet_published_is_offseason() -> None:
    schedule = make_schedule(finals_through=18)
    state = resolve_state(schedule, utc(2027, 1, 12, 12))
    assert state.mode == "offseason"
    assert state.projection_week is None
    assert state.next_kickoff_utc is None


def test_march_is_offseason_and_keeps_last_season() -> None:
    last = make_schedule(2025, finals_through=18, playoffs="final", week1_thursday=date(2025, 9, 4))
    state = resolve_state(last, utc(2026, 3, 1, 12))
    assert state.season == 2025
    assert state.mode == "offseason"
    assert state.projection_week is None
    assert state.completed_weeks == tuple(range(1, 19))
    assert state.last_completed_week == 18
    assert state.next_kickoff_utc is None
    assert state.last_final_kickoff_utc == utc(2026, 2, 8, 21, 30)  # Super Bowl row

    # the new season's schedule being published does not switch over until 45 days before
    # its first kickoff (2026-09-11T00:15Z here, so the switch happens 2026-07-28T00:15Z)
    both = pl.concat([last, make_schedule(2026)])
    assert resolve_state(both, utc(2026, 3, 1, 12)).season == 2025
    assert resolve_state(both, utc(2026, 7, 27, 12)).season == 2025
    assert resolve_state(both, utc(2026, 7, 28, 0, 14)).season == 2025
    assert resolve_state(both, utc(2026, 7, 28, 0, 15)).season == 2026
    assert resolve_state(both, utc(2026, 7, 28, 12)).projection_week == 1


def test_earliest_season_used_when_nothing_qualifies() -> None:
    state = resolve_state(make_schedule(2026), utc(2025, 1, 1))
    assert state.season == 2026
    assert state.projection_week == 1


def test_null_gametime_defaults_to_one_pm_eastern() -> None:
    schedule = make_schedule(finals_through=1).with_columns(
        pl.when(pl.col("week") == 2).then(None).otherwise(pl.col("gametime")).alias("gametime")
    )
    assert schedule.filter(pl.col("week") == 2)["gametime"].null_count() == 16
    kick = with_kickoff_utc(schedule).filter(pl.col("week") == 2)["kickoff_utc"]
    # Thursday 2026-09-17 13:00 EDT -> 17:00 UTC; Sunday and Monday rows likewise
    assert kick.min() == utc(2026, 9, 17, 17, 0)
    assert kick.max() == utc(2026, 9, 21, 17, 0)
    state = resolve_state(schedule, utc(2026, 9, 16, 12))
    assert state.projection_week == 2
    assert state.next_kickoff_utc == utc(2026, 9, 17, 17, 0)

    # the same null time after the DST change resolves to 18:00 UTC
    winter = pl.DataFrame(
        [
            {
                "season": 2026,
                "game_type": "REG",
                "week": 9,
                "gameday": "2026-11-08",
                "gametime": None,
                "result": None,
                "home_team": "H",
                "away_team": "A",
                "game_id": "2026_09_A_H",
            }
        ],
        schema=SCHEMA,
    )
    assert with_kickoff_utc(winter)["kickoff_utc"][0] == utc(2026, 11, 8, 18, 0)


def test_kickoff_accepts_date_and_time_dtypes(real_2026: pl.DataFrame) -> None:
    typed = real_2026.with_columns(
        pl.col("gameday").str.to_date("%Y-%m-%d"),
        pl.col("gametime").str.to_time("%H:%M"),
    )
    assert with_kickoff_utc(typed)["kickoff_utc"].equals(with_kickoff_utc(real_2026)["kickoff_utc"])


def test_naive_now_is_treated_as_utc(real_2026: pl.DataFrame) -> None:
    aware = resolve_state(real_2026, utc(2026, 11, 1, 23, 30))
    naive = resolve_state(real_2026, datetime(2026, 11, 1, 23, 30))
    assert naive == aware
    assert naive.as_of_utc.tzinfo is not None


def test_state_is_frozen_and_serialisable(real_2026: pl.DataFrame) -> None:
    state = resolve_state(real_2026, utc(2026, 11, 1, 23, 30))
    assert isinstance(state, SeasonState)
    with pytest.raises(AttributeError):
        state.season = 2027  # type: ignore[misc]
    payload = state.to_dict()
    json.dumps(payload)
    assert payload["completed_weeks"] == [1, 2, 3, 4, 5, 6, 7]
    assert payload["next_kickoff_utc"] == "2026-11-02T01:20:00+00:00"
    assert payload["projection_week"] == 8


def test_missing_columns_and_empty_frames_raise(real_2026: pl.DataFrame) -> None:
    with pytest.raises(ValueError, match="missing columns"):
        resolve_state(real_2026.drop("gametime"), utc(2026, 11, 1))
    with pytest.raises(ValueError):
        resolve_state(real_2026.clear(), utc(2026, 11, 1))


def test_describe_current_state_reads_latest_snapshot(tmp_settings, real_2026) -> None:
    from ffml.data.store import SnapshotStore

    assert "error" in describe_current_state()

    SnapshotStore(tmp_settings.raw_dir).write_snapshot("schedules", {2026: real_2026})
    with freeze_time("2026-11-01 23:30:00"):
        payload = describe_current_state()
    assert payload["season"] == 2026
    assert payload["projection_week"] == 8
    assert payload["week_in_progress"] is True
    assert payload["as_of_utc"] == "2026-11-01T23:30:00+00:00"
