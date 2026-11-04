"""Named backtest configurations for ``ffml backtest --config <name>`` (harness in ``ffml/eval/harness.py``).

``default``
    The reported walk-forward: seasons 2021-2024, weeks 1-18, refit before
    every week, PPR, half-PPR and standard scoring. 2021 only warms up the
    online calibration; reported metrics cover 2022-2024 (``report_seasons``).
    Models: the five transparent baselines, the direct LightGBM challenger for
    each scoring, and the structural chain in its primary and final-report
    configurations when ``ffml.models.chain.ChainModel`` is importable. Models
    run in three worker processes.
``quick``
    Smoke test: 2023 weeks 5-8, one fit at week 5 (``refit_every = 4``), same
    models, one process.
``baselines``
    The default schedule with the baselines only.
``holdout_<season>`` / ``forward_<season + 1>`` (pre-registered, D-091 to D-096)
    The one-time holdout (the holdout season of the settings, weeks 1-18) and
    the forward check (the season after it, weeks 1-7, the weeks fully final in
    the frozen snapshot). Names and seasons come from ``settings.holdout_season``. Both use purpose
    ``holdout_eval`` for every read (one audited session, D-049), refit
    every week, the five baselines, the challenger per scoring and both
    chain configurations (built strictly: a chain that cannot be built stops
    the run), and FIXED calibration: each model's frozen calibrator from
    ``artifacts/calibration/FROZEN.json`` is applied unchanged, with no online
    fitting (D-095). They run only when the holdout guard allows the seasons
    they read; nothing here bypasses it.
``frozen_dryrun_2024``
    The same mechanics on 2024 weeks 1-3 with calibrators frozen on 2022-2023,
    whose manifest is passed to ``run_backtest(frozen_manifest=...)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ffml.config import get_settings

log = logging.getLogger(__name__)

SCORINGS: tuple[str, ...] = ("ppr", "half", "std")
#: the holdout and forward-check seasons come from settings, never from literals (holdout audit)
HOLDOUT_SEASON: int = get_settings().holdout_season
FORWARD_SEASON: int = HOLDOUT_SEASON + 1
CHAIN_CONFIGS: tuple[str, ...] = ("primary", "final_report")


def baseline_models(purpose: str = "backtest") -> list[Any]:
    from ffml.models.baselines import default_baselines

    return list(default_baselines(purpose=purpose))


def challenger_models(scorings: tuple[str, ...] = SCORINGS, purpose: str = "backtest") -> list[Any]:
    from ffml.models.challenger import DirectQuantileModel

    return [DirectQuantileModel(s, purpose=purpose) for s in scorings]


def chain_models(purpose: str | None = None, strict: bool = False) -> list[Any]:
    """``ChainModel`` in the primary and final-report configurations, if B1's chain is importable.

    ``purpose`` gives both models one shared ``ChainData`` that reads features
    under that purpose (the holdout configs use ``holdout_eval``); ``None``
    keeps the chain's own default data. Without ``strict``, an import or
    construction error (a chain package still being written) leaves the chain
    out with a warning; with ``strict`` (the pre-registered holdout runs) it
    is raised.
    """
    try:
        from ffml.models.chain import ChainModel
        from ffml.models.chain.data import ChainData
    except ImportError as exc:
        if strict:
            raise
        log.info("ChainModel not importable (%s); chain left out of the backtest", exc)
        return []
    except Exception as exc:  # noqa: BLE001 - a broken package must not stop the baselines
        if strict:
            raise
        log.warning("ChainModel import failed (%r); chain left out of the backtest", exc)
        return []
    data = ChainData(purpose=purpose) if purpose is not None else None
    models = []
    for config in CHAIN_CONFIGS:
        try:
            if data is not None:
                models.append(ChainModel(config=config, data=data))
            else:
                try:
                    models.append(ChainModel(config=config))
                except TypeError:
                    models.append(ChainModel(config))
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise
            log.warning("ChainModel(%r) could not be built (%r); left out", config, exc)
    return models


#: Chain ablations (docs/MODELING.md section 11; D-059 depth sensitivity).
CHAIN_ABLATIONS: tuple[dict[str, Any], ...] = (
    {"config": "primary", "ablation": "no_vegas"},
    {"config": "primary", "ablation": "no_ngs"},
    {"config": "primary", "player_features_dataset": "player_week_depth_same_week"},
)


def ablation_models() -> list[Any]:
    """Chain variants for the ablation run; compare raw rows against ``chain_v1-primary``."""
    from ffml.models.chain import ChainModel

    return [ChainModel(**kwargs) for kwargs in CHAIN_ABLATIONS]


@dataclass(frozen=True)
class BacktestConfig:
    name: str
    seasons: tuple[int, ...]
    weeks: tuple[int, ...]
    report_seasons: tuple[int, ...]
    refit_every: int = 1
    scorings: tuple[str, ...] = SCORINGS
    include_baselines: bool = True
    include_challenger: bool = True
    include_chain: bool = True
    n_jobs: int = 1
    purpose: str = "backtest"
    description: str = ""
    extra_models: Callable[[], list[Any]] | None = field(default=None, compare=False)
    #: ``online`` (walk-forward calibration) or ``frozen`` (FIXED, pre-registered calibrators)
    calibration: str = "online"
    #: frozen mode: the calibrator manifest (``None`` = ``artifacts/calibration/FROZEN.json``)
    frozen_manifest: str | None = None
    #: give the models this run's purpose and fail if the chain cannot be built
    strict_models: bool = False

    def build_models(self) -> list[Any]:
        models: list[Any] = []
        purpose = self.purpose if self.strict_models else "backtest"
        if self.include_baselines:
            models += baseline_models(purpose)
        if self.include_challenger:
            models += challenger_models(self.scorings, purpose)
        if self.include_chain:
            if self.strict_models:
                models += chain_models(purpose=self.purpose, strict=True)
            else:
                models += chain_models()
        if self.extra_models is not None:
            models += self.extra_models()
        return models


CONFIGS: dict[str, BacktestConfig] = {
    "default": BacktestConfig(
        name="default",
        seasons=(2021, 2022, 2023, 2024),
        weeks=tuple(range(1, 19)),
        report_seasons=(2022, 2023, 2024),
        refit_every=1,
        n_jobs=3,
        description="reported walk-forward, 2021 warm-up for calibration",
    ),
    "quick": BacktestConfig(
        name="quick",
        seasons=(2023,),
        weeks=(5, 6, 7, 8),
        report_seasons=(2023,),
        refit_every=4,
        n_jobs=1,
        description="smoke test",
    ),
    "baselines": BacktestConfig(
        name="baselines",
        seasons=(2021, 2022, 2023, 2024),
        weeks=tuple(range(1, 19)),
        report_seasons=(2022, 2023, 2024),
        include_challenger=False,
        include_chain=False,
        description="baselines only on the default schedule",
    ),
    "ablations": BacktestConfig(
        name="ablations",
        seasons=(2022, 2023, 2024),
        weeks=tuple(range(1, 19)),
        report_seasons=(2022, 2023, 2024),
        refit_every=1,
        include_baselines=False,
        include_challenger=False,
        include_chain=False,
        n_jobs=3,
        description="chain ablations (no_vegas, no_ngs, depth_same_week); compare raw rows "
        "with chain_v1-primary from the default run",
        extra_models=ablation_models,
    ),
    # ---- pre-registered holdout and forward check (D-091 to D-096) ---- #
    f"holdout_{HOLDOUT_SEASON}": BacktestConfig(
        name=f"holdout_{HOLDOUT_SEASON}",
        seasons=(HOLDOUT_SEASON,),
        weeks=tuple(range(1, 19)),
        report_seasons=(HOLDOUT_SEASON,),
        refit_every=1,
        n_jobs=3,
        purpose="holdout_eval",
        calibration="frozen",
        strict_models=True,
        description="one-time holdout season, FIXED frozen calibration",
    ),
    f"forward_{FORWARD_SEASON}": BacktestConfig(
        name=f"forward_{FORWARD_SEASON}",
        seasons=(FORWARD_SEASON,),
        weeks=tuple(range(1, 8)),
        report_seasons=(FORWARD_SEASON,),
        refit_every=1,
        n_jobs=3,
        purpose="holdout_eval",
        calibration="frozen",
        strict_models=True,
        description="forward check, the season after the holdout, weeks 1-7, FIXED frozen calibration",
    ),
    "frozen_dryrun_2024": BacktestConfig(
        name="frozen_dryrun_2024",
        seasons=(2024,),
        weeks=(1, 2, 3),
        report_seasons=(2024,),
        refit_every=1,
        n_jobs=3,
        purpose="backtest",
        calibration="frozen",
        strict_models=True,
        description="dry run of the frozen mechanics on 2024 weeks 1-3 (calibrators frozen on 2022-2023)",
    ),
}


def get_config(name: str) -> BacktestConfig:
    if name not in CONFIGS:
        raise ValueError(f"unknown backtest config {name!r}; choose from {sorted(CONFIGS)}")
    return CONFIGS[name]


__all__ = ["CONFIGS", "BacktestConfig", "chain_models", "get_config"]
