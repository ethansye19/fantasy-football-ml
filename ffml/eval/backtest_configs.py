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

The holdout season is never part of a configuration (D-005, D-058).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

SCORINGS: tuple[str, ...] = ("ppr", "half", "std")
CHAIN_CONFIGS: tuple[str, ...] = ("primary", "final_report")


def baseline_models() -> list[Any]:
    from ffml.models.baselines import default_baselines

    return list(default_baselines())


def challenger_models(scorings: tuple[str, ...] = SCORINGS) -> list[Any]:
    from ffml.models.challenger import DirectQuantileModel

    return [DirectQuantileModel(s) for s in scorings]


def chain_models() -> list[Any]:
    """``ChainModel`` in the primary and final-report configurations, if B1's chain is importable.

    Any import or construction error (a chain package still being written)
    leaves the chain out with a warning instead of failing the backtest.
    """
    try:
        from ffml.models.chain import ChainModel
    except ImportError as exc:
        log.info("ChainModel not importable (%s); chain left out of the backtest", exc)
        return []
    except Exception as exc:  # noqa: BLE001 - a broken package must not stop the baselines
        log.warning("ChainModel import failed (%r); chain left out of the backtest", exc)
        return []
    models = []
    for config in CHAIN_CONFIGS:
        try:
            try:
                models.append(ChainModel(config=config))
            except TypeError:
                models.append(ChainModel(config))
        except Exception as exc:  # noqa: BLE001
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

    def build_models(self) -> list[Any]:
        models: list[Any] = []
        if self.include_baselines:
            models += baseline_models()
        if self.include_challenger:
            models += challenger_models(self.scorings)
        if self.include_chain:
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
}


def get_config(name: str) -> BacktestConfig:
    if name not in CONFIGS:
        raise ValueError(f"unknown backtest config {name!r}; choose from {sorted(CONFIGS)}")
    return CONFIGS[name]


__all__ = ["CONFIGS", "BacktestConfig", "chain_models", "get_config"]
