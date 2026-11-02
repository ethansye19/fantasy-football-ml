"""``ffml train``: fit the structural chain and save a versioned artifact.

``run_train(config_name)`` resolves a training configuration, fits
:class:`ffml.models.chain.ChainModel` at its cutoff and writes
``artifacts/models/chain/<model_version>/``:

- ``params.json``: everything :meth:`ChainModel.load` needs (stage choices,
  structural parameters, dispersion, constants), plus ``lgbm/<stage>.txt``
  LightGBM model files for the stages that use LightGBM;
- ``features.json``: feature version, the schema fingerprint of
  :func:`ffml.features.manifest.fingerprint` with its SHA-256, and the inputs
  of every LightGBM stage;
- ``selection.json``: the stage selection used (copy of
  ``ffml/models/chain/selection.json`` plus the resolved choices);
- ``train_manifest.json``: model version, configuration, cutoff, input
  feature snapshot ids, git sha, seeds, timings and training row counts.

Configurations: ``default`` (primary, cutoff 2024 week 19 = all of
2016-2024), ``final_report`` (same cutoff, final-report availability), or a
path to a JSON file with the keys ``config``, ``cutoff`` ([season, week]),
``n_sims`` and ``seed``. The 2025 holdout is never read (D-005, D-058).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from ffml.config import get_settings

log = logging.getLogger(__name__)

TRAIN_CONFIGS: dict[str, dict[str, Any]] = {
    "default": {"config": "primary", "cutoff": [2024, 19], "n_sims": 2000, "seed": 20261101},
    "primary": {"config": "primary", "cutoff": [2024, 19], "n_sims": 2000, "seed": 20261101},
    "final_report": {
        "config": "final_report",
        "cutoff": [2024, 19],
        "n_sims": 2000,
        "seed": 20261101,
    },
}


def resolve_config(config_name: str) -> dict[str, Any]:
    if config_name in TRAIN_CONFIGS:
        return dict(TRAIN_CONFIGS[config_name])
    path = Path(config_name)
    if path.exists():
        cfg = dict(TRAIN_CONFIGS["default"])
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
        return cfg
    raise ValueError(
        f"unknown training configuration {config_name!r}; choose {sorted(TRAIN_CONFIGS)}"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def run_train(
    config_name: str = "default", out_root: Path | str | None = None, data: Any = None
) -> Path:
    """Fit the chain for ``config_name`` and save the artifact; returns its directory.

    ``data`` (a :class:`ffml.models.chain.data.ChainData`) is for tests; by default the
    feature datasets are read through ``ffml.features.access``.
    """
    from ffml.features.manifest import fingerprint
    from ffml.holdout import _git_sha
    from ffml.models.chain import ChainModel
    from ffml.models.chain.model import SELECTION_PATH, model_version

    cfg = resolve_config(config_name)
    cutoff = (int(cfg["cutoff"][0]), int(cfg["cutoff"][1]))
    if cutoff[0] >= get_settings().holdout_season:
        raise ValueError(f"cutoff {cutoff} would train on the locked holdout season")
    t0 = time.perf_counter()
    model = ChainModel(
        config=cfg["config"], n_sims=int(cfg["n_sims"]), seed=int(cfg["seed"]), data=data
    )
    model.fit(cutoff)
    fit_seconds = time.perf_counter() - t0
    version = model_version(cfg["config"], cutoff)
    root = Path(out_root) if out_root is not None else get_settings().models_dir / "chain"
    directory = root / version
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
    selection = (
        json.loads(SELECTION_PATH.read_text(encoding="utf-8")) if SELECTION_PATH.exists() else {}
    )
    _write_json(
        directory / "selection.json", {"resolved_choices": model.choices, "selection": selection}
    )
    rows = {
        ds: model.data.train_rows(ds, cutoff).height
        for ds in ("team_week", "player_week", "dst_week")
    }
    _write_json(
        directory / "train_manifest.json",
        {
            "model_version": version,
            "model": model.name,
            "config": cfg["config"],
            "config_name": config_name,
            "cutoff": list(cutoff),
            "inputs": {"features": model.data.snapshot_ids},
            "git": _git_sha(),
            "seeds": {"model": model.seed, "lightgbm": model.seed, "n_sims": model.n_sims},
            "fit_seconds": round(fit_seconds, 2),
            "timings": {k: round(v, 2) for k, v in model.timings.items()},
            "train_rows": rows,
        },
    )
    log.info("trained %s in %.1fs -> %s", version, fit_seconds, directory)
    print(f"trained {version} in {fit_seconds:.1f}s -> {directory}")
    return directory


__all__ = ["TRAIN_CONFIGS", "resolve_config", "run_train"]
