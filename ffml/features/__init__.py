"""Point-in-time feature builders (docs/FEATURES.md, DECISIONS D-006, D-046, D-047).

Public surface: :data:`FEATURE_VERSION`, :data:`PARAMS`, :func:`feature_manifest`
and :func:`feature_names` (column contract), ``ffml.features.build``
(``run_features``, ``build_player_week``, ``build_team_week``,
``build_dst_week``), ``ffml.features.targets.future_targets`` and
``ffml.features.access`` (the holdout-guarded loader that model code uses).
"""

from __future__ import annotations

from typing import Any

from ffml.features.spec import FEATURE_VERSION, PARAMS

__all__ = ["FEATURE_VERSION", "PARAMS", "feature_manifest", "feature_names"]


def feature_manifest(dataset: str | None = None) -> Any:
    """Ordered column list of a feature dataset (see :mod:`ffml.features.manifest`)."""
    from ffml.features.manifest import feature_manifest as _fm

    return _fm(dataset)


def feature_names(dataset: str, config: str = "primary") -> list[str]:
    """Model input column names for ``config`` (see :mod:`ffml.features.manifest`)."""
    from ffml.features.manifest import feature_names as _fn

    return _fn(dataset, config)
