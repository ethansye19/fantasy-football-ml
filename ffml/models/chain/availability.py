"""Availability: P(active) for every rostered player row (D-006, D-047, MODELING.md 9).

"Active" means the player recorded a snap or a stat in the game
(``y_played``), which is what the evaluation population uses.

Primary configuration (:class:`AvailabilityLogit`)
    A logistic regression per position on lagged information only: games
    missed in the team's last five, weeks since the player's last game (and
    whether that was the previous week), the previous game week's injury
    report status (one-hot) and weeks since last ruled out, depth rank and
    starter flag at the as-of time (D-059: legacy charts are the week w-1
    chart), last-game and EW snap share, games played this season and in the
    career, rookie flag. Inputs are standardised; nulls get documented fill
    values plus missing indicators. **No ``avail__`` column is read**: the
    frame is stripped of them before the model sees it, and
    :attr:`AvailabilityLogit.input_columns` lists what it reads.

Final-report configuration (:func:`final_report_availability`)
    The FEATURES.md section G rules, which describe injury availability, cap
    the primary participation probability (a healthy backup who rarely plays
    keeps his low probability): ``P = min(cap, P_primary)`` with
    cap 0 for Out or IR, 0.15 for Doubtful, the position's historical
    P(active | Questionable) among starters for Questionable, at most 0.5 for
    Questionable with no practice all week, 0 for roster status RES, PUP, NFI,
    SUS or CUT, 0.5 when absent from the same-week depth chart with a starter
    history (EW snap share at least 0.5), and 1 otherwise.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

AVAIL_PREFIX = "avail__"

#: (source column, transform, fill value for nulls, add a missing indicator)
LOGIT_INPUTS: tuple[tuple[str, str, float, bool], ...] = (
    ("attr__games_missed__l5", "id", 0.0, False),
    ("attr__weeks_since_last_game", "log_cap52", 52.0, True),
    ("attr__weeks_since_last_game", "eq1", 0.0, False),
    ("attr__inj_prev_status", "eq1", 0.0, False),
    ("attr__inj_prev_status", "eq2", 0.0, False),
    ("attr__inj_prev_status", "eq3", 0.0, False),
    ("attr__inj_weeks_since_out", "log_cap52", 52.0, False),
    ("depth__rank", "cap6", 7.0, True),
    ("depth__starter_flag", "id", 0.0, False),
    ("opp__snap_share__l1", "id", 0.0, True),
    ("opp__snap_share__ew", "id", 0.0, False),
    ("attr__games__std_n", "log1p", 0.0, False),
    ("attr__games__car_n", "log1p", 0.0, False),
    ("attr__rookie_flag", "id", 0.0, False),
)

REPORT_CAPS: dict[str, float] = {"Out": 0.0, "IR": 0.0, "Doubtful": 0.15}
ROSTER_ZERO: tuple[str, ...] = ("RES", "PUP", "NFI", "SUS", "CUT")
DNP_CAP = 0.5
DEPTH_ABSENT_CAP = 0.5
STARTER_SNAP = 0.5
DEFAULT_Q_RATE = 0.75


def strip_avail(df: pl.DataFrame) -> pl.DataFrame:
    """Drop every same-week ``avail__`` column (primary configuration)."""
    return df.select([c for c in df.columns if not c.startswith(AVAIL_PREFIX)])


def _transform(x: np.ndarray, how: str) -> np.ndarray:
    if how == "id":
        return x
    if how == "log1p":
        return np.log1p(np.maximum(x, 0))
    if how == "log_cap52":
        return np.log1p(np.clip(x, 0, 52))
    if how == "cap6":
        return np.minimum(x, 6)
    if how.startswith("eq"):
        return (x == float(how[2:])).astype(float)
    raise ValueError(how)


def design_matrix(df: pl.DataFrame) -> np.ndarray:
    cols = []
    for name, how, fill, indicator in LOGIT_INPUTS:
        if name in df.columns:
            raw = df[name].cast(pl.Float64).fill_null(np.nan).to_numpy()
        else:
            raw = np.full(df.height, np.nan)
        miss = np.isnan(raw)
        cols.append(_transform(np.where(miss, fill, raw), how))
        if indicator:
            cols.append(miss.astype(float))
    return np.column_stack(cols) if cols else np.zeros((df.height, 0))


class AvailabilityLogit:
    """Per-position logistic regression on lagged availability inputs (primary)."""

    input_columns: tuple[str, ...] = tuple(dict.fromkeys(n for n, *_ in LOGIT_INPUTS))

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = dict(params or {})

    def fit(self, df: pl.DataFrame) -> AvailabilityLogit:
        df = strip_avail(df)
        y = df["y_played"].cast(pl.Float64).to_numpy()
        pos = np.asarray(df["position"].to_list(), dtype=object)
        X = design_matrix(df)
        models: dict[str, Any] = {}
        for p in np.unique(pos):
            m = pos == p
            Xp, yp = X[m], y[m]
            mean = Xp.mean(axis=0)
            sd = Xp.std(axis=0)
            sd = np.where(sd > 1e-9, sd, 1.0)
            if np.unique(yp).size < 2:
                models[str(p)] = {"const": float(yp.mean())}
                continue
            lr = LogisticRegression(C=1.0, max_iter=2000)
            lr.fit((Xp - mean) / sd, yp)
            models[str(p)] = {
                "mean": mean.tolist(),
                "sd": sd.tolist(),
                "coef": lr.coef_[0].tolist(),
                "intercept": float(lr.intercept_[0]),
            }
        self.params = {"models": models, "base_rate": float(y.mean())}
        return self

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        df = strip_avail(df)
        X = design_matrix(df)
        pos = np.asarray(df["position"].to_list(), dtype=object)
        out = np.full(df.height, self.params.get("base_rate", 0.8))
        for p, m in self.params["models"].items():
            sel = pos == p
            if not sel.any():
                continue
            if "const" in m:
                out[sel] = m["const"]
                continue
            z = (X[sel] - np.asarray(m["mean"])) / np.asarray(m["sd"])
            out[sel] = 1.0 / (1.0 + np.exp(-(z @ np.asarray(m["coef"]) + m["intercept"])))
        return np.clip(out, 1e-4, 1 - 1e-4)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": "availability", "method": "availability_logit", "params": self.params}


def questionable_rates(df: pl.DataFrame) -> dict[str, float]:
    """P(active | Questionable) per position among starters (training window)."""
    if "avail__report_status" not in df.columns:
        return {}
    starter = (pl.col("depth__starter_flag").fill_null(0) == 1) | (
        pl.col("opp__snap_share__ew").fill_null(0) >= STARTER_SNAP
    )
    q = df.filter((pl.col("avail__report_status") == "Questionable") & starter)
    rates = q.group_by("position").agg(pl.col("y_played").mean().alias("rate"), pl.len())
    return {r["position"]: float(r["rate"]) for r in rates.iter_rows(named=True) if r["len"] >= 30}


def final_report_cap(df: pl.DataFrame, q_rates: dict[str, float]) -> np.ndarray:
    """Section G caps from same-week report, practice, roster and depth-chart inputs."""
    n = df.height

    def col(name: str) -> list[Any]:
        return df[name].to_list() if name in df.columns else [None] * n

    status, practice, roster = (
        col("avail__report_status"),
        col("avail__practice_status"),
        col("avail__roster_status"),
    )
    present = col("avail__depth_chart_present")
    snap = col("opp__snap_share__ew")
    pos = col("position")
    cap = np.ones(n)
    for i in range(n):
        c = 1.0
        s = status[i]
        if s in REPORT_CAPS:
            c = REPORT_CAPS[s]
        elif s == "Questionable":
            c = q_rates.get(pos[i], DEFAULT_Q_RATE)
            if practice[i] is not None and str(practice[i]).startswith("Did Not Participate"):
                c = min(c, DNP_CAP)
        if roster[i] in ROSTER_ZERO:
            c = 0.0
        if present[i] is not None and float(present[i]) == 0.0 and (snap[i] or 0.0) >= STARTER_SNAP:
            c = min(c, DEPTH_ABSENT_CAP)
        cap[i] = c
    return cap


def final_report_availability(
    df: pl.DataFrame, primary_prob: np.ndarray, q_rates: dict[str, float]
) -> np.ndarray:
    """``min(section G cap, primary probability)``."""
    return np.minimum(final_report_cap(df, q_rates), primary_prob)


__all__ = [
    "AVAIL_PREFIX",
    "AvailabilityLogit",
    "LOGIT_INPUTS",
    "final_report_availability",
    "final_report_cap",
    "questionable_rates",
    "strip_avail",
]
