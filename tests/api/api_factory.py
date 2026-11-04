"""Small synthetic prediction snapshots and registries for API tests (fixtures, not projections).

The snapshot layout and manifest come from :func:`ffml.sim.snapshot.write_predictions_snapshot`,
so the API reads exactly what the pipeline writes. Values are random but shaped like the
simulator output (stat-line vocabulary of ``ffml.scoring.statline``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.scoring.statline import SIM_STAT_DTYPE, STAT_COLUMNS
from ffml.sim.snapshot import ROS_SIMS_FILE, ROS_SUMMARY_FILE, write_predictions_snapshot

TEAMS = ("KC", "BUF", "MIN", "DET", "LA", "SF")
GAMES = {"KC": ("BUF", True), "BUF": ("KC", False), "MIN": ("DET", True),
         "DET": ("MIN", False), "LA": ("SF", True), "SF": ("LA", False)}
PER_TEAM = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1}


def player_ids() -> list[tuple[str, str, str]]:
    """``(entity_id, position, team)`` for every synthetic entity (DST id = team code)."""
    out: list[tuple[str, str, str]] = []
    n = 0
    for team in TEAMS:
        for pos, count in PER_TEAM.items():
            for _ in range(count):
                n += 1
                out.append((f"00-00{n:05d}", pos, team))
        out.append((team, "DST", team))
    return out


def _game_id(season: int, week: int, team: str) -> str:
    opp, home = GAMES[team]
    h, a = (team, opp) if home else (opp, team)
    return f"{season}_{week:02d}_{a}_{h}"


def _stat_draws(rng: np.random.Generator, pos: str, n: int, scale: float) -> dict[str, np.ndarray]:
    z = {c: np.zeros(n, dtype=np.int16) for c in STAT_COLUMNS}

    def pois(lam: float) -> np.ndarray:
        return rng.poisson(max(lam * scale, 0.01), n).astype(np.int16)

    if pos == "QB":
        z["pass_att"] = pois(34)
        z["pass_cmp"] = np.minimum(pois(22), z["pass_att"])
        z["pass_yds"] = pois(240)
        z["pass_td"] = pois(1.7)
        z["pass_int"] = pois(0.7)
        z["rush_att"] = pois(4)
        z["rush_yds"] = pois(18)
        z["rush_td"] = pois(0.2)
    elif pos in ("RB", "WR", "TE"):
        tg = {"RB": 4, "WR": 7, "TE": 5}[pos]
        z["targets"] = pois(tg)
        z["rec"] = np.minimum(pois(tg * 0.65), z["targets"])
        z["rec_yds"] = pois(tg * 8)
        z["rec_td"] = pois(0.3 if pos != "RB" else 0.15)
        if pos == "RB":
            z["rush_att"] = pois(14)
            z["rush_yds"] = pois(60)
            z["rush_td"] = pois(0.45)
        z["fum_lost"] = pois(0.05)
    elif pos == "K":
        z["fg_made_30_39"] = pois(0.8)
        z["fg_made_40_49"] = pois(0.6)
        z["fg_made_50_59"] = pois(0.3)
        z["fg_missed"] = pois(0.3)
        z["xp_made"] = pois(2.4)
    elif pos == "DST":
        z["sacks"] = pois(2.5)
        z["def_int"] = pois(0.8)
        z["fum_rec"] = pois(0.6)
        z["def_td"] = pois(0.1)
        z["pts_allowed"] = pois(21)
        z["yds_allowed"] = pois(330)
    return z


def sims_frame(
    season: int, week: int, n_sims: int, seed: int = 7, weeks: list[int] | None = None
) -> pl.DataFrame:
    """Simulated stat lines for every entity; ``weeks`` gives several weeks (ROS)."""
    rng = np.random.default_rng(seed)
    frames = []
    ents = player_ids()
    for w in weeks or [week]:
        for i, (eid, pos, team) in enumerate(ents):
            if weeks and team == "LA" and w == weeks[0]:
                continue  # a bye week for one team in the ROS window
            scale = 0.6 + 0.8 * ((i * 37) % 11) / 10.0
            stats = _stat_draws(rng, pos, n_sims, scale)
            active = rng.random(n_sims) < (0.97 if i % 9 else 0.6)
            for c in STAT_COLUMNS:
                stats[c] = np.where(active, stats[c], 0).astype(np.int16)
            frames.append(
                pl.DataFrame(
                    {
                        "entity_id": [eid] * n_sims,
                        "season": pl.Series([season] * n_sims, dtype=pl.Int32),
                        "week": pl.Series([w] * n_sims, dtype=pl.Int32),
                        "game_id": [_game_id(season, w, team)] * n_sims,
                        "team": [team] * n_sims,
                        "position": [pos] * n_sims,
                        "sim": pl.Series(np.arange(n_sims), dtype=pl.Int16),
                        "active": active,
                        **{c: pl.Series(stats[c], dtype=SIM_STAT_DTYPE) for c in STAT_COLUMNS},
                    }
                )
            )
    return pl.concat(frames)


def summary_frame(sims: pl.DataFrame, model: str = "chain_v2") -> pl.DataFrame:
    """A reference summary with a ``drivers`` JSON column, like ``player_summary.parquet``."""
    from ffml.scoring.rules import ScoringRules

    rows = []
    for preset in ("ppr", "half", "std"):
        rules = ScoringRules.from_preset(preset)
        scored = sims.with_columns(rules.expression(sims.columns).alias("pts"))
        agg = scored.group_by(["entity_id", "position", "team", "game_id"]).agg(
            pl.col("pts").mean().alias("pred_mean"),
            pl.col("pts").quantile(0.1).alias("pred_q10"),
            pl.col("pts").quantile(0.5).alias("pred_q50"),
            pl.col("pts").quantile(0.9).alias("pred_q90"),
            pl.col("active").mean().alias("active_prob"),
        )
        for r in agg.iter_rows(named=True):
            m = float(r["pred_mean"])
            drivers = {
                "opportunity": round(0.5 * m - 3, 2),
                "efficiency": round(0.1 * m - 0.5, 2),
                "matchup": round(((sum(map(ord, r["entity_id"])) % 7) - 3) / 3, 2),
                "environment": round(0.05 * m, 2),
                "availability": round((r["active_prob"] - 1) * m, 2),
                "reference": round(0.4 * m, 2),
                "method": "sequential-substitution-v1",
            }
            rows.append(
                {
                    **r,
                    "season": int(sims["season"][0]),
                    "week": int(sims["week"][0]),
                    "scoring": preset,
                    "model": model,
                    "boom_prob": 0.1,
                    "bust_prob": 0.3,
                    "drivers": json.dumps(drivers),
                }
            )
    return pl.DataFrame(rows)


def write_snapshot(
    root: Path,
    *,
    season: int = 2024,
    week: int = 10,
    run_id: str | None = None,
    model_version: str = "chain_v2-test",
    labels: Mapping[str, Any] | None = None,
    mode: str = "live",
    n_sims: int = 200,
    ros: bool = True,
    update_latest: bool = True,
) -> Path:
    """Write one run under ``root`` (and ``root/LATEST`` unless ``update_latest=False``)."""
    run_id = run_id or f"{season}w{week:02d}-test{'-replay' if mode == 'replay' else ''}"
    sims = sims_frame(season, week, n_sims)
    summary = summary_frame(sims)
    extra: dict[str, pl.DataFrame] = {}
    if ros and week < 18:
        weeks = list(range(week + 1, 19))
        ros_sims = sims_frame(season, week, max(n_sims // 4, 20), seed=11, weeks=weeks)
        extra = {ROS_SIMS_FILE: ros_sims, ROS_SUMMARY_FILE: pl.DataFrame({"entity_id": ["x"]})}
    meta: dict[str, Any] = {
        "model_version": model_version,
        "seeds": {"week": 1},
        "n_sims": n_sims,
        "inputs": {"test": True},
        "mode": mode,
        **(labels or {}),
    }
    return write_predictions_snapshot(
        season,
        week,
        run_id,
        sims,
        summary,
        None,
        meta,
        root=root,
        update_latest=update_latest,
        extra_frames=extra,
    )


def write_registry(artifacts: Path, entries: list[dict[str, Any]]) -> Path:
    path = artifacts / "models" / "REGISTRY.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"format": "ffml.registry/1", "models": entries}, indent=2), encoding="utf-8"
    )
    return path


def live_entry(version: str = "chain_v2-test", status: str = "live") -> dict[str, Any]:
    return {
        "name": "chain_v2",
        "version": version,
        "path": f"artifacts/models/chain/{version}",
        "status": status,
        "created_at": "2026-11-10T00:00:00+00:00",
        "git": "abc1234",
        "backtest_run_id": None,
        "evaluator_report": "reports/evaluator_release_test.md" if status != "candidate" else None,
        "history": [],
    }


APPROVED_LABELS = {"environment": "production", "approval_status": "approved", "label": "approved"}
PRIVATE_LABELS = {
    "environment": "private_test",
    "approval_status": "not approved",
    "label": "not approved",
}
