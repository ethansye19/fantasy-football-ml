"""Scoring engine vs. Sleeper-computed points for the test league, 2026 week 8.

Runs only when ``data/processed`` exists. The fixture was captured on
2026-11-02 01:43 UTC, so only players whose game kicked off before
2026-11-02 00:00 UTC (final at capture) are compared.

Mismatch classes found (documented in docs/status/phase3-ml-engineer-a.md):

- Offense, stat-source disagreement: one WR (Jalen Coker) is 2.0 points
  higher here than on Sleeper; nflverse records 2 catches, 36 yards, 1 TD and
  no lost fumble, so the two sources disagree on one stat (a 20-yard gain or
  a lost fumble), not on the rules.
- DST, forced fumbles: before D-055 the league's ``ff`` and ``def_st_ff``
  keys (1 point each) could not be computed, and all four DST mismatches
  (CIN -3, PIT -2, KC -1, LAR -1) equalled the defense's forced fumbles. With
  the combined ``forced_fumbles`` column scored by the mean of the two keys
  every final DST matches (7/7 on 2026-11-03), so the DST rate is gated too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import polars as pl
import pytest

from ffml.config import get_settings
from ffml.data.store import SnapshotStore
from ffml.scoring import ScoringRules
from ffml.scoring.actuals import actual_statlines

CUTOFF = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
TOLERANCE = 0.01

pytestmark = pytest.mark.slow


def _processed_available() -> bool:
    root = get_settings().processed_dir
    return all(
        (root / t / "LATEST").exists()
        for t in ("fact_player_game", "fact_dst_game", "dim_player", "dim_game", "dim_team")
    )


@pytest.mark.skipif(not _processed_available(), reason="data/processed not built")
def test_week8_points_match_sleeper(fixtures_dir):
    league = json.loads((fixtures_dir / "sleeper" / "league.json").read_text(encoding="utf-8"))
    matchups = json.loads(
        (fixtures_dir / "sleeper" / "matchups_week8.json").read_text(encoding="utf-8")
    )
    rules = ScoringRules.from_sleeper(league["scoring_settings"], name="test_league")
    sleeper_points: dict[str, float] = {}
    for entry in matchups:
        sleeper_points.update(entry.get("players_points") or {})

    store = SnapshotStore(get_settings().processed_dir)
    actual = actual_statlines([2026], "forward_check", store=store).filter(pl.col("week") == 8)
    actual = rules.with_points(actual)

    players = store.read("dim_player", columns=["player_id", "sleeper_id", "full_name"])
    alias_rows = store.read("dim_team", columns=["team", "aliases"]).explode("aliases")
    team_alias = dict(zip(alias_rows["aliases"], alias_rows["team"], strict=True))

    sl = pl.DataFrame(
        {"sleeper_id": list(sleeper_points), "sleeper_points": list(sleeper_points.values())}
    )
    is_team = pl.col("sleeper_id").str.contains(r"^[A-Z]+$")
    sl = sl.with_columns(
        pl.when(is_team)
        .then(pl.col("sleeper_id").replace(team_alias))
        .otherwise(None)
        .alias("team_code")
    )
    offense = (
        sl.filter(~is_team)
        .join(players, on="sleeper_id", how="left")
        .join(
            actual.filter(pl.col("position") != "DST"),
            left_on="player_id",
            right_on="entity_id",
            how="inner",
        )
    )
    dst = sl.filter(is_team).join(
        actual.filter(pl.col("position") == "DST"),
        left_on="team_code",
        right_on="entity_id",
        how="inner",
    )
    offense = offense.filter(pl.col("kickoff_utc") < CUTOFF)
    dst = dst.filter(pl.col("kickoff_utc") < CUTOFF)

    def report(frame: pl.DataFrame, label: str) -> float:
        frame = frame.with_columns(
            (pl.col("points") - pl.col("sleeper_points")).abs().alias("diff")
        )
        n = frame.height
        hits = int((frame["diff"] <= TOLERANCE).sum())
        print(f"{label}: {hits}/{n} within {TOLERANCE} ({hits / n:.1%})")
        for row in frame.filter(pl.col("diff") > TOLERANCE).iter_rows(named=True):
            print(
                f"  mismatch {row.get('full_name') or row['sleeper_id']} {row['position']}: "
                f"engine {row['points']:.2f} sleeper {row['sleeper_points']:.2f}"
            )
        return hits / n

    assert offense.height >= 60, "too few final offense players to compare"
    offense_rate = report(offense, "offense (QB/RB/WR/TE/K)")
    dst_rate = report(dst, "DST")
    assert offense_rate >= 0.95
    # D-055: forced fumbles close the only DST definition gap (7/7 when written); a
    # small allowance remains for later stat corrections on either side.
    assert dst.height >= 5
    assert dst_rate >= 0.85
