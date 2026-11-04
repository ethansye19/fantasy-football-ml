"""Projection service (BE-4.3): league scoring applied to simulated stat lines at request time.

For the run chosen by the approval gate, ``player_sims`` (and ``ros_sims``
when present) are loaded once and kept in memory. For each league scoring
(:class:`ffml.scoring.rules.ScoringRules`, D-003) the points of every
simulation are computed with one vectorized expression and cached per
``(run, scoring hash)`` as dense matrices (entity x simulation: float64 for the
projection week, so ties rank exactly as in the chain; float32 for rest of season).

Summary conventions match ``ffml.models.chain.summary`` (D-017, D-067):
positional ranks per simulation are computed over every draw (inactive draws
score 0; ``min`` rank for boom, ``max`` rank for bust); floor (q10), median
(q50), ceiling (q90), mean, boom and bust are taken over the simulations in
which the player is active; ``active_prob`` is the share of active
simulations and ``expected`` the unconditional mean, which is what lineup
values use. Players with fewer than 100 active simulations are flagged
``low_sample`` (the chain's forced-active pass is not stored in snapshots);
below 20 their conditional columns are null.

Quantiles are raw simulation quantiles under the league scoring; the chain's
frozen calibration is fitted per reference scoring and is not applied here
(see the backend status note, proposal P-2).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import rankdata

from api.approval import PredictionSource
from ffml.scoring.rules import ScoringRules
from ffml.sim.summarize import BOOM_BUST_THRESHOLDS

log = logging.getLogger(__name__)

MIN_ACTIVE_SIMS = 100
MIN_REPORT_SIMS = 20
LOW_CONFIDENCE_BOOM_BUST = frozenset({"K", "DST"})  # D-089
PRESETS = ("ppr", "half", "std")
LAST_REG_WEEK = 18


@dataclass
class RunData:
    """One prediction run loaded for scoring."""

    key: str
    season: int
    week: int
    entities: list[str]
    index: dict[str, int]
    positions: list[str]
    teams: list[str | None]
    game_ids: list[str | None]
    opponents: list[str | None]
    sims: pl.DataFrame  # sorted (entity order, sim); stat columns only
    active: np.ndarray  # bool (n_entities, n_sims); False where the entity has no week row
    in_week: np.ndarray  # bool (n_entities,): has a projection-week row
    n_sims: int
    ros_sims: pl.DataFrame | None = None  # with _e (entity index) and _w (week index) columns
    ros_weeks: list[int] = field(default_factory=list)
    n_ros: int = 0
    drivers: dict[str, dict[str, Any]] = field(default_factory=dict)  # preset -> entity -> raw


@dataclass
class Scored:
    """A run scored under one league scoring."""

    run: RunData
    scoring_hash: str
    preset_like: str
    points: np.ndarray  # float64 (n_entities, n_sims), inactive draws 0 (exact ties)
    table: pl.DataFrame  # one row per projection-week entity
    weekly: dict[int, dict[str, float]]  # week -> entity -> expected points
    ros_points: np.ndarray | None  # float32 (n_entities, n_weeks, n_ros) or None

    _ros_tables: dict[bool, pl.DataFrame] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def row(self, entity: str) -> dict[str, Any] | None:
        i = self.run.index.get(entity)
        if i is None or not self.run.in_week[i]:
            return None
        rows = self.table.filter(pl.col("player_id") == entity).to_dicts()
        return rows[0] if rows else None

    def weeks(self, include_current: bool = True) -> list[int]:
        out = list(self.run.ros_weeks)
        if include_current:
            out = [self.run.week, *[w for w in out if w != self.run.week]]
        return out

    def ros_table(self, include_current: bool = True) -> pl.DataFrame:
        """Rest-of-season totals per entity (weeks after the run week, plus it if asked)."""
        with self._lock:
            if include_current not in self._ros_tables:
                self._ros_tables[include_current] = self._build_ros(include_current)
            return self._ros_tables[include_current]

    def _build_ros(self, include_current: bool) -> pl.DataFrame:
        run = self.run
        n = len(run.entities)
        if self.ros_points is not None and run.n_ros:
            totals = self.ros_points.sum(axis=1).astype(np.float64)  # (n, n_ros)
            k = run.n_ros
        else:
            k = run.n_sims
            totals = np.zeros((n, k))
        if include_current:
            k_use = min(k, run.n_sims)
            totals = totals[:, :k_use] + self.points[:, :k_use].astype(np.float64)
        weeks = self.weeks(include_current)
        games = np.zeros(n, dtype=np.int32)
        byes: list[list[int]] = [[] for _ in range(n)]
        for w in weeks:
            vals = self.weekly.get(w, {})
            for i, e in enumerate(run.entities):
                if e in vals:
                    games[i] += 1
                else:
                    byes[i].append(w)
        q = np.quantile(totals, [0.1, 0.5, 0.9], axis=1)
        return pl.DataFrame(
            {
                "player_id": run.entities,
                "position": run.positions,
                "team": run.teams,
                "ros_mean": totals.mean(axis=1),
                "ros_floor": q[0],
                "ros_median": q[1],
                "ros_ceiling": q[2],
                "games_remaining": games,
                "bye_weeks": byes,
            },
            schema_overrides={"bye_weeks": pl.List(pl.Int32)},
        ).with_columns(
            pl.lit(min(weeks) if weeks else run.week).alias("first_week"),
            pl.lit(max(weeks) if weeks else run.week).alias("last_week"),
        )


def _opponent(team: str | None, game_id: str | None) -> str | None:
    if not team or not game_id:
        return None
    parts = str(game_id).split("_")
    if len(parts) != 4:
        return None
    away, home = parts[2], parts[3]
    return home if team == away else away if team == home else None


def preset_like(rules: ScoringRules) -> str:
    rec = rules.weights.get("rec", 0.0)
    return "ppr" if rec >= 0.75 else "half" if rec >= 0.25 else "std"


def load_run(source: PredictionSource) -> RunData:
    """Read the run files of ``source`` into the in-memory layout."""
    path = source.path
    sims = pl.read_parquet(path / "player_sims.parquet")
    sims = sims.filter(pl.col("week") == source.week) if "week" in sims.columns else sims
    ros_path = path / "ros_sims.parquet"
    ros = pl.read_parquet(ros_path) if ros_path.is_file() else None

    key_cols = ["entity_id", "position", "team", "game_id"]
    week_ents = (
        sims.group_by("entity_id", maintain_order=True)
        .agg([pl.col(c).first() for c in key_cols[1:] if c in sims.columns])
        .sort("entity_id")
    )
    frames = [week_ents.with_columns(pl.lit(True).alias("_in_week"))]
    if ros is not None and ros.height:
        ros_ents = (
            ros.group_by("entity_id", maintain_order=True)
            .agg(pl.col("position").first(), pl.col("team").first())
            .filter(~pl.col("entity_id").is_in(week_ents["entity_id"].implode()))
            .with_columns(pl.lit(None, dtype=pl.String).alias("game_id"), pl.lit(False).alias("_in_week"))
        )
        frames.append(ros_ents.select(frames[0].columns))
    ents = pl.concat(frames, how="vertical_relaxed")
    entities = ents["entity_id"].to_list()
    index = {e: i for i, e in enumerate(entities)}
    n = len(entities)

    n_sims = int(sims["sim"].max()) + 1 if sims.height else 0
    e_idx = sims["entity_id"].replace_strict(index, return_dtype=pl.Int32).to_numpy()
    s_idx = sims["sim"].cast(pl.Int32).to_numpy()
    active = np.zeros((n, n_sims), dtype=bool)
    active[e_idx, s_idx] = sims["active"].to_numpy() if "active" in sims.columns else True
    sims = sims.with_columns(pl.Series("_e", e_idx), pl.Series("_s", s_idx))

    ros_weeks: list[int] = []
    n_ros = 0
    if ros is not None and ros.height:
        ros_weeks = sorted(int(w) for w in ros["week"].unique().to_list() if w != source.week)
        ros = ros.filter(pl.col("week").is_in(ros_weeks))
        w_index = {w: i for i, w in enumerate(ros_weeks)}
        n_ros = int(ros["sim"].max()) + 1
        ros = ros.with_columns(
            pl.col("entity_id").replace_strict(index, return_dtype=pl.Int32).alias("_e"),
            pl.col("week").replace_strict(w_index, return_dtype=pl.Int32).alias("_w"),
            pl.col("sim").cast(pl.Int32).alias("_s"),
        )

    opponents = {}
    team_env = path / "team_env.parquet"
    if team_env.is_file():
        te = pl.read_parquet(team_env)
        if {"team", "opponent", "game_id"} <= set(te.columns):
            opponents = {(r["team"], r["game_id"]): r["opponent"] for r in te.iter_rows(named=True)}
    teams = ents["team"].to_list()
    game_ids = ents["game_id"].to_list()
    opps = [
        opponents.get((t, g)) or _opponent(t, g) for t, g in zip(teams, game_ids, strict=True)
    ]

    drivers: dict[str, dict[str, Any]] = {}
    summary_path = path / "player_summary.parquet"
    if summary_path.is_file():
        summary = pl.read_parquet(summary_path)
        if {"entity_id", "scoring", "drivers"} <= set(summary.columns):
            for preset in PRESETS:
                part = summary.filter(pl.col("scoring") == preset)
                drivers[preset] = dict(
                    zip(part["entity_id"].to_list(), part["drivers"].to_list(), strict=True)
                )

    return RunData(
        key=source.key,
        season=source.season,
        week=source.week,
        entities=entities,
        index=index,
        positions=ents["position"].to_list(),
        teams=teams,
        game_ids=game_ids,
        opponents=opps,
        sims=sims,
        active=active,
        in_week=ents["_in_week"].to_numpy(),
        n_sims=n_sims,
        ros_sims=ros,
        ros_weeks=ros_weeks,
        n_ros=n_ros,
        drivers=drivers,
    )


def _dense(
    frame: pl.DataFrame,
    points: np.ndarray,
    shape: tuple[int, ...],
    idx: list[str],
    dtype: type = np.float64,
) -> np.ndarray:
    out = np.zeros(shape, dtype=dtype)
    out[tuple(frame[c].to_numpy() for c in idx)] = points
    return out


def score_run(run: RunData, rules: ScoringRules) -> Scored:
    """Score every simulation of ``run`` under ``rules`` (see the module docstring)."""
    n, s = len(run.entities), run.n_sims
    pts = run.sims.select(rules.expression(run.sims.columns).alias("p"))["p"].to_numpy()
    points = _dense(run.sims, pts, (n, s), ["_e", "_s"])
    act = run.active

    boom = np.full(n, np.nan)
    bust = np.full(n, np.nan)
    pos_arr = np.array(run.positions, dtype=object)
    for pos, (top, outside) in BOOM_BUST_THRESHOLDS.items():
        rows = np.where((pos_arr == pos) & run.in_week)[0]
        if rows.size == 0:
            continue
        block = -points[rows]
        rk_min = rankdata(block, method="min", axis=0)
        rk_max = rankdata(block, method="max", axis=0)
        a = act[rows]
        cnt = np.maximum(a.sum(axis=1), 1)
        boom[rows] = ((rk_min <= top) & a).sum(axis=1) / cnt
        bust[rows] = ((rk_max > outside) & a).sum(axis=1) / cnt

    n_active = act.sum(axis=1)
    masked = np.where(act, points, np.nan).astype(np.float64)
    ok = n_active >= MIN_REPORT_SIMS
    q = np.full((3, n), np.nan)
    mean = np.full(n, np.nan)
    if ok.any():
        q[:, ok] = np.nanquantile(masked[ok], [0.1, 0.5, 0.9], axis=1)
        mean[ok] = np.nanmean(masked[ok], axis=1)
    boom = np.where(ok, boom, np.nan)
    bust = np.where(ok, bust, np.nan)
    expected = points.mean(axis=1).astype(np.float64) if s else np.zeros(n)
    active_prob = n_active / max(s, 1)

    def _clean(x: np.ndarray) -> list[float | None]:
        return [None if v != v else round(float(v), 2) for v in x]

    def _prob(x: np.ndarray) -> list[float | None]:
        return [None if v != v else round(float(v), 4) for v in x]

    table = pl.DataFrame(
        {
            "player_id": run.entities,
            "position": run.positions,
            "team": run.teams,
            "opponent": run.opponents,
            "game_id": run.game_ids,
            "floor": _clean(q[0]),
            "median": _clean(q[1]),
            "ceiling": _clean(q[2]),
            "mean": _clean(mean),
            "expected": _clean(expected),
            "boom_prob": _prob(boom),
            "bust_prob": _prob(bust),
            "active_prob": _prob(active_prob),
            "n_active": n_active.astype(np.int32),
            "_in_week": run.in_week,
        },
        schema_overrides={
            "floor": pl.Float64, "median": pl.Float64, "ceiling": pl.Float64,
            "mean": pl.Float64, "expected": pl.Float64, "boom_prob": pl.Float64,
            "bust_prob": pl.Float64, "active_prob": pl.Float64,
        },
    ).filter(pl.col("_in_week")).drop("_in_week")
    table = table.with_columns(
        pl.col("median")
        .rank(method="ordinal", descending=True)
        .over("position")
        .cast(pl.Int32)
        .alias("position_rank"),
        (pl.col("n_active") < MIN_ACTIVE_SIMS).alias("low_sample"),
        pl.when(pl.col("position").is_in(list(LOW_CONFIDENCE_BOOM_BUST)))
        .then(pl.lit("low"))
        .otherwise(pl.lit("normal"))
        .alias("boom_bust_confidence"),
    )

    weekly: dict[int, dict[str, float]] = {
        run.week: {
            e: float(expected[i]) for i, e in enumerate(run.entities) if run.in_week[i]
        }
    }
    ros_points = None
    if run.ros_sims is not None and run.ros_weeks:
        r = run.ros_sims
        rp = r.select(rules.expression(r.columns).alias("p"))["p"].to_numpy()
        ros_points = _dense(
            r, rp, (n, len(run.ros_weeks), run.n_ros), ["_e", "_w", "_s"], np.float32
        )
        present = np.zeros((n, len(run.ros_weeks)), dtype=bool)
        present[r["_e"].to_numpy(), r["_w"].to_numpy()] = True
        means = ros_points.mean(axis=2)
        for wi, w in enumerate(run.ros_weeks):
            weekly[w] = {
                e: float(means[i, wi]) for i, e in enumerate(run.entities) if present[i, wi]
            }
    return Scored(
        run=run,
        scoring_hash=rules.scoring_hash,
        preset_like=preset_like(rules),
        points=points,
        table=table,
        weekly=weekly,
        ros_points=ros_points,
    )


class ProjectionService:
    """Caches loaded runs (one at a time) and scored runs per ``(run, scoring hash)``."""

    def __init__(self, max_scored: int = 8):
        self.max_scored = max(1, int(max_scored))
        self._lock = threading.Lock()
        self._run: RunData | None = None
        self._scored: OrderedDict[tuple[str, str], Scored] = OrderedDict()
        self._building: dict[Any, threading.Lock] = {}

    def _key_lock(self, key: Any) -> threading.Lock:
        with self._lock:
            return self._building.setdefault(key, threading.Lock())

    def run(self, source: PredictionSource) -> RunData:
        with self._key_lock(("run", source.key)):
            with self._lock:
                if self._run is not None and self._run.key == source.key:
                    return self._run
            data = load_run(source)
            with self._lock:
                if self._run is None or self._run.key != source.key:
                    self._scored.clear()  # entries of the previous run
                self._run = data
            return data

    def scored(self, source: PredictionSource, rules: ScoringRules) -> Scored:
        key = (source.key, rules.scoring_hash)
        with self._lock:
            hit = self._scored.get(key)
            if hit is not None:
                self._scored.move_to_end(key)
                return hit
        with self._key_lock(key):
            with self._lock:
                hit = self._scored.get(key)
                if hit is not None:
                    return hit
            result = score_run(self.run(source), rules)
            with self._lock:
                self._scored[key] = result
                while len(self._scored) > self.max_scored:
                    self._scored.popitem(last=False)
            return result

    def clear(self) -> None:
        with self._lock:
            self._run = None
            self._scored.clear()


def drivers_raw(run: RunData, entity: str, preset: str) -> Any:
    return run.drivers.get(preset, {}).get(entity)


def to_json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


__all__ = [
    "LOW_CONFIDENCE_BOOM_BUST",
    "ProjectionService",
    "RunData",
    "Scored",
    "drivers_raw",
    "load_run",
    "preset_like",
    "score_run",
]
