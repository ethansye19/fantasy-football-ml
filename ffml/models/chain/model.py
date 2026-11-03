"""The structural chain as a :class:`ffml.models.protocol.WeeklyModel`.

``ChainModel(config="primary" | "final_report", stage_choice=None, n_sims=2000, seed=...)``

``fit(cutoff)``
    Trains every stage of :data:`ffml.models.chain.stages.STAGES` on
    regular-season rows strictly before ``cutoff`` (structural baselines
    always, LightGBM where the stage choice says so), estimates the
    simulation's dispersion parameters from training-window residuals
    (:mod:`ffml.models.chain.dispersion`) and the constants the parameter
    builder needs (kneel-downs, the unmodeled-receivers bucket, kicker band
    priors, DST coupling), all from the same rows.

``predict(season, week, scorings)``
    Predicts every stage for the week's rows, builds the engine's parameter
    frames (:mod:`ffml.models.chain.params_builder`), simulates ``n_sims``
    draws with a seed derived from ``(seed, season, week)``, and summarises
    per scoring (:mod:`ffml.models.chain.summary`) with drivers
    (:mod:`ffml.models.chain.drivers`). The frame passes
    :func:`ffml.models.protocol.validate_predictions`.

Stage choice: ``None`` reads ``selection.json`` next to this module (the
walk-forward selection, MODELING.md section 8); a string ``"structural"`` or
``"lgbm"`` forces every stage; a mapping overrides single stages (missing
stages are structural).

Configurations (MODELING.md section 9): ``primary`` never reads ``avail__``
columns; ``final_report`` caps availability with the same-week report rules
(:func:`ffml.models.chain.availability.final_report_availability`) and adds
the teammates-out inputs to LightGBM share and touchdown-share stages.

Ablations (MODELING.md section 11, :mod:`ffml.models.chain.ablation`):
``ablation="no_vegas" | "no_ngs" | "none"`` and ``exclude_features`` (column
prefixes) remove feature columns from every frame before any stage sees them;
``player_features_dataset="player_week_depth_same_week"`` reads the D-059
depth-timing sensitivity dataset. A variant's ``name`` encodes it, for example
``chain_v1-primary-no_vegas``. The defaults leave the model unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.models.chain import ablation as abl
from ffml.models.chain import drivers as drv
from ffml.models.chain import params_builder as pb
from ffml.models.chain.availability import (
    AvailabilityLogit,
    final_report_availability,
    questionable_rates,
    strip_avail,
)
from ffml.models.chain.data import ChainData
from ffml.models.chain.dispersion import estimate as estimate_dispersion
from ffml.models.chain.lgbm import LGBMStage
from ffml.models.chain.stages import STAGE_BY_NAME, STAGES, StageSpec
from ffml.models.chain.structural import StructuralStage
from ffml.models.chain.summary import MIN_ACTIVE_SIMS, QCOLS, summarize_chain
from ffml.models.protocol import validate_predictions
from ffml.scoring.rules import ScoringRules
from ffml.scoring.statline import FG_BUCKETS
from ffml.sim.engine import simulate
from ffml.sim.params import DEFAULT_FG_ATT_SHARE, DEFAULT_FG_MAKE_PROB

log = logging.getLogger(__name__)

MODEL_NAME = "chain_v1"
SELECTION_PATH = Path(__file__).with_name("selection.json")
DEFAULT_SEED = 20261101
DISPERSION_STAGES = (
    "team_plays",
    "team_pass_rate",
    "share_target",
    "share_carry",
    "share_dropback",
    "eff_ypr",
    "eff_ypc",
)
CONFIGS = ("primary", "final_report")


def load_selection(path: Path = SELECTION_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {k: v["choice"] if isinstance(v, dict) else v for k, v in payload["stages"].items()}


def resolve_choices(stage_choice: str | Mapping[str, str] | None) -> dict[str, str]:
    if stage_choice is None:
        base = load_selection()
    elif isinstance(stage_choice, str):
        if stage_choice not in ("structural", "lgbm"):
            raise ValueError("stage_choice string must be 'structural' or 'lgbm'")
        base = {s.name: stage_choice for s in STAGES}
    else:
        base = dict(stage_choice)
    out = {}
    for s in STAGES:
        c = base.get(s.name, "structural")
        out[s.name] = c if (c == "structural" or s.lgbm) else "structural"
    return out


def week_seed(seed: int, season: int, week: int) -> int:
    return int(seed) + int(season) * 100 + int(week)


class ChainModel:
    """Football-first chain: stage models, parameter builder, Monte Carlo, summaries."""

    def __init__(
        self,
        config: str = "primary",
        stage_choice: str | Mapping[str, str] | None = None,
        n_sims: int = 2000,
        seed: int = DEFAULT_SEED,
        data: ChainData | None = None,
        exclude_features: tuple[str, ...] = (),
        ablation: str = "none",
        player_features_dataset: str = abl.DEFAULT_PLAYER_DATASET,
    ) -> None:
        if config not in CONFIGS:
            raise ValueError(f"config must be one of {CONFIGS}")
        self.config = config
        self.choices = resolve_choices(stage_choice)
        self.n_sims = int(n_sims)
        self.seed = int(seed)
        self.ablation = ablation
        self.exclude_features = tuple(exclude_features)
        self.player_features_dataset = player_features_dataset
        self.exclusions = abl.resolve_exclusions(ablation, self.exclude_features)
        tokens = abl.variant_tokens(ablation, self.exclude_features, player_features_dataset)
        if tokens:
            self.name = f"{MODEL_NAME}-{config}-{'-'.join(tokens)}"
        else:
            self.name = MODEL_NAME if config == "primary" else f"{MODEL_NAME}_final_report"
        if data is not None and data.player_dataset != player_features_dataset:
            raise ValueError(
                f"data reads {data.player_dataset!r}, model wants {player_features_dataset!r}"
            )
        self.data = data or ChainData.shared(player_features_dataset)
        self.cutoff: tuple[int, int] | None = None
        self.structural: dict[str, Any] = {}
        self.lgbm: dict[str, LGBMStage] = {}
        self.dispersion: dict[str, Any] = {}
        self.consts: dict[str, Any] = {}
        self.q_rates: dict[str, float] = {}
        self.neutral_means: dict[str, float] = {}
        self.timings: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # fit
    # ------------------------------------------------------------------ #

    def stage_rows(self, spec: StageSpec, frame: pl.DataFrame) -> pl.DataFrame:
        df = frame
        if spec.dataset == "player_week":
            if spec.played_only:
                df = df.filter(pl.col("y_played") == 1.0)
            if spec.positions:
                df = df.filter(pl.col("position").is_in(list(spec.positions)))
        if spec.target not in df.columns:
            return df.head(0)
        df = df.filter(pl.col(spec.target).is_not_null() & pl.col(spec.target).is_finite())
        if spec.weight:
            df = df.filter(pl.col(spec.weight).fill_null(0) > 0)
        if self.config == "primary":
            df = strip_avail(df)
        return df

    def fit(self, cutoff: tuple[int, int]) -> ChainModel:
        t0 = time.perf_counter()
        cutoff = (int(cutoff[0]), int(cutoff[1]))
        self.data.ensure(cutoff[0])
        frames = {
            ds: abl.drop_excluded(self.data.train_rows(ds, cutoff), self.exclusions)
            for ds in ("team_week", "player_week", "dst_week")
        }
        self.timings["load"] = time.perf_counter() - t0
        self.structural, self.lgbm = {}, {}
        residuals: dict[str, pl.DataFrame] = {}
        t1 = time.perf_counter()
        for spec in STAGES:
            rows = self.stage_rows(spec, frames[spec.dataset])
            if rows.height == 0:
                log.warning("stage %s: no training rows before %s", spec.name, cutoff)
                continue
            if spec.structural == "availability_logit":
                st: Any = AvailabilityLogit().fit(rows)
            else:
                st = StructuralStage(spec).fit(rows)
            self.structural[spec.name] = st
            if self.choices[spec.name] == "lgbm":
                self.lgbm[spec.name] = LGBMStage(spec, config=self.config, seed=self.seed).fit(rows)
            if spec.name in DISPERSION_STAGES:
                if spec.name in self.lgbm and self.lgbm[spec.name].es_frame is not None:
                    residuals[spec.name] = self.lgbm[spec.name].es_frame
                else:
                    residuals[spec.name] = rows.with_columns(pl.Series("pred", st.predict(rows)))
        self.timings["stages"] = time.perf_counter() - t1
        self.dispersion = estimate_dispersion(residuals)
        self.consts = self._constants(frames)
        if abl.lines_excluded(self.exclusions):
            tw = frames["team_week"]
            mean_pts = float(tw["y_points"].mean() or 22.6) if tw.height else 22.6
            total_sd, margin_sd = abl.strength_residual_sds(tw, mean_pts)
            self.consts["strength"] = {
                "points_mean": mean_pts,
                "total_sd": total_sd,
                "margin_sd": margin_sd,
            }
        if self.config == "final_report":
            self.q_rates = questionable_rates(frames["player_week"])
        pw = frames["player_week"]
        self.neutral_means = {
            c: float(pw[c].cast(pl.Float64).mean() or 0.0)
            for c in pw.columns
            if c.startswith("def__") and pw[c].dtype.is_numeric()
        }
        self.cutoff = cutoff
        self.timings["fit_total"] = time.perf_counter() - t0
        return self

    @staticmethod
    def _constants(frames: dict[str, pl.DataFrame]) -> dict[str, Any]:
        tw, pw, dw = frames["team_week"], frames["player_week"], frames["dst_week"]
        played = pw.filter(pl.col("y_played") == 1.0)
        per_team = played.group_by(["game_id", "team"]).agg(
            pl.col("y_targets").fill_null(0).sum().alias("tgt"),
            pl.col("y_receptions").fill_null(0).sum().alias("rec"),
            pl.col("y_rec_yds").fill_null(0).sum().alias("ryd"),
            pl.col("y_completions").fill_null(0).sum().alias("cmp"),
            pl.col("y_pass_yds").fill_null(0).sum().alias("pyd"),
            pl.col("y_scrambles").fill_null(0).sum().alias("scr"),
            pl.col("y_touches").fill_null(0).sum().alias("touches"),
        )
        j = per_team.join(
            tw.select("game_id", "team", "y_pass_att", "y_rush_att", "y_dropbacks", "y_plays"),
            on=["game_id", "team"],
            how="inner",
        )
        out: dict[str, Any] = {}
        if j.height:
            att, tgt = float(j["y_pass_att"].sum()), float(j["tgt"].sum())
            cmp_, rec = float(j["cmp"].sum()), float(j["rec"].sum())
            out["unmodeled_tgt_share"] = float(np.clip(1 - tgt / max(att, 1), 0.0, 0.2))
            out["unmodeled_catch_rate"] = float(np.clip((cmp_ - rec) / max(att - tgt, 1), 0.0, 0.9))
            out["unmodeled_yards_per_rec"] = float(
                np.clip((float(j["pyd"].sum()) - float(j["ryd"].sum())) / max(cmp_ - rec, 1), 3, 20)
            )
            kneels = (j["y_rush_att"] + j["y_dropbacks"] - j["y_plays"] - j["scr"]).mean()
            out["kneels"] = float(np.clip(kneels or 0.0, 0.0, 2.0))
            out["touches_per_play"] = float(j["touches"].sum() / max(float(j["y_plays"].sum()), 1))
        has_line = tw.height and "ctx__total_line" in tw.columns
        out["total_line"] = float(tw["ctx__total_line"].mean() or 45.0) if has_line else 45.0
        if dw.height and "y_forced_fumbles" in dw.columns:
            d = dw.drop_nulls(["y_forced_fumbles", "y_fumble_recoveries"])
            x = d["y_fumble_recoveries"].to_numpy().astype(float)
            y = d["y_forced_fumbles"].to_numpy().astype(float)
            slope = float(np.cov(x, y)[0, 1] / np.var(x)) if x.size > 50 and np.var(x) > 0 else 0.84
            out["ff_per_fum_rec"] = float(np.clip(slope, 0.3, 1.0))
            out["dst_league"] = {
                "sack_rate": float(dw["def__dst_sack_rate__ew"].mean() or 0.065),
                "int_rate": float(dw["def__dst_int_rate__ew"].mean() or 0.023),
            }
            fr = dw["def__dst_fumble_recoveries__ew"].mean() or 0.52
            pf = dw["def__plays_faced__ew"].mean() or 62.6
            out["dst_league"]["fum_rate"] = float(fr / (pf * out.get("touches_per_play", 1.35)))
        k = played.filter(pl.col("position") == "K")
        share, make = {}, {}
        if k.height and all(f"y_fg_missed_{b}" in k.columns for b in FG_BUCKETS):
            atts = {
                b: float(
                    (k[f"y_fg_made_{b}"].fill_null(0) + k[f"y_fg_missed_{b}"].fill_null(0)).sum()
                )
                for b in FG_BUCKETS
            }
            total = sum(atts.values())
            for b in FG_BUCKETS:
                share[b] = atts[b] / total if total else DEFAULT_FG_ATT_SHARE[b]
                made = float(k[f"y_fg_made_{b}"].fill_null(0).sum())
                make[b] = made / atts[b] if atts[b] >= 20 else DEFAULT_FG_MAKE_PROB[b]
        out["fg_share_prior"] = share or dict(DEFAULT_FG_ATT_SHARE)
        out["fg_make_prior"] = make or dict(DEFAULT_FG_MAKE_PROB)
        if k.height:
            pat_att = float(k["y_pat_att"].fill_null(0).sum())
            out["xp_prior"] = (
                float(k["y_pat_made"].fill_null(0).sum() / pat_att) if pat_att else 0.94
            )
        return out

    # ------------------------------------------------------------------ #
    # stage predictions
    # ------------------------------------------------------------------ #

    def _predict_stage(self, name: str, df: pl.DataFrame) -> np.ndarray:
        if df.height == 0:
            return np.zeros(0)
        if self.choices.get(name) == "lgbm" and name in self.lgbm:
            return self.lgbm[name].predict(df)
        model = self.structural.get(name)
        if model is None:
            raise RuntimeError(f"stage {name} is not fitted")
        return model.predict(df)

    def stage_predictions(
        self, season: int, week: int, neutral: bool = False
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        """Week rows with ``p_<stage>`` columns (``neutral``: defense features at training means)."""
        if self.cutoff is None:
            raise RuntimeError("ChainModel.fit must be called before predict")
        ex = self.exclusions
        team = abl.drop_excluded(self.data.week_rows("team_week", season, week), ex)
        players = abl.drop_excluded(self.data.week_rows("player_week", season, week), ex)
        dst = abl.drop_excluded(self.data.week_rows("dst_week", season, week), ex)
        if neutral:
            players = players.with_columns(
                [
                    pl.lit(v, dtype=pl.Float32).alias(c)
                    for c, v in self.neutral_means.items()
                    if c in players.columns
                ]
            )
        model_players = players if self.config == "final_report" else strip_avail(players)
        out = {"team_week": team, "player_week": players, "dst_week": dst}
        for spec in STAGES:
            frame = model_players if spec.dataset == "player_week" else out[spec.dataset]
            pred = self._predict_stage(spec.name, frame)
            out[spec.dataset] = out[spec.dataset].with_columns(pl.Series(f"p_{spec.name}", pred))
        if self.config == "final_report":
            p = out["player_week"]
            prob = final_report_availability(p, p["p_availability"].to_numpy(), self.q_rates)
            out["player_week"] = p.with_columns(pl.Series("p_availability", prob))
        return out["team_week"], out["player_week"], out["dst_week"]

    def build_params(
        self, season: int, week: int, neutral: bool = False
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        team, players, dst = self.stage_predictions(season, week, neutral=neutral)
        strength = self.consts.get("strength")
        if strength is not None and abl.lines_excluded(self.exclusions):
            # no Vegas lines: the game draw is centred on lagged team strength; stage
            # predictions above never saw any line column
            team = abl.strength_lines(team, strength["points_mean"])
        tp = pb.build_team_params(team, self.dispersion, self.consts)
        if strength is not None and abl.lines_excluded(self.exclusions):
            tp = tp.with_columns(
                pl.lit(strength["total_sd"]).alias("total_sd"),
                pl.lit(strength["margin_sd"]).alias("margin_sd"),
            )
        pp = pb.build_player_params(players, tp, self.dispersion, self.consts, self.choices)
        dp = pb.build_dst_params(dst, tp, pp, self.consts, self.choices)
        return tp, pp, dp

    # ------------------------------------------------------------------ #
    # predict
    # ------------------------------------------------------------------ #

    def predict(self, season: int, week: int, scorings: Mapping[str, ScoringRules]) -> pl.DataFrame:
        t0 = time.perf_counter()
        tp, pp, dp = self.build_params(season, week)
        if tp.height == 0:
            return self._empty_frame()
        seed = week_seed(self.seed, season, week)
        sims = simulate(tp, pp, dp, n_sims=self.n_sims, seed=seed)
        counts = sims.group_by("entity_id").agg(pl.col("active").sum().alias("n"))
        forced_ids = counts.filter(pl.col("n") < MIN_ACTIVE_SIMS)["entity_id"].to_list()
        forced = None
        if forced_ids:
            pp_forced = pp.with_columns(
                pl.when(pl.col("entity_id").is_in(forced_ids))
                .then(1.0)
                .otherwise(pl.col("availability_prob"))
                .alias("availability_prob")
            )
            forced = simulate(tp, pp_forced, dp, n_sims=self.n_sims, seed=seed + 7)
        self.timings["simulate"] = time.perf_counter() - t0
        # drivers inputs (scoring-independent)
        _, pp_neutral, _ = self.build_params(season, week, neutral=True)
        act_in = drv.offense_inputs(pp, tp, dp, blend=True)
        neu_in = drv.offense_inputs(pp_neutral, tp, dp, blend=False)
        frames = []
        for label, rules in scorings.items():
            summary, scored = summarize_chain(sims, rules, forced, forced_ids)
            contrib = pl.concat(
                [
                    f
                    for f in (
                        drv.offense_drivers(act_in, neu_in, rules),
                        drv.kicker_drivers(pp, tp, rules),
                        drv.dst_drivers(
                            scored.filter(pl.col("position") == "DST").join(
                                sims.filter(pl.col("position") == "DST"),
                                on=["entity_id", "game_id", "sim"],
                                how="left",
                                suffix="_s",
                            ),
                            rules,
                        ),
                    )
                    if f.height
                ],
                how="vertical_relaxed",
            )
            js = drv.to_json(contrib)
            frames.append(
                summary.join(js, on=["entity_id", "game_id"], how="left").with_columns(
                    pl.lit(label).alias("scoring"), pl.lit(self.name).alias("model")
                )
            )
        out = pl.concat(frames, how="vertical_relaxed")
        out = self._finalise(out)
        self.timings["predict_total"] = time.perf_counter() - t0
        return validate_predictions(out)

    def _finalise(self, out: pl.DataFrame) -> pl.DataFrame:
        cols = [
            "entity_id",
            "position",
            "team",
            "game_id",
            "season",
            "week",
            "scoring",
            "model",
            "pred_mean",
            *QCOLS,
            "boom_prob",
            "bust_prob",
            "active_prob",
            "pred_mean_uncond",
            "pred_q50_uncond",
            "drivers",
        ]
        floats = [
            "pred_mean",
            *QCOLS,
            "boom_prob",
            "bust_prob",
            "active_prob",
            "pred_mean_uncond",
            "pred_q50_uncond",
        ]
        out = out.with_columns(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            *[pl.col(c).cast(pl.Float64) for c in floats],
        )
        # quantiles are monotone by construction; guard tiny interpolation noise
        q = out.select(QCOLS).to_numpy()
        q = np.maximum.accumulate(q, axis=1)
        out = out.with_columns([pl.Series(c, q[:, i]) for i, c in enumerate(QCOLS)])
        # total order (ties in pred_mean are common for zero-usage players)
        return out.select(cols).sort(
            ["scoring", "position", "pred_mean", "entity_id", "game_id"],
            descending=[False, False, True, False, False],
        )

    def _empty_frame(self) -> pl.DataFrame:
        schema = {
            "entity_id": pl.String,
            "position": pl.String,
            "team": pl.String,
            "game_id": pl.String,
            "season": pl.Int32,
            "week": pl.Int32,
            "scoring": pl.String,
            "model": pl.String,
            **{
                c: pl.Float64
                for c in (
                    "pred_mean",
                    *QCOLS,
                    "boom_prob",
                    "bust_prob",
                    "active_prob",
                    "pred_mean_uncond",
                    "pred_q50_uncond",
                )
            },
            "drivers": pl.String,
        }
        return pl.DataFrame(schema=schema)

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #

    def state(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "config": self.config,
            "cutoff": list(self.cutoff) if self.cutoff else None,
            "n_sims": self.n_sims,
            "seed": self.seed,
            "choices": self.choices,
            "structural": {k: v.to_dict() for k, v in self.structural.items()},
            "lgbm": {k: v.to_dict() for k, v in self.lgbm.items()},
            "dispersion": self.dispersion,
            "consts": self.consts,
            "q_rates": self.q_rates,
            "neutral_means": self.neutral_means,
            # variant keys only for ablations, so default artifacts are unchanged
            **(
                {
                    "ablation": self.ablation,
                    "exclude_features": list(self.exclude_features),
                    "player_features_dataset": self.player_features_dataset,
                }
                if self.name not in (MODEL_NAME, f"{MODEL_NAME}_final_report")
                else {}
            ),
        }

    def save(self, directory: Path | str) -> Path:
        directory = Path(directory)
        (directory / "lgbm").mkdir(parents=True, exist_ok=True)
        (directory / "params.json").write_text(
            json.dumps(self.state(), indent=2, default=float) + "\n", encoding="utf-8"
        )
        for name, stage in self.lgbm.items():
            (directory / "lgbm" / f"{name}.txt").write_text(stage.model_string(), encoding="utf-8")
        return directory

    @classmethod
    def load(cls, directory: Path | str, data: ChainData | None = None) -> ChainModel:
        directory = Path(directory)
        st = json.loads((directory / "params.json").read_text(encoding="utf-8"))
        model = cls(
            config=st["config"],
            stage_choice=st["choices"],
            n_sims=st["n_sims"],
            seed=st["seed"],
            data=data,
            exclude_features=tuple(st.get("exclude_features", ())),
            ablation=st.get("ablation", "none"),
            player_features_dataset=st.get("player_features_dataset", abl.DEFAULT_PLAYER_DATASET),
        )
        model.cutoff = tuple(st["cutoff"]) if st["cutoff"] else None
        for name, payload in st["structural"].items():
            if payload["method"] == "availability_logit":
                model.structural[name] = AvailabilityLogit(payload["params"])
            else:
                model.structural[name] = StructuralStage(STAGE_BY_NAME[name], payload["params"])
        for name, meta in st["lgbm"].items():
            text = (directory / "lgbm" / f"{name}.txt").read_text(encoding="utf-8")
            model.lgbm[name] = LGBMStage.from_saved(STAGE_BY_NAME[name], meta, text)
        model.dispersion = st["dispersion"]
        model.consts = st["consts"]
        model.q_rates = st.get("q_rates", {})
        model.neutral_means = st.get("neutral_means", {})
        return model


def model_version(config: str, cutoff: tuple[int, int]) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{MODEL_NAME}-{config}-c{cutoff[0]}w{cutoff[1]:02d}-{stamp}"


__all__ = ["ChainModel", "MODEL_NAME", "load_selection", "model_version", "resolve_choices"]
