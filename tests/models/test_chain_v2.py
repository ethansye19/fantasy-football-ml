"""chain_v2 definition: pins, naming, and the walk-forward calibrator state (D-098)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.models import chain_v2
from ffml.models.calibration import FrozenCalibration
from ffml.models.chain.data import ChainData
from ffml.models.chain.model import resolve_choices
from ffml.models.chain.synthetic import synthetic_frames

POSITIONS = {"QB": 12, "RB": 16, "WR": 20, "TE": 2, "K": 5, "DST": 4}
QCOLS = ["pred_q05", "pred_q10", "pred_q25", "pred_q50", "pred_q75", "pred_q90", "pred_q95"]
COMPARE = [
    *QCOLS,
    "boom_prob",
    "bust_prob",
    "cal_q_source",
    "cal_n",
    "cal_boom_source",
    "cal_bust_source",
]
MULT = np.array([0.1, 0.3, 0.6, 1.0, 1.4, 1.8, 2.1])


def week_start(season: int, week: int) -> datetime:
    return datetime(season, 9, 4, 0, 15, tzinfo=UTC) + timedelta(days=7 * (week - 1))


def toy_history(
    seasons=(2021, 2022), last=(2022, 18), scorings=("ppr", "half"), seed=0, positions=POSITIONS
) -> pl.DataFrame:
    """Raw rows with harness outcome columns; integer-rounded scores give ties and atoms."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in range(1, 19):
            if (season, week) > last:
                break
            first = week_start(season, week)
            for pos, n in positions.items():
                for i in range(n):
                    loc = rng.gamma(2.0, 4.0)
                    active = bool(rng.random() < 0.9)
                    kick = first + timedelta(hours=0 if i % 4 == 0 else 72)
                    for scoring in scorings:
                        m = {"ppr": 1.0, "half": 0.9, "std": 0.8}[scoring]
                        q = np.round(np.maximum(0.0, loc * m * MULT + rng.normal(0, 0.3, 7)), 1)
                        q = np.maximum.accumulate(np.where(q < 0.6, 0.0, q))
                        actual = float(np.round(max(0.0, rng.gamma(2.0, loc * m / 2) - 1.0)))
                        boom = float(rng.random() * 0.6)
                        bust = float(rng.random())
                        rows.append(
                            {
                                "entity_id": f"{pos}{i:03d}",
                                "position": pos,
                                "team": f"T{i % 8}",
                                "game_id": f"{season}_{week:02d}_G{i % 8}",
                                "season": season,
                                "week": week,
                                "scoring": scoring,
                                "model": "chain_v1",
                                "pred_mean": float(q[3]),
                                **{c: float(v) for c, v in zip(QCOLS, q, strict=True)},
                                "boom_prob": boom,
                                "bust_prob": bust,
                                "actual": actual if active else 0.0,
                                "active": active,
                                "boom_actual": float(rng.random() < boom * 1.2) if active else None,
                                "bust_actual": float(rng.random() < bust) if active else None,
                                "kickoff_utc": kick,
                                "week_cutoff": first,
                                "pos_min": -2.0,
                                "pos_max": 60.0,
                            }
                        )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def target_rows(history: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    return history.filter((pl.col("season") == season) & (pl.col("week") == week))


def before(history: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    return history.filter(
        (pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") < week))
    )


@pytest.fixture(scope="module")
def history():
    return toy_history()


# --------------------------------------------------------------------------- #
# Definition and pins
# --------------------------------------------------------------------------- #


def test_pins_hold_on_the_repository():
    pins = chain_v2.check_pins()
    assert pins["selection_sha256"] == chain_v2.SELECTION_SHA256
    assert pins["feature_manifest_sha256"] == chain_v2.FEATURE_MANIFEST_SHA256
    assert pins["quantile_calibrator"] == chain_v2.QUANTILE_CALIBRATOR_REPR
    assert pins["isotonic_calibrator"] == chain_v2.ISOTONIC_CALIBRATOR_REPR


def test_pins_fail_on_other_feature_rows():
    data = ChainData(frames=synthetic_frames())  # feature_version "synthetic"
    with pytest.raises(chain_v2.PinMismatch, match="feature rows"):
        chain_v2.check_pins(data)


def test_calibrators_are_the_validation_settings():
    # the exact strings data/backtests/default_20261102/_manifest.json recorded
    assert repr(chain_v2.quantile_calibrator()) == chain_v2.QUANTILE_CALIBRATOR_REPR
    assert repr(chain_v2.isotonic_calibrator()) == chain_v2.ISOTONIC_CALIBRATOR_REPR
    q = chain_v2.quantile_calibrator()
    assert (q.seed, q.atoms, q.min_count, q.by) == (0, "nearest", 50, ("position",))


def test_raw_chain_is_chain_v1_primary_defaults():
    m = chain_v2.new_model(ChainData(frames=synthetic_frames()))
    assert (m.config, m.n_sims, m.seed) == ("primary", 2000, 20261101)
    assert m.choices == resolve_choices(None) and m.exclusions == ()
    assert m.name == chain_v2.RAW_MODEL_LABEL == "chain_v1"
    spec = chain_v2.spec()
    assert spec["unchanged"]["stage_choices"]["sha256"] == chain_v2.SELECTION_SHA256
    assert set(spec["changes"]) == {"a_training", "b_calibration"}
    assert spec["tuned"] == "nothing"


def test_registry_version_follows_the_chain_v1_convention():
    now = datetime(2026, 11, 5, 12, 0, 7, tzinfo=UTC)
    v = chain_v2.model_version((2026, 9), now)
    assert v == "chain_v2-primary-c2026w09-20261105T120007Z"
    v1 = "chain_v1-primary-c2024w19-20261102T222634Z"
    pattern = r"^chain_v[12]-primary-c\d{4}w\d{2}-\d{8}T\d{6}Z$"
    assert re.match(pattern, v) and re.match(pattern, v1)
    naive = chain_v2.model_version((2026, 10), datetime(2026, 11, 12, 9, 30))
    assert naive == "chain_v2-primary-c2026w10-20261112T093000Z"


def test_save_artifact_writes_the_manifest(tmp_path):
    data = ChainData(frames=synthetic_frames())
    model = chain_v2.new_model(data)
    model.n_sims = 100
    model.fit((2017, 10))
    out = chain_v2.save_artifact(
        model, tmp_path / "v", "chain_v2-primary-c2017w10-x", fit_seconds=1
    )
    manifest = json.loads((out / "train_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model"] == "chain_v2" and manifest["model_version"].endswith("-x")
    assert manifest["cutoff"] == [2017, 10] and manifest["raw_model_label"] == "chain_v1"
    assert manifest["train_seasons"] == [2016, 2017]
    assert json.loads((out / "chain_v2.json").read_text(encoding="utf-8"))["model"] == "chain_v2"
    assert (out / "params.json").exists() and (out / "selection.json").exists()
    with pytest.raises(FileExistsError):
        chain_v2.save_artifact(model, out, "again")


# --------------------------------------------------------------------------- #
# Calibrator state
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target", [(2021, 3), (2021, 10), (2022, 5), (2022, 18)])
def test_state_equals_the_online_walk_forward(history, target):
    """The state applied to a week gives exactly the harness's online calibrated rows."""
    rows = target_rows(history, *target)
    cutoff = rows["week_cutoff"].min()
    state = chain_v2.calibrator_state(history, target, cutoff)
    applied = state.apply(rows, lower_bound="pos_min", upper_bound="pos_max")
    online = chain_v2.online_reference(before(history, *target), rows)
    cmp = chain_v2.frames_equal(
        applied.select("scoring", "entity_id", *COMPARE),
        online.select("scoring", "entity_id", *COMPARE),
        COMPARE,
        ["scoring", "entity_id"],
    )
    assert cmp["equal"], cmp
    # the harness transform over every week (later ones included) gives the same target rows
    full_online = (
        chain_v2.quantile_calibrator()
        .transform(history, lower_bound="pos_min", upper_bound="pos_max")
        .pipe(chain_v2.isotonic_calibrator().transform)
        .filter((pl.col("season") == target[0]) & (pl.col("week") == target[1]))
    )
    cmp2 = chain_v2.frames_equal(
        applied.select("scoring", "entity_id", *COMPARE),
        full_online.select("scoring", "entity_id", *COMPARE),
        COMPARE,
        ["scoring", "entity_id"],
    )
    assert cmp2["equal"], cmp2


def test_sources_cover_position_pooled_and_identity(history):
    target = (2022, 5)
    rows = target_rows(history, *target)
    state = chain_v2.calibrator_state(history, target, rows["week_cutoff"].min())
    out = state.apply(rows, lower_bound="pos_min", upper_bound="pos_max")
    q_src = dict(out.group_by("position").agg(pl.col("cal_q_source").first()).iter_rows())
    assert q_src["WR"] == "position" and q_src["TE"] == "pooled"  # TE has < 50 history rows
    boom = dict(out.group_by("position").agg(pl.col("cal_boom_source").first()).iter_rows())
    assert boom["WR"] == "position" and boom["K"] == "pooled"  # K has < 200 rows
    early = (2021, 1)
    first = chain_v2.calibrator_state(
        history, early, target_rows(history, *early)["week_cutoff"].min()
    )
    applied = first.apply(
        target_rows(history, *early), lower_bound="pos_min", upper_bound="pos_max"
    )
    assert set(applied["cal_q_source"]) == {"identity"} and first.meta["rows"] == 0


def test_state_uses_only_earlier_weeks(history):
    """Target-week and later rows, outcomes included, never reach the state."""
    target = (2022, 5)
    cutoff = target_rows(history, *target)["week_cutoff"].min()
    base = chain_v2.calibrator_state(before(history, *target), target, cutoff)
    full = chain_v2.calibrator_state(history, target, cutoff)
    later = (pl.col("season") > 2022) | ((pl.col("season") == 2022) & (pl.col("week") >= 5))
    scrambled = history.with_columns(
        pl.when(later).then(pl.col("actual") + 7.0).otherwise(pl.col("actual")).alias("actual"),
        pl.when(later)
        .then(1.0 - pl.col("boom_actual"))
        .otherwise(pl.col("boom_actual"))
        .alias("boom_actual"),
        pl.when(later).then(pl.col("pred_q50") * 3).otherwise(pl.col("pred_q50")).alias("pred_q50"),
    )
    other = chain_v2.calibrator_state(scrambled, target, cutoff)
    assert base.to_dict() == full.to_dict() == other.to_dict()
    assert chain_v2.state_sha256(base) == chain_v2.state_sha256(other)
    assert base.meta["history_last"] == [2022, 4] and base.meta["history_first"] == [2021, 1]
    # an earlier-week game that kicks off after the cutoff (postponed) is left out
    late = history.filter((pl.col("season") == 2022) & (pl.col("week") == 4)).head(3)
    late = late.with_columns(pl.lit(cutoff + timedelta(days=1)).alias("kickoff_utc"))
    keys = ["season", "week", "scoring", "entity_id"]
    moved = history.join(late.select(keys), on=keys, how="anti")
    postponed = chain_v2.calibrator_state(pl.concat([moved, late]), target, cutoff)
    eligible = late.filter(pl.col("active")).height
    assert postponed.meta["rows"] == base.meta["rows"] - eligible


def test_state_hash_is_the_saved_bytes_and_round_trips(history, tmp_path):
    target = (2022, 5)
    rows = target_rows(history, *target)
    state = chain_v2.calibrator_state(history, target, rows["week_cutoff"].min())
    path = tmp_path / chain_v2.STATE_FILE
    path.write_bytes(chain_v2.state_payload(state))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == chain_v2.state_sha256(state)
    loaded = FrozenCalibration.load(path)
    a = state.apply(rows, lower_bound="pos_min", upper_bound="pos_max")
    b = loaded.apply(rows, lower_bound="pos_min", upper_bound="pos_max")
    assert a.select(COMPARE).equals(b.select(COMPARE))


def test_calibrate_uses_stored_bounds_and_labels_rows(history):
    target = (2022, 5)
    rows = target_rows(history, *target)
    bounds = rows.select("scoring", "position", "pos_min", "pos_max").unique()
    state = chain_v2.calibrator_state(history, target, rows["week_cutoff"].min(), bounds=bounds)
    with_cols = chain_v2.calibrate(state, rows)
    without = chain_v2.calibrate(state, rows.drop("pos_min", "pos_max"))
    assert with_cols.select(COMPARE).equals(without.select(COMPARE))
    assert set(with_cols["model"]) == {"chain_v2"}
    version = with_cols["calibration_version"].unique().to_list()
    assert version == [f"walk_forward:{chain_v2.state_sha256(state)[:12]}"]
    assert not with_cols.select(QCOLS).equals(rows.select(QCOLS))  # it did calibrate


def test_state_refuses_mixed_models_and_missing_columns(history):
    two = pl.concat([history, history.with_columns(pl.lit("other").alias("model"))])
    with pytest.raises(ValueError, match="one model"):
        chain_v2.calibrator_state(two, (2022, 5), None)
    with pytest.raises(ValueError, match="missing"):
        chain_v2.calibrator_state(history.drop("actual"), (2022, 5), None)


def test_pin_sha256_gives_one_digest_for_crlf_and_lf(tmp_path):
    """Tracked text pins: a CRLF working copy (core.autocrlf) and git's LF bytes hash alike."""
    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    lf.write_bytes(b'{\n  "a": 1,\n  "b": "x"\n}\n')
    crlf.write_bytes(b'{\r\n  "a": 1,\r\n  "b": "x"\r\n}\r\n')
    assert chain_v2.pin_sha256(lf) == chain_v2.pin_sha256(crlf)
    assert chain_v2.pin_sha256(lf) == hashlib.sha256(lf.read_bytes()).hexdigest()
    # artifact and data files are hashed as they are
    assert chain_v2.pin_sha256(crlf, text=False) == hashlib.sha256(crlf.read_bytes()).hexdigest()
    assert chain_v2.pin_sha256(crlf, text=False) != chain_v2.pin_sha256(lf, text=False)
    # the repository pins are the LF form; the CRLF form is only documented
    from ffml.models.chain.model import SELECTION_PATH

    raw = SELECTION_PATH.read_bytes()
    assert chain_v2.pin_sha256(SELECTION_PATH) == chain_v2.SELECTION_SHA256
    assert hashlib.sha256(raw.replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n")).hexdigest() == (
        chain_v2.SELECTION_SHA256_CRLF_FORM
    )
    assert chain_v2.spec()["unchanged"]["stage_choices"]["sha256"] == chain_v2.SELECTION_SHA256
