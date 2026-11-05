"""chain_v2: chain_v1's primary configuration with 2025 in training and a walk-forward calibrator (D-098).

What chain_v2 is
================
chain_v2 differs from chain_v1's primary configuration (registry version
``chain_v1-primary-c2024w19-20261102T222634Z``, rejected) in exactly two ways.

(a) **Training data.** ``ChainModel.fit(cutoff)`` trains on every
    regular-season row strictly before the cutoff. chain_v1 was frozen at
    cutoff (2024, 19). chain_v2 is refit for every live week ``W`` at that
    week's cutoff, so its training rows include 2025 (released as training
    data and history, ``artifacts/holdout_release.json``, ``approved=false``)
    and the 2026 weeks before ``W``. Nothing else about the fit changes.
(b) **Calibration.** chain_v1's holdout calibrator was FIXED on 2022-2024
    (D-095). chain_v2 is calibrated every week by the v3 online calibrator
    exactly as the validation walk-forward ``data/backtests/default_20261102``
    applied it (``ffml.eval.harness.calibrated_rows``):
    :class:`~ffml.models.calibration.OnlinePITQuantile` (seed 0, per
    position, ``min_count`` 50, ``atoms="nearest"``, calibration version
    ``v3-tie-aware-quantile``) for the quantiles and per-position
    :class:`~ffml.models.calibration.OnlineIsotonic` (``min_count`` 200) for
    boom and bust. For week ``W`` both are fitted only on (raw prediction,
    outcome) pairs of earlier weeks, from the 2021 warm-up season through
    week ``W - 1``, and only on games that kicked off before week ``W``'s
    first kickoff.

Unchanged and checked by :func:`check_pins`: the stage choices
(``ffml/models/chain/selection.json``, SHA-256 :data:`SELECTION_SHA256`;
pins of tracked text files hash LF-normalized bytes, :func:`pin_sha256`, so
a CRLF working copy under ``core.autocrlf`` and the LF bytes git and CI see
give one digest),
features 0.2.0 (manifest pin :data:`FEATURE_MANIFEST_SHA256`), every
hyperparameter (code), the seed 20261101 (per week ``seed + 100*season +
week``), 2,000 simulations, the live availability layer (``final_report``)
and every calibrator setting (:data:`QUANTILE_CALIBRATOR_REPR`,
:data:`ISOTONIC_CALIBRATOR_REPR`, the strings the validation run recorded).
Nothing is searched, re-selected or tuned.

The raw chain is unchanged code, so raw prediction rows keep the model label
``chain_v1`` (the label :class:`~ffml.models.chain.ChainModel` writes);
calibrated rows carry the label ``chain_v2``.

Calibrator state
================
For one target week, the online calibrators' whole input is the set of
earlier rows. :func:`calibrator_state` computes from those rows exactly the
levels and isotonic maps that ``OnlinePITQuantile.transform`` and
``OnlineIsotonic.transform`` compute for that week, and returns them as a
:class:`~ffml.models.calibration.FrozenCalibration` (``pit_quantile``
method) that is saved, hashed and applied with ``FrozenCalibration.apply``.
The randomized PITs are drawn over every eligible earlier row of a scoring in
(season, week, position, entity) order from one generator seeded 0; the draws
are sequential, so rows of later weeks never change the PIT of an earlier
row, and the draws for 2021-2024 equal those of the validation run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.models.calibration import (
    CALIBRATION_VERSION,
    PIT_METHOD,
    FrozenCalibration,
    OnlineIsotonic,
    OnlinePITQuantile,
    _calibration_rows,
    _earlier,
    _isotonic_entry,
)

MODEL_NAME = "chain_v2"
#: label of raw chain rows (the unchanged structural chain writes it)
RAW_MODEL_LABEL = "chain_v1"
CONFIG = "primary"
N_SIMS = 2000
SEED = 20261101
#: the live availability layer of ffml.sim.predict (D-065), unchanged
AVAILABILITY_CONFIG = "final_report"
FEATURE_VERSION = "0.2.0"
#: SHA-256 of the LF bytes of selection.json (the form git stores and CI checks out)
SELECTION_SHA256 = "75477b2b97bc7d9bef935c2cfd470158d5b809b62f531fe898de2f7d2da84601"
#: the same file's CRLF working-copy form, the digest the holdout pre-registration recorded
SELECTION_SHA256_CRLF_FORM = "d6c1891828657b76de9cb5aab51a314cd5d85c014ad4a2ba4546b9ad2e82e0da"
#: SHA-256 of tests/fixtures/features/feature_manifest_0.2.0.json (LF; identical normalized)
FEATURE_MANIFEST_SHA256 = "b119f85cdfdc3546b3fb053ca480da6b5e543349940080a3bd19ae692f2c42f6"
#: the calibrators exactly as data/backtests/default_20261102/_manifest.json recorded them
QUANTILE_CALIBRATOR_REPR = (
    "OnlinePITQuantile(levels=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95), by=('position',), "
    "min_count=50, seed=0, atoms='nearest')"
)
ISOTONIC_CALIBRATOR_REPR = (
    "OnlineIsotonic(pairs=(('boom_prob', 'boom_actual'), ('bust_prob', 'bust_actual')), "
    "by=('position',), min_count=200)"
)
#: first season of the calibration history (the validation run's warm-up season)
WARMUP_SEASON = 2021
#: the rejected predecessor whose primary configuration chain_v2 keeps
PREDECESSOR = "chain_v1-primary-c2024w19-20261102T222634Z"
SCORINGS: tuple[str, ...] = ("ppr", "half", "std")
STATE_FILE = "calibration_state.json"
SPEC_FILE = "chain_v2.json"


class PinMismatch(RuntimeError):
    """A pinned input of chain_v2 (stage choices, features, calibrator settings) has changed."""


# --------------------------------------------------------------------------- #
# Definition and pins
# --------------------------------------------------------------------------- #


def quantile_calibrator() -> OnlinePITQuantile:
    """The v3 quantile calibrator of the validation walk-forward (seed 0, all defaults)."""
    return OnlinePITQuantile(seed=0)


def isotonic_calibrator() -> OnlineIsotonic:
    """The per-position boom/bust calibrator of the validation walk-forward (defaults)."""
    return OnlineIsotonic()


def pin_sha256(path: Path | str, *, text: bool = True) -> str:
    """SHA-256 of a pinned file; ``text`` files are hashed with CRLF normalized to LF.

    Tracked text files differ byte for byte between a Windows working copy
    (``core.autocrlf=true``) and git or CI, so their pins use the LF form.
    Artifact and data files (JSON written by the code, parquet) are hashed
    as they are (``text=False``).
    """
    data = Path(path).read_bytes()
    if text:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return pin_sha256(path, text=True)


def spec() -> dict[str, Any]:
    """chain_v2 as data: what is unchanged from chain_v1 and the two changes (written to artifacts)."""
    return {
        "model": MODEL_NAME,
        "predecessor": PREDECESSOR,
        "decision": "D-098",
        "unchanged": {
            "config": CONFIG,
            "stage_choices": {
                "path": "ffml/models/chain/selection.json",
                "sha256": SELECTION_SHA256,
                "sha256_crlf_working_copy": SELECTION_SHA256_CRLF_FORM,
            },
            "features": {
                "version": FEATURE_VERSION,
                "manifest_pin": "tests/fixtures/features/feature_manifest_0.2.0.json",
                "manifest_sha256": FEATURE_MANIFEST_SHA256,
            },
            "hyperparameters": "ffml/models/chain (code unchanged)",
            "seed": SEED,
            "week_seed": "seed + 100*season + week",
            "n_sims": N_SIMS,
            "availability_config": AVAILABILITY_CONFIG,
        },
        "changes": {
            "a_training": "fit(cutoff of the predicted week): every regular-season row before it, "
            "2025 (released as data, not approved) and the 2026 weeks to date included",
            "b_calibration": {
                "mode": "walk_forward",
                "version": CALIBRATION_VERSION,
                "quantiles": QUANTILE_CALIBRATOR_REPR,
                "isotonic": ISOTONIC_CALIBRATOR_REPR,
                "history": f"(raw prediction, outcome) pairs of every week from {WARMUP_SEASON} "
                "week 1 through the week before the predicted week; games before its first kickoff",
                "as_applied_by": "data/backtests/default_20261102 (ffml.eval.harness.calibrated_rows)",
            },
        },
        "tuned": "nothing",
        "pin_hashing": "tracked text files: SHA-256 of LF-normalized bytes (CRLF -> LF); "
        "artifact and data files as they are",
    }


def check_pins(data: Any = None) -> dict[str, Any]:
    """Raise :class:`PinMismatch` unless every pinned input of chain_v2 is as validated.

    Checks the selection file hash, the feature version and schema
    fingerprint (the pinned manifest text and the repository pin file), the
    calibrator reprs and the chain defaults. ``data`` (a loaded ChainData)
    adds the feature version of the rows. Returns the checked values.
    """
    from ffml.config import PROJECT_ROOT
    from ffml.features.manifest import FEATURE_VERSION as MANIFEST_VERSION
    from ffml.features.manifest import fingerprint_json
    from ffml.models.chain.model import SELECTION_PATH, load_selection, resolve_choices

    problems = []
    sel_sha = _sha256_file(SELECTION_PATH)
    if sel_sha != SELECTION_SHA256:
        problems.append(f"selection.json sha256 {sel_sha} != {SELECTION_SHA256}")
    if MANIFEST_VERSION != FEATURE_VERSION:
        problems.append(f"feature version {MANIFEST_VERSION} != {FEATURE_VERSION}")
    fp_sha = hashlib.sha256(fingerprint_json().encode("utf-8")).hexdigest()
    if fp_sha != FEATURE_MANIFEST_SHA256:
        problems.append(f"feature manifest sha256 {fp_sha} != {FEATURE_MANIFEST_SHA256}")
    pin_file = PROJECT_ROOT / "tests" / "fixtures" / "features" / "feature_manifest_0.2.0.json"
    if pin_file.exists() and _sha256_file(pin_file) != FEATURE_MANIFEST_SHA256:
        problems.append(f"{pin_file.name} sha256 changed")
    if repr(quantile_calibrator()) != QUANTILE_CALIBRATOR_REPR:
        problems.append(f"quantile calibrator {quantile_calibrator()!r}")
    if repr(isotonic_calibrator()) != ISOTONIC_CALIBRATOR_REPR:
        problems.append(f"isotonic calibrator {isotonic_calibrator()!r}")
    from ffml.sim.predict import AVAILABILITY_CONFIG as LIVE_AVAILABILITY

    if LIVE_AVAILABILITY != AVAILABILITY_CONFIG:
        problems.append(f"availability config {LIVE_AVAILABILITY}")
    choices = resolve_choices(None)
    if choices != resolve_choices(load_selection()):
        problems.append("resolved stage choices differ from selection.json")
    if data is not None and data.feature_version not in (None, FEATURE_VERSION):
        problems.append(f"feature rows are version {data.feature_version}")
    if problems:
        raise PinMismatch("chain_v2 pins changed: " + "; ".join(problems))
    return {
        "selection_sha256": sel_sha,
        "feature_version": FEATURE_VERSION,
        "feature_manifest_sha256": fp_sha,
        "quantile_calibrator": QUANTILE_CALIBRATOR_REPR,
        "isotonic_calibrator": ISOTONIC_CALIBRATOR_REPR,
        "stage_choices": choices,
    }


def new_model(data: Any = None) -> Any:
    """The raw chain of chain_v2: ``ChainModel`` in the primary configuration with the v1 defaults."""
    from ffml.models.chain import ChainModel

    return ChainModel(config=CONFIG, stage_choice=None, n_sims=N_SIMS, seed=SEED, data=data)


def model_version(cutoff: tuple[int, int], now: datetime | None = None) -> str:
    """Registry version, the chain_v1 convention: ``chain_v2-primary-c2026w09-20261105T120000Z``."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{MODEL_NAME}-{CONFIG}-c{int(cutoff[0])}w{int(cutoff[1]):02d}-{stamp}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def save_artifact(
    model: Any,
    directory: Path | str,
    version: str,
    *,
    fit_seconds: float | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Write a fitted chain_v2 raw chain as an artifact (the ``ffml train`` layout plus the spec).

    Files: ``params.json`` and ``lgbm/*.txt`` (:meth:`ChainModel.save`),
    ``features.json``, ``selection.json``, ``train_manifest.json`` (model
    ``chain_v2``, the version, cutoff, feature snapshot ids, git, seeds,
    timings, training rows) and ``chain_v2.json`` (:func:`spec`).
    """
    from ffml.features.manifest import fingerprint
    from ffml.holdout import _git_sha
    from ffml.models.chain.model import SELECTION_PATH

    directory = Path(directory)
    if directory.exists():
        raise FileExistsError(f"artifact directory already exists: {directory}")
    cutoff = tuple(model.cutoff)
    model.save(directory)
    fp = fingerprint()
    fp_text = json.dumps(fp, sort_keys=True, separators=(",", ":"))
    _write_json(
        directory / "features.json",
        {
            "feature_version": model.data.feature_version,
            "manifest_feature_version": fp["feature_version"],
            "fingerprint_sha256": hashlib.sha256(fp_text.encode("utf-8")).hexdigest(),
            "fingerprint": fp,
            "lgbm_inputs": {k: v.features for k, v in model.lgbm.items()},
        },
    )
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    _write_json(
        directory / "selection.json", {"resolved_choices": model.choices, "selection": selection}
    )
    rows = {
        ds: model.data.train_rows(ds, cutoff).height
        for ds in ("team_week", "player_week", "dst_week")
    }
    seasons = sorted(
        {int(s) for s in model.data.train_rows("player_week", cutoff)["season"].unique().to_list()}
    )
    manifest = {
        "model_version": version,
        "model": MODEL_NAME,
        "raw_model_label": model.name,
        "config": model.config,
        "config_name": "chain_v2",
        "cutoff": list(cutoff),
        "train_seasons": seasons,
        "inputs": {"features": model.data.snapshot_ids},
        "git": _git_sha(),
        "seeds": {"model": model.seed, "lightgbm": model.seed, "n_sims": model.n_sims},
        "fit_seconds": round(fit_seconds, 2) if fit_seconds is not None else None,
        "timings": {k: round(v, 2) for k, v in model.timings.items()},
        "train_rows": rows,
        **dict(extra or {}),
    }
    _write_json(directory / "train_manifest.json", manifest)
    _write_json(directory / SPEC_FILE, spec())
    return directory


# --------------------------------------------------------------------------- #
# Calibrator state (exactly the online calibrators' fit for one week)
# --------------------------------------------------------------------------- #


def _before(season: int, week: int) -> pl.Expr:
    return (pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") < week))


def calibrator_state(
    history: pl.DataFrame,
    target: tuple[int, int],
    cutoff: Any,
    *,
    bounds: pl.DataFrame | None = None,
    meta: Mapping[str, Any] | None = None,
    quantiles: OnlinePITQuantile | None = None,
    isotonic: OnlineIsotonic | None = None,
) -> FrozenCalibration:
    """The walk-forward calibrator for week ``target`` as a :class:`FrozenCalibration`.

    ``history`` holds raw rows of one model with the harness outcome columns
    (``actual``, ``active``, ``boom_actual``, ``bust_actual``,
    ``kickoff_utc``, ``pos_min``, ``pos_max``) and ``scoring``. Only rows of
    weeks before ``target`` are read, and of those only games with
    ``kickoff_utc < cutoff`` (the target week's first kickoff) are fitted on,
    as in ``ffml.models.calibration._earlier``; rows of the target week or
    later, outcomes included, never reach the state. ``bounds`` (``scoring,
    position, pos_min, pos_max``) are stored for target rows without bound
    columns. Applying the state with ``FrozenCalibration.apply`` gives the
    rows that ``OnlinePITQuantile.transform`` followed by
    ``OnlineIsotonic.transform`` give for the target week.
    """
    q = quantiles or quantile_calibrator()
    iso = isotonic or isotonic_calibrator()
    season, week = int(target[0]), int(target[1])
    if "model" in history.columns and history["model"].n_unique() > 1:
        raise ValueError("calibrator_state takes the rows of one model; filter the frame first")
    missing = [
        c
        for c in ("season", "week", "position", "scoring", "actual", *q.columns)
        if c not in history.columns
    ]
    if missing:
        raise ValueError(f"history is missing columns {missing}")
    prior = history.filter(_before(season, week))
    q_maps: list[dict[str, Any]] = []
    q_pooled: list[dict[str, Any]] = []
    # every probability column gets an entry, empty ones too: the online calibrator writes
    # ``identity`` sources when no map can be fitted, and so does the applied state
    iso_maps: dict[str, dict[str, Any]] = {
        prob: {"maps": [], "pooled": [], "outcome": outcome}
        for prob, outcome in iso.present(history)
    }
    n_fit = 0
    used_weeks: set[tuple[int, int]] = set()
    for scoring in sorted({str(s) for s in prior["scoring"].unique().to_list()}):
        part = prior.filter(pl.col("scoring") == scoring)
        # PIT draws over every earlier row of the partition, as the online transform draws them
        u = q.pits(part, "pos_min", "pos_max")
        part = part.with_columns(pl.Series("_u", u, dtype=pl.Float64))
        hist = _earlier(part, season, week, cutoff)
        hu = hist.filter(pl.col("_u").is_not_null() & ~pl.col("_u").is_nan())
        n_fit += hu.height
        used_weeks |= {(int(s), int(w)) for s, w in hu.select("season", "week").unique().rows()}
        if hu.height >= q.min_count:
            q_pooled.append(
                {
                    "group": {"scoring": scoring},
                    "levels": q.level_map(hu["_u"].to_numpy()).tolist(),
                    "n": hu.height,
                }
            )
        for pos in sorted({str(p) for p in hu["position"].unique().to_list()}):
            values = hu.filter(pl.col("position") == pos)["_u"].to_numpy()
            if values.size < q.min_count:
                continue
            q_maps.append(
                {
                    "group": {"scoring": scoring, "position": pos},
                    "levels": q.level_map(values).tolist(),
                    "n": int(values.size),
                }
            )
        for prob, outcome in iso.present(hist):
            slot = iso_maps[prob]
            train_all = _calibration_rows(hist).filter(
                pl.col(prob).is_not_null() & pl.col(outcome).is_not_null()
            )
            if train_all.height >= iso.min_count:
                slot["pooled"].append(
                    _isotonic_entry(train_all, prob, outcome, {"scoring": scoring})
                )
            for pos in sorted({str(p) for p in train_all["position"].unique().to_list()}):
                sub = train_all.filter(pl.col("position") == pos)
                if sub.height < iso.min_count:
                    continue
                slot["maps"].append(
                    _isotonic_entry(sub, prob, outcome, {"scoring": scoring, "position": pos})
                )
    isotonic_spec = {
        prob: {
            "outcome": slot["outcome"],
            "keys": ["scoring", *iso.by],
            "pooled_keys": ["scoring"],
            "by": list(iso.by),
            "min_count": iso.min_count,
            "maps": slot["maps"],
            "pooled": slot["pooled"],
        }
        for prob, slot in iso_maps.items()
    }
    stored_bounds = (
        bounds.select(
            pl.col("scoring").cast(pl.String),
            pl.col("position").cast(pl.String),
            pl.col("pos_min").cast(pl.Float64),
            pl.col("pos_max").cast(pl.Float64),
        )
        .sort("scoring", "position")
        .to_dicts()
        if bounds is not None
        else []
    )
    weeks_sorted = sorted(used_weeks)
    info = {
        "calibration_version": CALIBRATION_VERSION,
        "mode": "walk_forward",
        "model": MODEL_NAME,
        "target": [season, week],
        "cutoff": str(cutoff) if cutoff is not None else None,
        "quantile_calibrator": repr(q),
        "isotonic": repr(iso),
        "rows": n_fit,
        "history_weeks": len(weeks_sorted),
        "history_first": list(weeks_sorted[0]) if weeks_sorted else None,
        "history_last": list(weeks_sorted[-1]) if weeks_sorted else None,
        "seasons": sorted({s for s, _ in weeks_sorted}),
        **dict(meta or {}),
    }
    return FrozenCalibration(
        levels=q.levels,
        by=q.by,
        keys=("scoring", *q.by),
        min_count=q.min_count,
        offsets=[],
        isotonic=isotonic_spec,
        meta=info,
        quantile_method=PIT_METHOD,
        quantile_maps={
            "keys": ["scoring", *q.by],
            "pooled_keys": ["scoring"],
            "maps": q_maps,
            "pooled": q_pooled,
            "seed": q.seed,
        },
        bounds=stored_bounds,
    )


def state_payload(state: FrozenCalibration) -> bytes:
    """The canonical bytes of a state as saved (``FrozenCalibration.save`` writes exactly these)."""
    return (json.dumps(state.to_dict(), indent=2) + "\n").encode("utf-8")


def state_sha256(state: FrozenCalibration) -> str:
    return hashlib.sha256(state_payload(state)).hexdigest()


def calibrate(
    state: FrozenCalibration, frame: pl.DataFrame, *, label: str | None = MODEL_NAME
) -> pl.DataFrame:
    """Apply a week's state to raw rows; ``calibration_version`` = ``walk_forward:<sha12>``.

    Bound columns ``pos_min``/``pos_max`` are used when the frame has them,
    else the stored bounds. ``label`` replaces the ``model`` column (calibrated
    rows are chain_v2; ``None`` keeps it).
    """
    has_bounds = "pos_min" in frame.columns and "pos_max" in frame.columns
    out = state.apply(
        frame,
        lower_bound="pos_min" if has_bounds else None,
        upper_bound="pos_max" if has_bounds else None,
    )
    out = out.with_columns(
        pl.lit(f"walk_forward:{state_sha256(state)[:12]}").alias("calibration_version")
    )
    if label is not None and "model" in out.columns:
        out = out.with_columns(pl.lit(label).alias("model"))
    return out


def online_reference(history: pl.DataFrame, target_rows: pl.DataFrame) -> pl.DataFrame:
    """The online transforms of the harness on ``history`` plus ``target_rows``, target week only.

    The reference that :func:`calibrator_state` must equal (tests and the
    identity check): ``OnlinePITQuantile.transform`` (bounds ``pos_min`` /
    ``pos_max``) then ``OnlineIsotonic.transform``, exactly as
    ``ffml.eval.harness.calibrated_rows``.
    """
    season = int(target_rows["season"][0])
    week = int(target_rows["week"][0])
    frame = pl.concat([history, target_rows], how="diagonal_relaxed")
    cal = quantile_calibrator().transform(frame, lower_bound="pos_min", upper_bound="pos_max")
    cal = isotonic_calibrator().transform(cal)
    return cal.filter((pl.col("season") == season) & (pl.col("week") == week))


def frames_equal(a: pl.DataFrame, b: pl.DataFrame, columns: list[str], keys: list[str]) -> dict:
    """Exact comparison of ``columns`` after sorting both by ``keys``; counts and max abs diffs."""
    a = a.sort(keys)
    b = b.sort(keys)
    out: dict[str, Any] = {"rows": [a.height, b.height], "columns": {}}
    if a.height != b.height or not a.select(keys).equals(b.select(keys)):
        out["equal"] = False
        out["keys_equal"] = False
        return out
    equal = True
    for c in columns:
        x, y = a[c], b[c]
        same = x.equals(y)
        rec: dict[str, Any] = {"equal": bool(same)}
        if not same and x.dtype.is_numeric():
            xa = x.cast(pl.Float64).to_numpy()
            ya = y.cast(pl.Float64).to_numpy()
            diff = np.abs(xa - ya)
            both_nan = np.isnan(xa) & np.isnan(ya)
            diff = np.where(both_nan, 0.0, diff)
            rec["n_diff"] = int(np.sum(diff > 0) + np.sum(np.isnan(diff)))
            rec["max_abs_diff"] = float(np.nanmax(diff)) if diff.size else 0.0
        elif not same:
            rec["n_diff"] = int((x != y).fill_null(True).sum())
        out["columns"][c] = rec
        equal = equal and same
    out["equal"] = equal
    out["keys_equal"] = True
    return out


__all__ = [
    "AVAILABILITY_CONFIG",
    "FEATURE_MANIFEST_SHA256",
    "ISOTONIC_CALIBRATOR_REPR",
    "MODEL_NAME",
    "N_SIMS",
    "PinMismatch",
    "QUANTILE_CALIBRATOR_REPR",
    "RAW_MODEL_LABEL",
    "SEED",
    "SELECTION_SHA256",
    "STATE_FILE",
    "WARMUP_SEASON",
    "calibrate",
    "calibrator_state",
    "check_pins",
    "frames_equal",
    "isotonic_calibrator",
    "model_version",
    "new_model",
    "online_reference",
    "pin_sha256",
    "quantile_calibrator",
    "save_artifact",
    "spec",
    "state_sha256",
]
