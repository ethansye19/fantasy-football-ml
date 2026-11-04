"""Live prediction pipeline (ML-3.4): ``ffml predict`` -> :func:`run_predict`.

Modes (approval gate, D-097 to D-099)
-------------------------------------
- **Live** (default). Only the registry's live version
  (:func:`ffml.models.registry.get_live`); ``model_dir``/``model`` overrides
  are refused and there is no newest-artifact fallback. Without a live
  version the run raises :class:`NoApprovedModel` before reading anything
  (no access-log line, no files). Manifest: ``environment="live"``,
  ``approval_status="approved"``; the public ``LATEST`` moves.
- **Private test** (``private_test=True, model_version=<candidate>``). The
  version must be a registry ``candidate`` (``model_dir`` only when it is that
  entry's path). Writes only under ``Settings.private_predictions_dir``
  (private ``LATEST`` there), never the public store. Manifest:
  ``environment="private_test"``, ``approval_status="not approved"``,
  ``label="not approved"``; ``player_summary`` and ``ros_summary`` carry an
  ``approval_status`` column.
- **Replay** (``replay=True``). As before (any model, never moves ``LATEST``);
  manifest ``environment="replay"`` and ``approval_status="not approved"``
  unless the model is the live approved version.

Refused requests raise :class:`PredictionRefused` before anything is read.

Steps
-----
1. **Week.** Without ``season``/``week`` the state comes from
   :func:`ffml.season.current_state` (schedule snapshot). Regular season:
   the projection week; in a week in progress only the games that are not
   final are predicted. Postseason or offseason: a clear message, nothing is
   written, ``None`` is returned.
2. **Holdout.** Live features for season ``s`` read processed history for
   2016..s. Season 2026 needs 2025, which stays locked until the Independent
   Evaluator releases it (D-058, D-098): the run stops before reading
   anything, with :class:`LivePredictionBlocked` and instructions.
3. **Model.** Live: the registry's live version. Private test: the named
   candidate. Replay: ``model_dir``, else the registry's live version, else the
   newest ``artifacts/models/chain/*``. The manifest records which
   (``model_source``).
4. **Rows.** Live: :func:`ffml.features.targets.future_targets` with
   ``asof_ts = now`` through the feature builders (the same code that built
   the training rows). Replay (``replay=True``, for testing): the stored
   historical feature rows of the week with every ``y_`` column blanked, and a
   model whose cutoff is at or before the week (otherwise refused).
5. **Availability** (``availability_config = "final_report"``, D-065): the
   chain's primary P(active) capped by the FEATURES.md section G rules
   (report, practice, roster, depth chart), plus, live only, the game-day
   roster status ``INA`` (cap 0) and the Sleeper ``injury_status`` from the
   latest ``sleeper_players`` table as a tie-breaker for players without an
   nflverse report (Out, IR, PUP, Sus, COV: 0; Doubtful 0.15; Questionable the
   position rate). The Sleeper map is used only when its snapshot date falls
   between 7 days before the week's first kickoff and its last kickoff.
6. **Projection week.** ``n_sims`` draws; snapshot files ``player_sims``,
   ``player_summary`` (protocol frame with drivers for the ``ppr``, ``half``
   and ``std`` presets) and ``team_env``.
7. **Rest of season** (``rest_of_season=True``, weeks w+1..18): see
   :mod:`ffml.sim.ros`; ``ros_sims.parquet`` (``ros_sims`` draws per entity
   and week, Int16) and ``ros_summary.parquet``.
8. **Snapshot.** :func:`ffml.sim.snapshot.write_predictions_snapshot` with a
   manifest carrying model_version, environment, approval_status (and label),
   run_id, seeds, inputs, model_source and availability_config. Live runs move
   the public ``LATEST``, private tests the private one; replays move none.
"""

from __future__ import annotations

import glob
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.config import PROJECT_ROOT, get_settings
from ffml.holdout import HoldoutLockedError, assert_seasons_allowed
from ffml.scoring.rules import ScoringRules
from ffml.sim.ros import LAST_REG_WEEK, StrengthModel, simulate_ros, strength_game_lines
from ffml.sim.snapshot import ROS_SIMS_FILE, ROS_SUMMARY_FILE, write_predictions_snapshot

log = logging.getLogger(__name__)

PRESETS: tuple[str, ...] = ("ppr", "half", "std")
AVAILABILITY_CONFIG = "final_report"
SLEEPER_CAPS: dict[str, float] = {
    "Out": 0.0,
    "IR": 0.0,
    "PUP": 0.0,
    "Sus": 0.0,
    "COV": 0.0,
    "Doubtful": 0.15,
}
SLEEPER_WINDOW_DAYS = 7
LINE_COLS = ("ctx__spread", "ctx__total_line", "ctx__implied_team_total", "ctx__implied_opp_total")
HEALTHY_RESET = {
    "attr__games_missed__l5": 0.0,
    "attr__weeks_since_last_game": 1.0,
    "attr__inj_prev_status": 0.0,
    "attr__inj_weeks_since_out": 52.0,
}


APPROVED = "approved"
NOT_APPROVED = "not approved"
ENVIRONMENTS: tuple[str, ...] = ("live", "private_test", "replay")
NO_APPROVED_MODEL_MESSAGE = (
    "no approved model: live projections are off until a model passes its "
    "pre-registered test (D-097, D-099)"
)


class LivePredictionBlocked(HoldoutLockedError):
    """A live run cannot start: the holdout lock (D-058) or no approved model (D-099)."""


class NoApprovedModel(LivePredictionBlocked):
    """The registry has no live, approved version; live projections are off (D-097, D-099)."""


class PredictionRefused(ValueError):
    """The request asks for a model or option that its mode does not allow; nothing was written."""


@dataclass
class PredictResult:
    path: Path
    run_id: str
    season: int
    week: int
    mode: str
    model_source: dict[str, Any]
    timings: dict[str, float] = field(default_factory=dict)
    rows: int = 0
    ros_weeks: list[int] = field(default_factory=list)
    skipped_final_games: list[str] = field(default_factory=list)
    environment: str = "live"
    approval_status: str = NOT_APPROVED


def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def blocked_message(season: int) -> str:
    holdout = get_settings().holdout_season
    return (
        f"Live predictions for {season} read processed history for seasons "
        f"{get_settings().first_season}-{season}, which includes {holdout}, the final "
        f"holdout. It stays locked until the Independent Evaluator releases it with "
        f"ffml.holdout.release_holdout() (D-058, D-098); a release is not an approval. Nothing "
        f"was read or written. Until then: run a replay of a past week, e.g. "
        f"run_predict(season=2024, week=10, replay=True, model_dir=<a chain trained at or "
        f"before cutoff (2024, 10)>), or ask the evaluator to finish the holdout session. "
        f"Do not set FFML_HOLDOUT_UNLOCK for this."
    )


# --------------------------------------------------------------------------- #
# Model resolution
# --------------------------------------------------------------------------- #


def resolve_model_dir(
    model_dir: Path | str | None = None, chain_root: Path | str | None = None
) -> tuple[Path, dict[str, Any]]:
    """Replay only: ``model_dir``, else the registry's live version, else the newest artifact.

    Live and private-test runs never use this; they take their model from the
    registry (:func:`live_model_entry`, :func:`private_test_entry`).
    """
    if model_dir is not None:
        return Path(model_dir), {"kind": "model_dir", "path": str(model_dir)}
    from ffml.models.registry import get_live

    live = get_live()
    if live is not None:
        p = Path(live["path"])
        p = p if p.is_absolute() else PROJECT_ROOT / p
        return p, {"kind": "registry_live", "version": live["version"], "path": str(p)}
    root = Path(chain_root) if chain_root is not None else get_settings().models_dir / "chain"
    dirs = [Path(d) for d in glob.glob(str(root / "*")) if (Path(d) / "params.json").exists()]
    if not dirs:
        raise FileNotFoundError(
            f"no trained chain under {root} and no live registry version; run `ffml train`"
        )
    newest = max(dirs, key=lambda d: (d / "params.json").stat().st_mtime)
    return newest, {"kind": "newest_artifact", "path": str(newest)}


def entry_path(entry: dict[str, Any]) -> Path:
    """A registry entry's artifact directory (relative paths are under the project root)."""
    p = Path(entry["path"])
    return p if p.is_absolute() else PROJECT_ROOT / p


def _same_path(a: Path | str, b: Path | str) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def live_model_entry() -> dict[str, Any]:
    """The registry's live version, or :class:`NoApprovedModel` (reads only the registry).

    A live entry without an evaluator report (possible only by editing the
    registry by hand; ``set_live`` refuses it) counts as no approved model.
    """
    from ffml.models.registry import get_live

    live = get_live()
    if live is None or not live.get("evaluator_report"):
        raise NoApprovedModel(NO_APPROVED_MODEL_MESSAGE)
    return live


def private_test_entry(model_version: str | None) -> dict[str, Any]:
    """The registry candidate for a private test; any other status is refused."""
    from ffml.models import registry

    if not model_version:
        raise PredictionRefused("a private test needs model_version, a registry candidate")
    try:
        entry = registry.get(model_version)
    except registry.RegistryError as exc:
        raise PredictionRefused(f"private test refused: {exc}") from None
    status = entry["status"]
    if status in ("approved", "live"):
        raise PredictionRefused(
            f"private test refused: {model_version!r} is {status}; approved models run only "
            f"in live mode, as the registry's live version"
        )
    if status != "candidate":
        raise PredictionRefused(
            f"private test refused: only candidates can be tested; {model_version!r} is {status}"
        )
    return entry


def _gate(
    environment: str,
    *,
    model_dir: Path | str | None,
    model: Any,
    chain_root: Path | str | None,
    model_version: str | None,
    root: Path | str | None,
    update_latest: bool | None,
) -> dict[str, Any] | None:
    """Mode checks before anything is read; the registry entry for live and private runs."""
    if environment == "replay":
        if model_version is not None:
            raise PredictionRefused("model_version is for private tests; a replay takes model_dir")
        if update_latest:
            raise PredictionRefused("replays never move LATEST")
        return None
    overrides = {"model": model, "chain_root": chain_root}
    if environment == "live":
        overrides.update(model_dir=model_dir, model_version=model_version)
    given = [k for k, v in overrides.items() if v is not None]
    if given:
        raise PredictionRefused(
            f"{environment} mode refuses {', '.join(given)}: "
            + (
                "live runs use only the registry's live, approved version"
                if environment == "live"
                else "a private test loads the registry candidate named by model_version"
            )
        )
    if environment == "live":
        return live_model_entry()
    private = get_settings().private_predictions_dir
    if root is not None and not _same_path(root, private):
        raise PredictionRefused(
            f"a private test writes only to Settings.private_predictions_dir ({private})"
        )
    entry = private_test_entry(model_version)
    if model_dir is not None and not _same_path(model_dir, entry_path(entry)):
        raise PredictionRefused(
            f"model_dir {model_dir} is not the registry path of {model_version!r} "
            f"({entry_path(entry)})"
        )
    return entry


def replay_approval_status(source: dict[str, Any]) -> str:
    """``approved`` only when the replayed artifact is the registry's live version."""
    from ffml.models.registry import get_live

    if source.get("kind") == "object" or not source.get("path"):
        return NOT_APPROVED
    live = get_live()
    if live is not None and _same_path(source["path"], entry_path(live)):
        return APPROVED
    return NOT_APPROVED


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


def _blind(frame: pl.DataFrame) -> pl.DataFrame:
    """Null every target and target-derived column (replay never uses actuals)."""
    cols = [c for c in frame.columns if c.startswith(("y_", "tm_"))]
    return frame.with_columns([pl.lit(None, dtype=frame.schema[c]).alias(c) for c in cols])


def _reg(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.filter(pl.col("game_type") == "REG") if "game_type" in frame.columns else frame


def live_rows(
    model: Any, ctx: Any, season: int, week: int, now: datetime
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Feature rows for the week's games that are not final, built as of ``now``."""
    from ffml.features.build import build_dst_week, build_player_week, build_team_week
    from ffml.features.targets import future_targets

    targets = future_targets(season, week, ctx, now=now, include_final=False)
    variant = (
        "same_week" if model.player_features_dataset.endswith("depth_same_week") else "primary"
    )
    built = {
        "team_week": build_team_week(ctx, targets["team_week"]),
        "player_week": build_player_week(ctx, targets["player_week"], depth_variant=variant),
        "dst_week": build_dst_week(ctx, targets["dst_week"]),
    }
    return tuple(
        _reg(model.data.prepare(n, built[n])) for n in ("team_week", "player_week", "dst_week")
    )  # type: ignore[return-value]


def replay_rows(
    model: Any, season: int, week: int
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    return tuple(
        _blind(model.data.week_rows(n, season, week))
        for n in ("team_week", "player_week", "dst_week")
    )  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Availability (final-report configuration, live extras)
# --------------------------------------------------------------------------- #


def sleeper_statuses(
    ctx: Any, season: int, week: int, store: Any = None
) -> tuple[pl.DataFrame | None, dict[str, Any]]:
    """``player_id, sleeper_injury_status`` from the latest Sleeper map, if fresh for the week."""
    try:
        from ffml.data.store import SnapshotStore

        store = store or SnapshotStore(get_settings().processed_dir)
        sp = store.read("sleeper_players", columns=["gsis_id", "injury_status", "snapshot_date"])
    except Exception as exc:  # noqa: BLE001 - the tie-breaker is optional
        return None, {"used": False, "reason": f"unavailable ({type(exc).__name__})"}
    if sp.height == 0:
        return None, {"used": False, "reason": "empty"}
    snap = sp["snapshot_date"].max()
    games = ctx.dim_game.filter((pl.col("season") == season) & (pl.col("week") == week))
    first, last = games["kickoff_utc"].min(), games["kickoff_utc"].max()
    if first is None or snap is None:
        return None, {"used": False, "reason": "no kickoff or snapshot date"}
    lo = (first - timedelta(days=SLEEPER_WINDOW_DAYS)).date()
    if not (lo <= snap <= last.date()):
        return None, {"used": False, "reason": f"snapshot {snap} outside {lo}..{last.date()}"}
    out = (
        sp.filter(pl.col("gsis_id").is_not_null())
        .select(
            pl.col("gsis_id").str.strip_chars().alias("player_id"),
            pl.col("injury_status").alias("sleeper_injury_status"),
        )
        .unique("player_id", keep="first", maintain_order=True)
    )
    return out, {"used": True, "snapshot_date": str(snap), "rows": out.height}


def availability_layer(
    model: Any,
    q_rates: dict[str, float],
    live_extras: bool,
) -> Callable[[pl.DataFrame], np.ndarray]:
    """Returns the callable ChainModel uses to replace ``p_availability``."""
    from ffml.models.chain.availability import final_report_cap

    def caps(players: pl.DataFrame) -> np.ndarray:
        cap = final_report_cap(players, q_rates)
        if live_extras:
            roster = (
                players["avail__roster_status"].to_list()
                if "avail__roster_status" in players.columns
                else []
            )
            ina = (
                np.array([r == "INA" for r in roster], dtype=bool)
                if roster
                else np.zeros(players.height, bool)
            )
            cap = np.where(ina, 0.0, cap)
            if "sleeper_injury_status" in players.columns:
                status = (
                    players["avail__report_status"].to_list()
                    if "avail__report_status" in players.columns
                    else [None] * players.height
                )
                pos = players["position"].to_list()
                for i, s in enumerate(players["sleeper_injury_status"].to_list()):
                    if status[i] is not None or s is None:
                        continue  # nflverse report wins; Sleeper only breaks ties
                    if s in SLEEPER_CAPS:
                        cap[i] = min(cap[i], SLEEPER_CAPS[s])
                    elif s == "Questionable":
                        cap[i] = min(cap[i], q_rates.get(pos[i], 0.75))
        return cap

    def fn(players: pl.DataFrame) -> np.ndarray:
        return np.minimum(caps(players), model.primary_availability(players))

    fn.caps = caps  # type: ignore[attr-defined]
    return fn


def with_availability_columns(
    model: Any, players: pl.DataFrame, layer: Callable[[pl.DataFrame], np.ndarray]
) -> pl.DataFrame:
    """Player rows with ``avail_cap``, ``p_primary``, ``p_healthy`` and the final ``p_availability``."""
    cap = layer.caps(players)  # type: ignore[attr-defined]
    primary = model.primary_availability(players)
    reset = players.with_columns(
        [
            pl.lit(v).cast(players.schema[c]).alias(c)
            for c, v in HEALTHY_RESET.items()
            if c in players.columns
        ]
    )
    healthy = model.primary_availability(reset)
    return players.with_columns(
        pl.Series("avail_cap", cap),
        pl.Series("p_primary", primary),
        pl.Series("p_healthy", healthy),
        pl.Series("p_availability", np.minimum(cap, primary)),
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def fill_missing_lines(
    team: pl.DataFrame, strength: StrengthModel, rating: pl.DataFrame
) -> pl.DataFrame:
    """Rows whose schedule lines are null get strength-model lines (documented fallback)."""
    if "ctx__spread" not in team.columns or team["ctx__spread"].null_count() == 0:
        return team
    lines = strength_game_lines(
        team.select("game_id", "team", "opponent", "home"), rating, strength
    )
    j = team.join(
        lines.select("game_id", "team", *[f"{c}" for c in LINE_COLS]),
        on=["game_id", "team"],
        how="left",
        suffix="_s",
    )
    missing = pl.col("ctx__spread").is_null()
    return j.with_columns(
        [
            pl.when(missing).then(pl.col(f"{c}_s")).otherwise(pl.col(c)).alias(c)
            for c in LINE_COLS
            if c in j.columns
        ]
    ).drop([f"{c}_s" for c in LINE_COLS])


def team_env(teams: pl.DataFrame, tp: pl.DataFrame) -> pl.DataFrame:
    """Per team: simulated points and volume summaries plus the game environment inputs."""
    agg = teams.group_by(["game_id", "team"], maintain_order=True).agg(
        pl.col("points").cast(pl.Float64).mean().alias("points_mean"),
        *[
            pl.col("points")
            .cast(pl.Float64)
            .quantile(q, interpolation="linear")
            .alias(f"points_q{int(q * 100):02d}")
            for q in (0.1, 0.5, 0.9)
        ],
        *[
            pl.col(c).cast(pl.Float64).mean().alias(f"{c}_mean")
            for c in (
                "plays",
                "dropbacks",
                "pass_att",
                "rush_att",
                "pass_yds",
                "rush_yds",
                "off_td",
                "fg_att",
            )
        ],
    )
    keep = [
        "game_id",
        "team",
        "opponent",
        "home",
        "season",
        "week",
        "exp_plays",
        "exp_pass_rate",
        "exp_off_td",
        "exp_fg_att",
        "implied_total",
        "spread_line",
        "total_line",
        "total_sd",
        "margin_sd",
    ]
    return tp.select([c for c in keep if c in tp.columns]).join(
        agg, on=["game_id", "team"], how="left"
    )


def _fill_rows_replay(
    model: Any, season: int, week: int, teams: list[str]
) -> tuple[pl.DataFrame, ...]:
    """Latest earlier-week stored rows for teams without a projection-week row (no leakage)."""
    out = []
    for name in ("team_week", "player_week", "dst_week"):
        f = model.data.frame(name).filter(
            (pl.col("season") == season)
            & (pl.col("week") < week)
            & pl.col("team").is_in(teams)
            & (pl.col("game_type") == "REG")
        )
        last = f.group_by("team").agg(pl.col("week").max().alias("_w"))
        f = f.join(last, left_on=["team", "week"], right_on=["team", "_w"], how="semi")
        out.append(_blind(f))
    return tuple(out)


def _fill_rows_live(
    model: Any,
    ctx: Any,
    season: int,
    week: int,
    teams: list[str],
    schedule: pl.DataFrame,
    now: datetime,
) -> tuple[pl.DataFrame, ...]:
    """Rows built as of ``now`` for the next scheduled week of teams without a current row."""
    parts: dict[str, list[pl.DataFrame]] = {"team_week": [], "player_week": [], "dst_week": []}
    nxt = (
        schedule.filter((pl.col("week") > week) & pl.col("team").is_in(teams))
        .group_by("team")
        .agg(pl.col("week").min().alias("w"))
    )
    for w in sorted(set(nxt["w"].to_list())):
        group = nxt.filter(pl.col("w") == w)["team"].to_list()
        rows = live_rows(model, ctx, season, int(w), now)
        for name, frame in zip(("team_week", "player_week", "dst_week"), rows, strict=True):
            parts[name].append(frame.filter(pl.col("team").is_in(group)))
    return tuple(
        pl.concat(parts[n], how="diagonal_relaxed") if parts[n] else pl.DataFrame()
        for n in ("team_week", "player_week", "dst_week")
    )


def _strength(model: Any) -> StrengthModel:
    try:
        rows = model.data.train_rows("team_week", tuple(model.cutoff))
        return StrengthModel.fit(rows)
    except Exception as exc:  # noqa: BLE001 - fall back to documented defaults
        log.warning("strength model not fitted (%r); using defaults", exc)
        return StrengthModel(source=f"default ({type(exc).__name__})")


def _q_rates(model: Any) -> tuple[dict[str, float], str]:
    if model.q_rates:
        return dict(model.q_rates), "model"
    try:
        from ffml.models.chain.availability import questionable_rates

        rows = model.data.train_rows("player_week", tuple(model.cutoff))
        rates = questionable_rates(rows)
        if rates:
            return rates, "training window"
    except Exception as exc:  # noqa: BLE001
        log.warning("questionable rates not estimated (%r)", exc)
    return {}, "default 0.75"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run_predict(
    season: int | None = None,
    week: int | None = None,
    rest_of_season: bool = True,
    model_dir: Path | str | None = None,
    n_sims: int = 2000,
    ros_sims: int = 500,
    now: datetime | None = None,
    replay: bool = False,
    *,
    root: Path | str | None = None,
    run_id: str | None = None,
    model: Any = None,
    data: Any = None,
    ctx: Any = None,
    chain_root: Path | str | None = None,
    update_latest: bool | None = None,
    private_test: bool = False,
    model_version: str | None = None,
) -> PredictResult | None:
    """Predict a week (and the rest of the season) and write a snapshot; see the module docstring.

    The mode is live unless ``replay=True`` or ``private_test=True`` (with
    ``model_version``, a registry candidate). Keyword-only arguments are for
    tests and tooling: ``root`` (predictions directory; a private test accepts
    only the private store), ``run_id``, a fitted ``model`` (replay only,
    source recorded as ``object``), ``data`` (a ChainData for the loaded
    model), ``ctx`` (a FeatureContext for live rows), ``chain_root`` (replay
    artifact search path) and ``update_latest`` (default: live and private
    runs, each in its own store; never for replays).
    """
    t0 = time.perf_counter()
    now = _now(now)
    timings: dict[str, float] = {}
    in_progress = False
    if replay and private_test:
        raise PredictionRefused("choose one mode: replay or private_test")
    environment = "replay" if replay else "private_test" if private_test else "live"
    entry = _gate(
        environment,
        model_dir=model_dir,
        model=model,
        chain_root=chain_root,
        model_version=model_version,
        root=root,
        update_latest=update_latest,
    )
    if environment == "private_test":
        root = get_settings().private_predictions_dir
    if replay:
        if season is None or week is None:
            raise ValueError("replay needs an explicit season and week")
    elif season is None or week is None:
        from ffml.season import current_state

        state = current_state(now)
        if state.mode != "regular":
            msg = (
                f"ffml predict: season {state.season} is in {state.mode} mode at "
                f"{now.isoformat()}; there are no regular-season games to project. Nothing written."
            )
            log.warning(msg)
            print(msg)
            return None
        season, week = state.season, int(state.projection_week)
        in_progress = state.week_in_progress
    season, week = int(season), int(week)
    if not 1 <= week <= LAST_REG_WEEK:
        raise ValueError(f"week {week} is not a regular-season week")

    history = list(range(get_settings().first_season, season + 1))
    try:
        assert_seasons_allowed(history, "predict_replay" if replay else "predict")
    except HoldoutLockedError as exc:
        raise LivePredictionBlocked(blocked_message(season)) from exc

    # ---- model ------------------------------------------------------------ #
    from ffml.models.chain import ChainModel
    from ffml.models.chain.model import week_seed

    if model is not None:  # replay only; the gate refuses it elsewhere
        source = {"kind": "object", "name": model.name}
    elif entry is not None:  # live or private test: the registry entry, nothing else
        path = entry_path(entry)
        source = {
            "kind": "registry_live" if environment == "live" else "registry_candidate",
            "version": entry["version"],
            "path": str(path),
            "registry_status": entry["status"],
        }
        model = ChainModel.load(path, data=data)
        manifest_path = path / "train_manifest.json"
        if manifest_path.exists():
            import json

            built = json.loads(manifest_path.read_text(encoding="utf-8")).get("model_version")
            if built is not None and built != entry["version"]:
                raise PredictionRefused(
                    f"artifact {path} was built as {built!r}, not the registry version "
                    f"{entry['version']!r}"
                )
    else:
        path, source = resolve_model_dir(model_dir, chain_root)
        model = ChainModel.load(path, data=data)
        manifest_path = path / "train_manifest.json"
        if manifest_path.exists():
            import json

            source["version"] = json.loads(manifest_path.read_text(encoding="utf-8")).get(
                "model_version"
            )
    if environment == "live":
        approval = APPROVED
    elif environment == "replay":
        approval = replay_approval_status(source)
    else:
        approval = NOT_APPROVED
    source["cutoff"] = list(model.cutoff) if model.cutoff else None
    source["config"] = model.config
    if model.cutoff is None or tuple(model.cutoff) > (season, week):
        raise ValueError(
            f"model cutoff {model.cutoff} is after {season} week {week}: it was trained on "
            f"rows of the predicted week or later"
        )
    timings["load_model"] = time.perf_counter() - t0

    # ---- rows ------------------------------------------------------------- #
    t1 = time.perf_counter()
    skipped: list[str] = []
    inputs: dict[str, Any] = {}
    sleeper_info: dict[str, Any] = {"used": False, "reason": "replay"}
    if replay:
        rows = replay_rows(model, season, week)
        inputs["features"] = model.data.snapshot_ids
    else:
        if ctx is None:
            from ffml.features.data import FeatureContext

            ctx = FeatureContext.from_store([season])
        inputs["processed"] = dict(ctx.snapshot_ids)
        final = ctx.dim_game.filter(
            (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("status") == "final")
        )
        skipped = sorted(final["game_id"].to_list())
        rows = live_rows(model, ctx, season, week, now)
        sleeper, sleeper_info = sleeper_statuses(ctx, season, week)
        if sleeper is not None:
            rows = (rows[0], rows[1].join(sleeper, on="player_id", how="left"), rows[2])
    timings["rows"] = time.perf_counter() - t1
    if rows[0].height == 0:
        msg = f"ffml predict: no games left to predict in {season} week {week}. Nothing written."
        log.warning(msg)
        print(msg)
        return None

    strength = _strength(model)
    from ffml.sim.ros import ratings

    rating = ratings(rows[0], rows[2], strength.points_mean)
    rows = (fill_missing_lines(rows[0], strength, rating), rows[1], rows[2])
    q_rates, q_source = _q_rates(model)
    layer = availability_layer(model, q_rates, live_extras=not replay)

    # ---- projection week ------------------------------------------------- #
    t2 = time.perf_counter()
    scorings = {p: ScoringRules.from_preset(p) for p in PRESETS}
    seed = week_seed(model.seed, season, week)
    pred = model.predict_detailed(
        season, week, scorings, rows=rows, availability=layer, n_sims=n_sims, seed=seed
    )
    tp = pred.params[0]
    env = team_env(pred.teams, tp)
    timings["projection_week"] = time.perf_counter() - t2

    # ---- rest of season ---------------------------------------------------- #
    extra: dict[str, pl.DataFrame] = {}
    ros_info: dict[str, Any] = {"enabled": False}
    if rest_of_season and week < LAST_REG_WEEK:
        t3 = time.perf_counter()
        team_p, players_p, dst_p = model.stage_predictions(season, week, rows=rows)
        players_p = with_availability_columns(model, players_p, layer)
        if replay:
            schedule = (
                model.data.frame("team_week")
                .filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
                .select("season", "week", "game_id", "team", "opponent", "home")
            )
        else:
            schedule = ctx.team_games.filter(
                (pl.col("season") == season) & (pl.col("game_type") == "REG")
            ).select("season", "week", "game_id", "team", "opponent", "home")
        all_teams = set(schedule["team"].to_list())
        missing = sorted(all_teams - set(team_p["team"].to_list()))
        if missing:
            fill = (
                _fill_rows_replay(model, season, week, missing)
                if replay
                else _fill_rows_live(model, ctx, season, week, missing, schedule, now)
            )
            if fill[0].height:
                f_team, f_players, f_dst = model.stage_predictions(season, week, rows=fill)
                f_players = with_availability_columns(model, f_players, layer)
                team_p = pl.concat([team_p, f_team], how="diagonal_relaxed")
                players_p = pl.concat([players_p, f_players], how="diagonal_relaxed")
                dst_p = pl.concat([dst_p, f_dst], how="diagonal_relaxed")
        team_p = team_p.unique("team", keep="first", maintain_order=True)
        players_p = players_p.unique("player_id", keep="first", maintain_order=True)
        dst_p = dst_p.unique("team", keep="first", maintain_order=True)
        ros = simulate_ros(
            model,
            team_p,
            players_p,
            dst_p,
            schedule,
            strength,
            week,
            ros_sims,
            seed + 500_000,
            scorings,
        )
        if ros.sims.height:
            extra = {ROS_SIMS_FILE: ros.sims, ROS_SUMMARY_FILE: ros.summary}
        ros_info = {
            "enabled": True,
            **ros.info,
            "fill_teams": missing,
            "seconds": time.perf_counter() - t3,
        }
        timings["rest_of_season"] = time.perf_counter() - t3

    # ---- snapshot ----------------------------------------------------------- #
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = {"live": "", "private_test": "-private", "replay": "-replay"}[environment]
    run_id = run_id or f"{season}w{week:02d}-{stamp}{suffix}"
    version = source.get("version") or model.name
    labels: dict[str, str] = {"environment": environment, "approval_status": approval}
    summary = pred.frame
    if environment == "private_test":
        labels["label"] = NOT_APPROVED
        summary = summary.with_columns(pl.lit(NOT_APPROVED).alias("approval_status"))
        if ROS_SUMMARY_FILE in extra:
            extra[ROS_SUMMARY_FILE] = extra[ROS_SUMMARY_FILE].with_columns(
                pl.lit(NOT_APPROVED).alias("approval_status")
            )
    meta = {
        "model_version": version,
        **labels,
        "seeds": {
            "week": seed,
            "forced_pass": seed + 7,
            "ros": seed + 500_000,
            "model": model.seed,
        },
        "n_sims": n_sims,
        "inputs": inputs,
        "run_id": run_id,
        "mode": environment,
        "model_source": source,
        "model_name": model.name,
        "availability_config": AVAILABILITY_CONFIG,
        "availability": {
            "rules": "min(section G cap, primary P(active))",
            "questionable_rates": q_rates,
            "questionable_rates_source": q_source,
            "ina_applied": not replay,
            "sleeper_tiebreak": sleeper_info,
        },
        "as_of": now.isoformat(),
        "week_in_progress": in_progress,
        "skipped_final_games": skipped,
        "scoring_presets": list(PRESETS),
        "forced_active_entities": len(pred.forced_ids),
        "rest_of_season": ros_info,
        "timings": {k: round(v, 2) for k, v in timings.items()},
    }
    if update_latest is None:
        update_latest = not replay  # live: public LATEST; private test: the private one
    path = write_predictions_snapshot(
        season,
        week,
        run_id,
        pred.sims,
        summary,
        env,
        meta,
        root=root,
        update_latest=update_latest,
        extra_frames=extra,
    )
    timings["total"] = time.perf_counter() - t0
    msg = (
        f"predictions {season} week {week} ({environment}, {approval}) -> {path} "
        f"in {timings['total']:.1f}s"
    )
    log.info(msg)
    print(msg)
    return PredictResult(
        path=path,
        run_id=run_id,
        season=season,
        week=week,
        mode=meta["mode"],
        model_source=source,
        timings=timings,
        rows=pred.frame.height,
        ros_weeks=ros_info.get("weeks", []),
        skipped_final_games=skipped,
        environment=environment,
        approval_status=approval,
    )


__all__ = [
    "APPROVED",
    "AVAILABILITY_CONFIG",
    "ENVIRONMENTS",
    "NOT_APPROVED",
    "NO_APPROVED_MODEL_MESSAGE",
    "LivePredictionBlocked",
    "NoApprovedModel",
    "PredictResult",
    "PredictionRefused",
    "availability_layer",
    "blocked_message",
    "entry_path",
    "live_model_entry",
    "private_test_entry",
    "replay_approval_status",
    "resolve_model_dir",
    "run_predict",
]
