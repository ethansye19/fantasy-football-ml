"""Feature manifest: the ordered column list (names, dtypes, kinds) of every dataset.

The manifest is generated from the same stat specifications the builders use,
so it is available without data. ``tests/features/test_leakage.py`` pins it
to ``tests/fixtures/features/feature_manifest_<version>.json`` (schema
fingerprint, FEATURES.md section J test 6) and checks that every built frame
matches it column for column.

Column kinds: ``key`` (identifiers and the cutoff/as-of times), ``meta``
(timestamps of the information used, for audits; never model inputs),
``feature`` (primary-configuration model features), ``sensitivity`` (actual
weather; ablations only), ``avail`` (same-week injury and roster inputs for
the availability layer and the final-report variant), ``target`` (``y_``).

Run ``python -m ffml.features.manifest <path>`` to write the pinned JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

from ffml.features.context import avail_cols, context_cols, depth_cols, injury_cols
from ffml.features.dst import dst_cols
from ffml.features.player import attr_cols, eff_cols, opp_cols, team_out_cols, teammates_out_cols
from ffml.features.spec import DATASETS, FEATURE_VERSION, PARAMS, TARGET_DTYPE, Col
from ffml.features.targets import KEYS, TARGET_NAMES
from ffml.features.team import def_cols, team_cols

DTYPES: dict[str, pl.DataType] = {
    "Float32": pl.Float32(),
    "Float64": pl.Float64(),
    "String": pl.String(),
    "Int32": pl.Int32(),
    "Boolean": pl.Boolean(),
    "Datetime[us, UTC]": pl.Datetime("us", "UTC"),
}

KEY_DTYPES: dict[str, str] = {
    "player_id": "String",
    "season": "Int32",
    "week": "Int32",
    "game_id": "String",
    "team": "String",
    "opponent": "String",
    "position": "String",
    "game_type": "String",
    "home": "Boolean",
    "kickoff_utc": "Datetime[us, UTC]",
    "cutoff_ts": "Datetime[us, UTC]",
    "asof_ts": "Datetime[us, UTC]",
    "feature_version": "String",
}

META: dict[str, list[tuple[str, str]]] = {
    "player_week": [
        ("meta__last_game_kickoff", "player's last game used (kickoff)"),
        ("meta__team_last_game_kickoff", "team offense's last game used"),
        ("meta__def_last_game_kickoff", "opponent defense's last game used"),
        ("meta__qb_depth_snapshot_ts", "depth snapshot used for the team's QB1"),
        ("meta__depth_snapshot_ts", "depth snapshot used for the player's rank"),
        ("meta__inj_info_ts", "latest injury report time used by lagged injury features"),
    ],
    "team_week": [
        ("meta__team_last_game_kickoff", "team offense's last game used"),
        ("meta__def_last_game_kickoff", "opponent defense's last game used"),
        ("meta__qb_depth_snapshot_ts", "depth snapshot used for the team's QB1"),
    ],
    "dst_week": [
        ("meta__team_last_game_kickoff", "opponent offense's last game used"),
        ("meta__def_last_game_kickoff", "own defense's last game used"),
        ("meta__dst_last_game_kickoff", "own DST unit's last game used"),
        ("meta__qb_depth_snapshot_ts", "depth snapshot used for the opponent's QB1"),
    ],
}


def _keys(dataset: str) -> list[Col]:
    names = [*KEYS[dataset], "cutoff_ts", "asof_ts", "feature_version"]
    return [Col(name=n, dtype=KEY_DTYPES[n], kind="key") for n in names]


def _meta(dataset: str) -> list[Col]:
    return [
        Col(name=n, dtype="Datetime[us, UTC]", kind="meta", note=note) for n, note in META[dataset]
    ]


def _targets(dataset: str) -> list[Col]:
    return [Col(name=n, dtype=TARGET_DTYPE, kind="target") for n in TARGET_NAMES[dataset]]


def _split(cols: list[Col]) -> tuple[list[Col], list[Col]]:
    sens = [c for c in cols if c.kind == "sensitivity"]
    return [c for c in cols if c.kind != "sensitivity"], sens


def columns(dataset: str) -> list[Col]:
    """Ordered output columns of ``dataset``."""
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}; expected one of {DATASETS}")
    ctx_feat, ctx_sens = _split(context_cols())
    if dataset == "player_week":
        feats = [
            *ctx_feat,
            *team_cols(),
            *def_cols(),
            *opp_cols(),
            *eff_cols(),
            *attr_cols(),
            *injury_cols(),
            *depth_cols(),
        ]
        avail = [*avail_cols(), *teammates_out_cols()]
    elif dataset == "team_week":
        feats = [*ctx_feat, *team_cols(), *def_cols()]
        avail = team_out_cols()
    else:
        feats = [*ctx_feat, *team_cols(), *def_cols(), *dst_cols()]
        avail = team_out_cols()
    return [*_keys(dataset), *_meta(dataset), *feats, *ctx_sens, *avail, *_targets(dataset)]


def feature_manifest(dataset: str | None = None) -> Any:
    """Ordered column list with dtypes and kinds.

    ``feature_manifest("player_week")`` returns a list of dicts; with no
    argument, a dict ``{"feature_version", "params", "datasets": {name: list}}``.
    """
    if dataset is not None:
        return [c.to_dict() for c in columns(dataset)]
    return {
        "feature_version": FEATURE_VERSION,
        "params": PARAMS,
        "datasets": {d: [c.to_dict() for c in columns(d)] for d in DATASETS},
    }


def feature_names(dataset: str, config: str = "primary") -> list[str]:
    """Model input columns: ``primary`` (features only), ``final_report`` (+ avail), ``sensitivity``."""
    kinds = {
        "primary": {"feature"},
        "final_report": {"feature", "avail"},
        "sensitivity": {"feature", "sensitivity"},
    }
    if config not in kinds:
        raise ValueError(f"unknown config {config!r}; expected one of {sorted(kinds)}")
    allowed = kinds[config]
    return [c.name for c in columns(dataset) if c.kind in allowed]


def polars_schema(dataset: str) -> dict[str, pl.DataType]:
    return {c.name: DTYPES[c.dtype] for c in columns(dataset)}


def dtype_name(dtype: pl.DataType) -> str:
    """Manifest name of a Polars dtype (inverse of ``DTYPES``)."""
    for name, dt in DTYPES.items():
        if dtype == dt:
            return name
    return str(dtype)


def fingerprint() -> dict[str, Any]:
    """Schema fingerprint: ``[name, dtype, kind]`` per column, in order, per dataset."""
    return {
        "feature_version": FEATURE_VERSION,
        "datasets": {d: [[c.name, c.dtype, c.kind] for c in columns(d)] for d in DATASETS},
    }


def fingerprint_json() -> str:
    """The pinned-file text: one column per line so diffs show exactly what changed."""
    fp = fingerprint()
    lines = ["{", f'  "feature_version": {json.dumps(fp["feature_version"])},', '  "datasets": {']
    names = list(fp["datasets"])
    for i, d in enumerate(names):
        lines.append(f"    {json.dumps(d)}: [")
        rows = fp["datasets"][d]
        for j, row in enumerate(rows):
            lines.append(f"      {json.dumps(row)}{',' if j < len(rows) - 1 else ''}")
        lines.append(f"    ]{',' if i < len(names) - 1 else ''}")
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def write_manifest(path: Path | str) -> Path:
    """Write the pinned schema fingerprint (see :func:`fingerprint_json`)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fingerprint_json(), encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    target = argv[0] if argv else f"tests/fixtures/features/feature_manifest_{FEATURE_VERSION}.json"
    print(write_manifest(target))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DTYPES",
    "columns",
    "dtype_name",
    "feature_manifest",
    "feature_names",
    "fingerprint",
    "fingerprint_json",
    "polars_schema",
    "write_manifest",
]
