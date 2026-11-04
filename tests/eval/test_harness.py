"""Walk-forward harness with a fake WeeklyModel on synthetic stat lines (no data store needed)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.eval.harness import (
    build_schedule,
    load_backtest,
    outcome_table,
    part_path,
    pit_values,
    read_manifest,
    refit_points,
    run_walk_forward,
    week_cutoffs,
)
from ffml.eval.metrics import evaluate
from ffml.holdout import HoldoutLockedError
from ffml.models.calibration import CAL_SUFFIX, CALIBRATION_VERSION
from ffml.models.protocol import PREDICTION_COLUMNS
from ffml.scoring.rules import ScoringRules
from ffml.scoring.statline import ensure_stat_columns

QCOLS = ["pred_q05", "pred_q10", "pred_q25", "pred_q50", "pred_q75", "pred_q90", "pred_q95"]
SCORINGS = {
    "a": ScoringRules.from_dict({"rec": 1, "sack": 1}, name="a"),
    "b": ScoringRules.from_dict({"rec": 0.5, "sack": 2}, name="b"),
}
POSITIONS = {"QB": 8, "RB": 14, "WR": 16, "TE": 8, "K": 8, "DST": 8}
SEASONS = (2020, 2021, 2022)
WEEKS = range(1, 5)


def kick(season: int, week: int) -> datetime:
    return datetime(season, 9, 7, 17, 0, tzinfo=UTC) + timedelta(days=7 * (week - 1))


def synthetic_actuals(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season in SEASONS:
        for week in WEEKS:
            for pos, n in POSITIONS.items():
                for i in range(n):
                    played = bool(rng.uniform() > 0.15) or pos == "DST"
                    value = int(rng.poisson(4 + i % 6)) if played else 0
                    rows.append(
                        {
                            "entity_id": f"{pos}{i}",
                            "position": pos,
                            "season": season,
                            "week": week,
                            "game_id": f"{season}_{week:02d}_G{i % 4}",
                            "team": f"T{i % 8}",
                            "opponent": "OPP",
                            "game_type": "REG",
                            "kickoff_utc": kick(season, week) + timedelta(hours=3 * (i % 2)),
                            "played": played,
                            "rec": value if pos != "DST" else 0,
                            "sacks": value if pos == "DST" else 0,
                        }
                    )
    frame = pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    return ensure_stat_columns(frame)


class FakeModel:
    """Predicts every entity of the week (plus one ghost with no game row) with fixed spreads."""

    def __init__(
        self, actuals: pl.DataFrame, name: str = "fake", fail_at=None, crossed: bool = False
    ):
        self.name = name
        self.actuals = actuals
        self.fits: list[tuple[int, int]] = []
        self.predicts: list[tuple[int, int]] = []
        self.fail_at = fail_at
        self.crossed = crossed

    @property
    def params(self):
        return {"kind": "fake", "fitted_at": {f"{s}_{w}": True for s, w in self.fits}}

    def fit(self, cutoff):
        self.fits.append(tuple(cutoff))
        return self

    def predict(self, season, week, scorings):
        if self.fail_at == (season, week):
            raise RuntimeError("simulated crash")
        self.predicts.append((season, week))
        rows = self.actuals.filter((pl.col("season") == season) & (pl.col("week") == week)).select(
            "entity_id", "position", "team", "game_id"
        )
        rows = pl.concat(
            [
                rows,
                pl.DataFrame(
                    {"entity_id": ["GHOST"], "position": ["WR"], "team": ["T0"], "game_id": [None]}
                ),
            ],
            how="vertical_relaxed",
        )
        frames = []
        for label in scorings:
            base = rows.with_columns(
                pl.lit(season, dtype=pl.Int32).alias("season"),
                pl.lit(week, dtype=pl.Int32).alias("week"),
                pl.lit(label).alias("scoring"),
                pl.lit(self.name).alias("model"),
                (pl.col("entity_id").str.len_chars().cast(pl.Float64) + 3.0).alias("pred_mean"),
            )
            spreads = [-4, -3, -1.5, 0, 1.5, 3, 4]
            if self.crossed:
                spreads = [-4, -3, 2, 0, 1.5, 3, 4]
            base = base.with_columns(
                (pl.col("pred_mean") + s).alias(c) for c, s in zip(QCOLS, spreads, strict=True)
            )
            frames.append(base.select(PREDICTION_COLUMNS))
        return pl.concat(frames)


def run(tmp_path, models, run_id="unit", seasons=(2021, 2022), refit_every=1, **kwargs):
    actuals = kwargs.pop("actuals", None)
    if actuals is None:
        actuals = synthetic_actuals()
    return run_walk_forward(
        models,
        seasons,
        weeks=WEEKS,
        scorings=SCORINGS,
        refit_every=refit_every,
        run_id=run_id,
        root=tmp_path / "backtests",
        actuals=actuals,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


def test_schedule_and_refit_points():
    cutoffs = week_cutoffs(synthetic_actuals())
    sched = build_schedule([2021, 2022], range(1, 19), cutoffs)
    assert sched == [(s, w) for s in (2021, 2022) for w in WEEKS]
    assert refit_points(sched, 1) == list(range(8))
    # every season starts with a fit, then every second scheduled week
    assert refit_points(sched, 2) == [0, 0, 2, 2, 4, 4, 6, 6]
    assert refit_points(sched, 3) == [0, 0, 0, 3, 4, 4, 4, 7]
    with pytest.raises(ValueError):
        refit_points(sched, 0)


# --------------------------------------------------------------------------- #
# Joins and outcomes
# --------------------------------------------------------------------------- #


def test_join_actuals_active_and_ghost(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    model = FakeModel(actuals)
    run_dir = run(tmp_path, [model], actuals=actuals)
    frame = pl.read_parquet(run_dir / "predictions.parquet")
    raw = frame.filter(pl.col("model") == "fake")
    assert raw.height == 2 * len(WEEKS) * 2 * (sum(POSITIONS.values()) + 1)
    for label, rules in SCORINGS.items():
        expected = actuals.filter(pl.col("season").is_in([2021, 2022])).select(
            "entity_id",
            "season",
            "week",
            pl.col("played").alias("exp_active"),
            rules.score(actuals.filter(pl.col("season").is_in([2021, 2022]))).alias("exp_actual"),
        )
        got = raw.filter(pl.col("scoring") == label).join(
            expected, on=["entity_id", "season", "week"], how="inner"
        )
        assert got.height == 2 * len(WEEKS) * sum(POSITIONS.values())
        assert (got["actual"] == got["exp_actual"]).all()
        assert (got["active"] == got["exp_active"]).all()
    ghost = raw.filter(pl.col("entity_id") == "GHOST")
    assert ghost["actual"].null_count() == ghost.height
    assert not ghost["active"].any()
    # inactive rostered rows score zero and carry no boom/bust outcome
    inactive = raw.filter(~pl.col("active") & pl.col("actual").is_not_null())
    assert inactive.height > 0
    assert (inactive["actual"] == 0).all()
    assert inactive["boom_actual"].null_count() == inactive.height
    assert model.fits == [(s, w) for s in (2021, 2022) for w in WEEKS]


def test_boom_and_bust_actuals_follow_rank_rules():
    rows = [
        {
            "entity_id": f"W{i}",
            "position": "WR",
            "season": 2021,
            "week": 1,
            "rec": v,
            "played": True,
        }
        for i, v in enumerate([10, 9, 9, 5, 0])
    ]
    rows.append(
        {"entity_id": "W9", "position": "WR", "season": 2021, "week": 1, "rec": 0, "played": False}
    )
    frame = ensure_stat_columns(
        pl.DataFrame(
            [
                dict(
                    r,
                    game_id="G",
                    team="T",
                    opponent="O",
                    game_type="REG",
                    kickoff_utc=kick(2021, 1),
                )
                for r in rows
            ]
        ).with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
    )
    out = outcome_table(frame, [2021], {"a": SCORINGS["a"]}, thresholds={"WR": (2, 3)}).sort(
        "entity_id"
    )
    got = dict(
        zip(out["entity_id"], zip(out["boom_actual"], out["bust_actual"], strict=True), strict=True)
    )
    # min ranks 1, 2, 2, 4, 5: the tie for second is a boom; max ranks 1, 3, 3, 4, 5: outside 3 busts
    assert got["W0"] == (1.0, 0.0)
    assert got["W1"] == (1.0, 0.0) and got["W2"] == (1.0, 0.0)
    assert got["W3"] == (0.0, 1.0) and got["W4"] == (0.0, 1.0)
    assert got["W9"] == (None, None)  # did not play: not ranked


def test_run_boom_actuals_use_default_thresholds(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    run_dir = run(tmp_path, [FakeModel(actuals)], actuals=actuals)
    raw = pl.read_parquet(run_dir / "predictions.parquet").filter(
        (pl.col("model") == "fake") & pl.col("active")
    )
    per_week = raw.group_by(["scoring", "season", "week", "position"]).agg(
        pl.col("boom_actual").sum().alias("booms"), pl.len().alias("n")
    )
    qb = per_week.filter(pl.col("position") == "QB")
    # QB boom is a top-6 finish among QBs who played: at least min(6, n) booms (more only with ties)
    assert (qb["booms"] >= qb["n"].clip(upper_bound=6)).all() and (qb["booms"] <= qb["n"]).all()
    assert (qb["n"] > 6).any() and (qb["booms"] < qb["n"]).any()


# --------------------------------------------------------------------------- #
# PIT
# --------------------------------------------------------------------------- #


def test_pit_interpolation():
    q = np.array([[0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0]] * 7)
    y = np.array([3.0, 0.0, -1.0, 15.0, 25.0, -5.0, 9.0])
    lo = np.array([-2.0] * 7)
    hi = np.array([20.0] * 7)
    got = pit_values(y, q, lo, hi)
    assert got[0] == pytest.approx(0.25 + 0.25 * (3 - 2) / 2)  # between q25 and q50
    assert got[1] == pytest.approx(0.05)  # exactly q05
    assert got[2] == pytest.approx(0.025)  # lower tail to the position minimum
    assert got[3] == pytest.approx(0.95 + 0.05 * 5 / 10)  # upper tail to the position maximum
    assert got[4] == 1.0 and got[5] == 0.0
    assert got[6] == pytest.approx(0.90 + 0.05 * 0.5)
    # a point mass at zero (q05 = q10 = q25 = 0 = minimum): mid-point of the jump
    mass = pit_values(
        np.array([0.0]),
        np.array([[0.0, 0.0, 0.0, 3.0, 6.0, 9.0, 12.0]]),
        np.array([0.0]),
        np.array([30.0]),
    )
    assert mass[0] == pytest.approx(0.125)
    # missing bounds drop the tails; missing actual gives NaN
    nb = pit_values(
        np.array([-1.0, np.nan]), q[:2], np.array([np.nan, 0.0]), np.array([np.nan, 1.0])
    )
    assert nb[0] == 0.0 and np.isnan(nb[1])


def test_run_pit_values_lie_in_unit_interval(tmp_settings, tmp_path):
    run_dir = run(tmp_path, [FakeModel(synthetic_actuals())])
    frame = pl.read_parquet(run_dir / "predictions.parquet").filter(pl.col("actual").is_not_null())
    pit = frame["pred_cdf_at_actual"]
    assert pit.null_count() == 0
    assert pit.min() >= 0.0 and pit.max() <= 1.0


# --------------------------------------------------------------------------- #
# Resumability
# --------------------------------------------------------------------------- #


def test_resume_skips_written_parts_and_refits_at_the_right_cutoff(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    clean_dir = run(tmp_path, [FakeModel(actuals)], run_id="clean", refit_every=2)
    crashing = FakeModel(actuals, fail_at=(2022, 2))
    with pytest.raises(RuntimeError):
        run(tmp_path, [crashing], run_id="resume", refit_every=2, actuals=actuals)
    assert crashing.predicts == [(2021, 1), (2021, 2), (2021, 3), (2021, 4), (2022, 1)]
    run_dir = tmp_path / "backtests" / "resume"
    assert part_path(run_dir, "fake", 2022, 1).exists()
    assert not part_path(run_dir, "fake", 2022, 2).exists()
    assert not (run_dir / "predictions.parquet").exists()

    fresh = FakeModel(actuals)
    run(tmp_path, [fresh], run_id="resume", refit_every=2, actuals=actuals)
    assert fresh.predicts == [(2022, 2), (2022, 3), (2022, 4)]
    # week 2 of 2022 belongs to the fit at week 1 (refit_every = 2), week 3 refits
    assert fresh.fits == [(2022, 1), (2022, 3)]
    resumed = pl.read_parquet(run_dir / "predictions.parquet")
    clean = pl.read_parquet(clean_dir / "predictions.parquet")
    assert resumed.equals(clean)

    # a third call has nothing to predict
    idle = FakeModel(actuals)
    run(tmp_path, [idle], run_id="resume", refit_every=2, actuals=actuals)
    assert idle.predicts == [] and idle.fits == []
    timing = json.loads((run_dir / "parts" / "fake" / "_timing.json").read_text())
    assert timing["parts"] == 8
    sessions = (run_dir / "_sessions.jsonl").read_text().strip().splitlines()
    assert len(sessions) == 2  # the crashed session wrote no session line


# --------------------------------------------------------------------------- #
# Guard, validation, manifest, evaluator hand-off
# --------------------------------------------------------------------------- #


def test_refuses_the_holdout_while_locked(tmp_settings, tmp_path):
    model = FakeModel(synthetic_actuals())
    for seasons in ([2025], [2024, 2025]):
        with pytest.raises(HoldoutLockedError):
            run(tmp_path, [model], seasons=seasons)
    assert model.fits == [] and model.predicts == []
    assert not (tmp_path / "backtests" / "unit").exists()


def test_bad_frames_are_rejected(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    with pytest.raises(ValueError, match="non-monotone"):
        run(tmp_path, [FakeModel(actuals, crossed=True)], run_id="crossed")

    class WrongWeek(FakeModel):
        def predict(self, season, week, scorings):
            return (
                super()
                .predict(season, week, scorings)
                .with_columns(pl.lit(week + 1, dtype=pl.Int32).alias("week"))
            )

    with pytest.raises(ValueError, match="week"):
        run(tmp_path, [WrongWeek(actuals)], run_id="wrongweek")
    with pytest.raises(ValueError, match="unique"):
        run(tmp_path, [FakeModel(actuals), FakeModel(actuals)], run_id="dupes")


def test_manifest_calibration_and_evaluate_accepts_the_output(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    models = [FakeModel(actuals, name="fake_a"), FakeModel(actuals, name="fake_b")]
    run_dir = run(tmp_path, models, seasons=(2021, 2022), report_seasons=[2022], config_name="unit")
    manifest = read_manifest(run_dir)
    assert manifest["report_seasons"] == [2022] and manifest["warmup_seasons"] == [2021]
    assert manifest["seasons"] == [2021, 2022] and manifest["refit_every"] == 1
    assert set(manifest["scorings"]) == {"a", "b"}
    assert [m["key"] for m in manifest["models"]] == ["fake_a", "fake_b"]
    assert all(m["wall_time_seconds"] >= 0 and m["parts"] == 8 for m in manifest["models"])
    assert manifest["rows"]["raw"] == manifest["rows"]["calibrated"]
    assert "predictions.parquet" in manifest["files"] and manifest["git"]

    full = pl.read_parquet(run_dir / "predictions.parquet")
    assert set(full["model"]) == {"fake_a", "fake_b", "fake_a" + CAL_SUFFIX, "fake_b" + CAL_SUFFIX}
    # the first week of the run has no calibration history: calibrated equals raw
    first = full.filter((pl.col("season") == 2021) & (pl.col("week") == 1))
    raw_q = first.filter(pl.col("model") == "fake_a").sort(["scoring", "entity_id"]).select(QCOLS)
    cal_q = (
        first.filter(pl.col("model") == "fake_a" + CAL_SUFFIX)
        .sort(["scoring", "entity_id"])
        .select(QCOLS)
    )
    assert raw_q.equals(cal_q)

    frame = load_backtest(run_dir, "a")
    assert set(frame["season"]) == {2022} and set(frame["scoring"]) == {"a"}
    result = evaluate(frame, n_boot=0, per_week=False)
    mae = result.summary.filter(pl.col("metric") == "mae")
    assert set(mae["model"]) == set(frame["model"])
    assert mae["value"].is_not_null().all()
    assert "pit_max_dev" in set(result.summary["metric"])
    assert "brier_boom" not in set(result.summary["metric"])  # no boom_prob from the fake model


def test_manifest_params_survive_a_combine_only_session(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    run(tmp_path, [FakeModel(actuals)], run_id="params")
    # a second process that only combines (as in a parallel run) never fits
    idle = FakeModel(actuals)
    run_dir = run(tmp_path, [idle], run_id="params")
    assert idle.fits == []
    params = read_manifest(run_dir)["models"][0]["params"]
    assert params["kind"] == "fake" and len(params["fitted_at"]) == 8


class BoomModel(FakeModel):
    """Adds boom and bust probabilities so the isotonic step runs."""

    def predict(self, season, week, scorings):
        out = super().predict(season, week, scorings)
        h = (pl.col("entity_id").hash(seed=week) % 1000).cast(pl.Float64) / 1000.0
        return out.with_columns(h.alias("boom_prob"), (1.0 - h).alias("bust_prob"))


def test_calibration_version_and_sources_on_recombine(tmp_settings, tmp_path):
    actuals = synthetic_actuals()
    run_dir = run(tmp_path, [BoomModel(actuals)], run_id="v2")
    first = pl.read_parquet(run_dir / "predictions.parquet")
    manifest = read_manifest(run_dir)
    assert manifest["calibration_version"] == CALIBRATION_VERSION
    assert manifest["calibration"]["version"] == CALIBRATION_VERSION
    raw = first.filter(pl.col("model") == "fake")
    cal = first.filter(pl.col("model") == "fake" + CAL_SUFFIX)
    for col in ("calibration_version", "cal_boom_source", "cal_bust_source"):
        assert raw[col].null_count() == raw.height
    assert set(cal["calibration_version"]) == {CALIBRATION_VERSION}
    assert set(cal["cal_boom_source"]) <= {"identity", "pooled", "position"}
    assert set(cal["cal_q_source"]) <= {"identity", "pooled", "position"}
    assert "position" in set(cal["cal_q_source"])
    assert raw["cal_q_source"].null_count() == raw.height
    # the PIT bounds are written for every row with an earlier season
    assert first.filter(pl.col("season") == 2022)["pos_min"].null_count() == 0
    assert set(
        cal.filter((pl.col("season") == 2021) & (pl.col("week") == 1))["cal_boom_source"]
    ) == {"identity"}
    # recombining a finished run refits nothing and reproduces the file
    idle = BoomModel(actuals)
    run(tmp_path, [idle], run_id="v2")
    assert idle.fits == [] and idle.predicts == []
    assert pl.read_parquet(run_dir / "predictions.parquet").equals(first)


# --------------------------------------------------------------------------- #
# FIXED (frozen) calibration, pre-registered for the holdout (D-095)
# --------------------------------------------------------------------------- #


def _sha(path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_setup(tmp_path, names=("fake_a", "fake_b")):
    """An online run on 2021-2022, calibrators frozen on its 2021 rows; returns actuals and manifest."""
    from ffml.eval.harness import freeze_calibrators

    actuals = synthetic_actuals()
    models = [BoomModel(actuals, name=n) for n in names]
    src = run(tmp_path, models, run_id="source", seasons=(2021, 2022), report_seasons=[2022])
    manifest = freeze_calibrators(
        src, list(names), tmp_path / "frozen", seasons=[2021], actuals=actuals, scorings=SCORINGS
    )
    return actuals, manifest


def test_frozen_mode_applies_calibrators_unchanged_and_never_fits(
    tmp_settings, tmp_path, monkeypatch
):
    from ffml.models import calibration as C

    actuals, manifest = frozen_setup(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["mode"] == "fixed" and payload["fitted_on"]["seasons"] == [2021]
    for key, entry in payload["calibrators"].items():
        assert _sha(tmp_path / "frozen" / key / "calibration.json") == entry["sha256"]

    def forbidden(*args, **kwargs):
        raise AssertionError("frozen mode must not fit any calibrator")

    for target in (
        (C.OnlinePITQuantile, "transform"),
        (C.OnlinePITQuantile, "fit_final"),
        (C.OnlineIsotonic, "transform"),
        (C.OnlineIsotonic, "fit_final"),
        (C.OnlineConformal, "transform"),
        (C.OnlineConformal, "fit_final"),
    ):
        monkeypatch.setattr(*target, forbidden)
    models = [BoomModel(actuals, name=n) for n in ("fake_a", "fake_b")]
    run_dir = run(
        tmp_path,
        models,
        run_id="frozen",
        seasons=(2022,),
        calibration="frozen",
        frozen_manifest=manifest,
    )
    out = pl.read_parquet(run_dir / "predictions.parquet")
    m = read_manifest(run_dir)
    assert m["calibration"]["mode"] == "frozen" and m["calibration_version"] == "frozen"
    rec = m["calibration"]["frozen_manifest"]
    assert rec["sha256"] == _sha(manifest)
    assert m["report_seasons"] == [2022] and m["warmup_seasons"] == []
    for key in ("fake_a", "fake_b"):
        file = tmp_path / "frozen" / key / "calibration.json"
        assert (
            rec["calibrators"][key]["sha256"] == _sha(file) == payload["calibrators"][key]["sha256"]
        )
        label = f"frozen:{_sha(file)[:12]}"
        cal = out.filter(pl.col("model") == key + CAL_SUFFIX)
        assert set(cal["calibration_version"]) == {label}
        # calibrated rows equal the frozen calibrator applied to the raw rows, every week (no warm-up)
        raw = out.filter(pl.col("model") == key)
        expect = C.FrozenCalibration.load(file).apply(
            raw, lower_bound="pos_min", upper_bound="pos_max"
        )
        k = ["scoring", "season", "week", "entity_id"]
        q = [
            "pred_q05",
            "pred_q10",
            "pred_q25",
            "pred_q50",
            "pred_q75",
            "pred_q90",
            "pred_q95",
            "boom_prob",
        ]
        assert cal.sort(k).select(q).equals(expect.sort(k).select(q))
        first_week = cal.filter(pl.col("week") == 1)
        assert set(first_week["cal_q_source"]) <= {
            "position",
            "pooled",
        }  # frozen levels from week 1


def test_frozen_mode_without_a_calibrator_fails_before_predicting(tmp_settings, tmp_path):
    actuals, manifest = frozen_setup(tmp_path, names=("fake_a",))
    extra = BoomModel(actuals, name="uncalibrated")
    with pytest.raises(ValueError, match="no frozen calibrator"):
        run(
            tmp_path,
            [BoomModel(actuals, name="fake_a"), extra],
            run_id="missing",
            seasons=(2022,),
            calibration="frozen",
            frozen_manifest=manifest,
        )
    assert extra.fits == [] and extra.predicts == []
    with pytest.raises(FileNotFoundError):
        run(
            tmp_path,
            [extra],
            run_id="nomanifest",
            seasons=(2022,),
            calibration="frozen",
            frozen_manifest=tmp_path / "nope.json",
        )
    # a calibrator whose bytes changed after registration is refused too
    file = tmp_path / "frozen" / "fake_a" / "calibration.json"
    file.write_text(file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    model = BoomModel(actuals, name="fake_a")
    with pytest.raises(ValueError, match="changed"):
        run(
            tmp_path,
            [model],
            run_id="tampered",
            seasons=(2022,),
            calibration="frozen",
            frozen_manifest=manifest,
        )
    assert model.predicts == []


def test_pre_registered_configs(tmp_settings):
    from ffml.eval.backtest_configs import get_config
    from ffml.eval.harness import run_backtest

    hold, fwd, dry = (
        get_config("holdout_2025"),
        get_config("forward_2026"),
        get_config("frozen_dryrun_2024"),
    )
    assert hold.seasons == (2025,) and hold.weeks == tuple(range(1, 19)) and hold.refit_every == 1
    assert fwd.seasons == (2026,) and fwd.weeks == tuple(range(1, 8))
    assert dry.seasons == (2024,) and dry.weeks == (1, 2, 3)
    for cfg in (hold, fwd):
        assert cfg.purpose == "holdout_eval" and cfg.calibration == "frozen" and cfg.strict_models
        assert cfg.include_baselines and cfg.include_challenger and cfg.include_chain
    # refused by the existing guard while locked, before any model is built or anything written
    for name in ("holdout_2025", "forward_2026"):
        with pytest.raises(HoldoutLockedError):
            run_backtest(name, run_id=f"locked_{name}")
        assert not (tmp_settings.data_dir / "backtests" / f"locked_{name}").exists()
