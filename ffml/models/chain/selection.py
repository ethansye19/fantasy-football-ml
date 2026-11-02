"""Walk-forward stage selection: structural baseline vs LightGBM (MODELING.md section 8).

For every validation season 2022-2024 the chain is refitted at weeks 2, 6, 10,
14 and 18 (every 4 weeks, to save time; the shipped backtest refits weekly)
on all regular-season rows before that week, and each stage's two candidates
predict the stage target for the following weeks up to the next refit. Rows
are the stage's own population (played rows of its positions for stages 2-4,
every rostered row for availability, all team or DST rows for team and DST
stages); targets are never seen by the predictors.

Per row loss: absolute error (weighted by the stage's denominator for rates
and shares), Poisson deviance for counts, log loss for availability. The
difference "LightGBM minus structural" is averaged with the stage weights,
and its 95% interval comes from a bootstrap that resamples whole weeks
(2,000 draws). LightGBM is chosen only when the whole interval is below zero.

Run ``python -m ffml.models.chain.selection`` to rewrite
``ffml/models/chain/selection.json`` and the report.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.models.chain.data import ChainData
from ffml.models.chain.model import SELECTION_PATH, ChainModel
from ffml.models.chain.stages import STAGES, StageSpec

log = logging.getLogger(__name__)

SEASONS = (2022, 2023, 2024)
REFIT_WEEKS = (2, 6, 10, 14, 18)
LAST_WEEK = 18
BOOTSTRAP = 2000


def row_loss(spec: StageSpec, y: np.ndarray, p: np.ndarray) -> np.ndarray:
    if spec.metric == "mae":
        return np.abs(y - p)
    if spec.metric == "poisson":
        p = np.maximum(p, 1e-6)
        term = np.where(y > 0, y * np.log(np.where(y > 0, y, 1.0) / p), 0.0)
        return 2.0 * (term - (y - p))
    if spec.metric == "logloss":
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p))
    raise ValueError(spec.metric)


def stage_weight(spec: StageSpec, rows: pl.DataFrame) -> np.ndarray:
    if spec.metric == "mae" and spec.weight and spec.kind in ("rate", "positive"):
        return rows[spec.weight].cast(pl.Float64).fill_null(0.0).to_numpy()
    return np.ones(rows.height)


def evaluate_block(model: ChainModel, season: int, weeks: list[int]) -> list[dict[str, Any]]:
    """Per stage and week: summed losses of both candidates and the weight."""
    out: list[dict[str, Any]] = []
    for spec in STAGES:
        if spec.name not in model.structural:
            continue
        for week in weeks:
            frame = model.data.week_rows(spec.dataset, season, week)
            rows = model.stage_rows(spec, frame)
            if rows.height == 0:
                continue
            y = rows[spec.target].cast(pl.Float64).to_numpy()
            w = stage_weight(spec, rows)
            ps = model.structural[spec.name].predict(rows)
            rec: dict[str, Any] = {
                "stage": spec.name,
                "season": season,
                "week": week,
                "n": rows.height,
                "w": float(w.sum()),
                "loss_structural": float(np.sum(w * row_loss(spec, y, ps))),
            }
            if spec.name in model.lgbm:
                pg = model.lgbm[spec.name].predict(rows)
                rec["loss_lgbm"] = float(np.sum(w * row_loss(spec, y, pg)))
            out.append(rec)
    return out


def bootstrap_diff(
    weekly: pl.DataFrame, rng: np.random.Generator, draws: int = BOOTSTRAP
) -> tuple[float, float, float]:
    d = (weekly["loss_lgbm"] - weekly["loss_structural"]).to_numpy()
    w = weekly["w"].to_numpy()
    point = float(d.sum() / w.sum())
    idx = rng.integers(0, d.size, size=(draws, d.size))
    boots = d[idx].sum(axis=1) / w[idx].sum(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return point, float(lo), float(hi)


def summarise(records: list[dict[str, Any]], seed: int = 0) -> dict[str, Any]:
    df = pl.DataFrame(records)
    rng = np.random.default_rng(seed)
    stages: dict[str, Any] = {}
    for spec in STAGES:
        sub = df.filter(pl.col("stage") == spec.name)
        if sub.height == 0:
            continue
        entry: dict[str, Any] = {
            "metric": spec.metric,
            "structural_method": spec.structural,
            "n_rows": int(sub["n"].sum()),
            "n_weeks": sub.height,
            "structural": float(sub["loss_structural"].sum() / sub["w"].sum()),
        }
        if spec.lgbm and "loss_lgbm" in sub.columns and sub["loss_lgbm"].null_count() == 0:
            entry["lgbm"] = float(sub["loss_lgbm"].sum() / sub["w"].sum())
            point, lo, hi = bootstrap_diff(sub, rng)
            entry["diff"] = point
            entry["ci"] = [lo, hi]
            entry["choice"] = "lgbm" if hi < 0 else "structural"
        else:
            entry["choice"] = "structural"
            entry["note"] = "no LightGBM candidate"
        stages[spec.name] = entry
    return stages


def run_selection(
    seasons: tuple[int, ...] = SEASONS,
    refit_weeks: tuple[int, ...] = REFIT_WEEKS,
    seed: int = 20261101,
) -> dict[str, Any]:
    data = ChainData(purpose="validate")
    records: list[dict[str, Any]] = []
    fit_times: list[float] = []
    for season in seasons:
        for i, start in enumerate(refit_weeks):
            end = refit_weeks[i + 1] - 1 if i + 1 < len(refit_weeks) else LAST_WEEK
            weeks = list(range(start, end + 1))
            t0 = time.perf_counter()
            model = ChainModel(stage_choice="lgbm", seed=seed, data=data).fit((season, start))
            fit_times.append(time.perf_counter() - t0)
            records += evaluate_block(model, season, weeks)
            log.info("selection %s weeks %s: fit %.1fs", season, weeks, fit_times[-1])
            print(
                f"selection {season} weeks {weeks[0]}-{weeks[-1]}: fit {fit_times[-1]:.1f}s",
                flush=True,
            )
    stages = summarise(records, seed)
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "feature_version": data.feature_version,
        "seasons": list(seasons),
        "refit_weeks": list(refit_weeks),
        "refit_every_weeks": 4,
        "bootstrap": {"draws": BOOTSTRAP, "cluster": "week", "level": 0.95},
        "rule": "LightGBM only when the 95% CI of (LightGBM - structural) loss is entirely below 0",
        "fit_seconds_mean": float(np.mean(fit_times)) if fit_times else None,
        "stages": stages,
        "records": records,
    }


def write_outputs(result: dict[str, Any], selection_path: Path, report_path: Path) -> None:
    slim = {k: v for k, v in result.items() if k != "records"}
    selection_path.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.with_suffix(".records.json").write_text(
        json.dumps(result["records"]) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", default=str(SELECTION_PATH))
    parser.add_argument("--records", default="artifacts/selection/stage_selection.json")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    result = run_selection()
    write_outputs(result, Path(args.selection), Path(args.records))
    for name, e in result["stages"].items():
        print(name, e.get("choice"), round(e["structural"], 5), e.get("lgbm"), e.get("ci"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["run_selection", "row_loss", "summarise", "bootstrap_diff", "write_outputs"]
