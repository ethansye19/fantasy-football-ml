"""Vectorized point-in-time window helpers (no Python loops per entity).

The computation has two halves:

1. :func:`compute_state` walks each entity's games in kickoff order and emits
   one *state* row per game holding every window **including** that game (L1,
   L3, L5, EW, career sums, season-to-date sums). States use Polars window
   expressions (``rolling_sum``, ``ewm_mean``, ``cum_sum`` over the entity).
2. :func:`asof_join` attaches to each feature row the state of the entity's
   last game with ``kickoff_utc < cutoff_ts`` (strict as-of join), so the
   current game, and any game of the same week, can never contribute
   (D-046). :func:`finalize` then turns the joined sums into the published
   columns: season-to-date masking and the cross-season blend with the prior
   season, empirical-Bayes shrinkage toward priors from earlier seasons only,
   sample sizes, and the team-change reset for share statistics.

Float sums are always taken as ordered cumulative sums over sorted frames so
the result does not depend on thread partitioning; this keeps the truncation
and future-week leakage tests byte-exact.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from ffml.features.spec import PARAMS, Stat, fname


def ratio(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    """``num / den`` where ``den > 0``, else null."""
    return pl.when(den > 0).then(num / den).otherwise(None)


def _c(tag: str, part: str, stat: str) -> str:
    return f"_{tag}_{part}_{stat}"


def ts_col(tag: str) -> str:
    """Name of the joined state's game kickoff column."""
    return f"_{tag}_ts"


def season_col(tag: str) -> str:
    return f"_{tag}_season"


def _num_den(s: Stat) -> tuple[pl.Expr, pl.Expr]:
    num = pl.col(s.num).cast(pl.Float64)
    if s.den is None:
        valid = num.is_not_null()
        den = pl.lit(1.0, dtype=pl.Float64)
    else:
        den = pl.col(s.den).cast(pl.Float64)
        valid = num.is_not_null() & den.is_not_null()
    return (
        pl.when(valid).then(num).otherwise(0.0),
        pl.when(valid).then(den).otherwise(0.0),
    )


def _needs(s: Stat, window: str) -> bool:
    return window in s.windows or window in s.shrink or window in s.n_windows


def compute_state(
    events: pl.DataFrame,
    entity: Sequence[str],
    stats: Sequence[Stat],
    group: str,
    tag: str,
    *,
    reset_by: Sequence[str] = (),
    carry: Sequence[str] = (),
) -> pl.DataFrame:
    """One state row per entity game with all windows up to and including that game.

    ``events`` needs ``entity`` columns, ``season`` and ``kickoff_utc`` plus the
    stat source columns. For stats with ``reset=True`` the rolling, EW and
    season-to-date windows are partitioned by ``entity + reset_by`` (a team
    stint), while career sums stay at the entity level. ``carry`` columns are
    copied from the game row into the state unchanged.
    """
    entity = list(entity)
    alpha = float(PARAMS["ew_alpha"])
    ev = events.sort([*entity, "kickoff_utc"])
    nd: list[pl.Expr] = []
    for s in stats:
        n, d = _num_den(s)
        nd += [n.alias(_c(tag, "n", s.name)), d.alias(_c(tag, "d", s.name))]
    ev = ev.with_columns(nd)

    exprs: list[pl.Expr] = []
    for s in stats:
        part = [*entity, *reset_by] if s.reset else entity
        n = pl.col(_c(tag, "n", s.name))
        d = pl.col(_c(tag, "d", s.name))
        if "l1" in s.windows:
            exprs.append(ratio(n, d).alias(fname(group, s.name, "l1")))
        for size in (3, 5):
            if f"l{size}" in s.windows:
                rn = n.rolling_sum(size, min_samples=1).over(part)
                rd = d.rolling_sum(size, min_samples=1).over(part)
                exprs.append(ratio(rn, rd).alias(fname(group, s.name, f"l{size}")))
        if _needs(s, "ew"):
            # adjust=True weights (1 - alpha)^i normalised by their sum W_t; the
            # un-normalised sums needed for shrinkage are mean * W_t.
            gidx = n.cum_count().over(part).cast(pl.Float64)
            wsum = (1.0 - (1.0 - alpha) ** gidx) / alpha
            en = n.ewm_mean(alpha=alpha, adjust=True).over(part)
            ed = d.ewm_mean(alpha=alpha, adjust=True).over(part)
            if "ew" in s.windows:
                exprs.append(ratio(en, ed).alias(fname(group, s.name, "ew")))
            if "ew" in s.shrink or "ew" in s.n_windows:
                exprs += [
                    (en * wsum).alias(_c(tag, "ewn", s.name)),
                    (ed * wsum).alias(_c(tag, "ewd", s.name)),
                ]
        if _needs(s, "car"):
            cn = n.cum_sum().over(entity)
            cd = d.cum_sum().over(entity)
            exprs += [cn.alias(_c(tag, "cn", s.name)), cd.alias(_c(tag, "cd", s.name))]
            if "car" in s.windows:
                exprs.append(ratio(cn, cd).alias(fname(group, s.name, "car")))
        if _needs(s, "std"):
            exprs += [
                n.cum_sum().over([*part, "season"]).alias(_c(tag, "sn", s.name)),
                d.cum_sum().over([*part, "season"]).alias(_c(tag, "sd", s.name)),
            ]
    return ev.select(
        *entity,
        pl.col("kickoff_utc").alias(ts_col(tag)),
        pl.col("season").alias(season_col(tag)),
        *carry,
        *exprs,
    )


def season_totals(
    events: pl.DataFrame,
    entity: Sequence[str],
    stats: Sequence[Stat],
    tag: str,
) -> pl.DataFrame:
    """Per entity and season totals of every stat, keyed to the *following* season.

    Joined on ``(entity, season)`` this yields the prior-season (PS) window.
    Also returns ``_<tag>_ps_games`` (games in that season).
    """
    entity = list(entity)
    ev = events.sort([*entity, "season", "kickoff_utc"])
    nd: list[pl.Expr] = []
    for s in stats:
        n, d = _num_den(s)
        part = [*entity, "season"]
        nd += [
            n.cum_sum().over(part).alias(_c(tag, "psn", s.name)),
            d.cum_sum().over(part).alias(_c(tag, "psd", s.name)),
        ]
    ev = ev.with_columns(
        *nd, pl.col("kickoff_utc").cum_count().over([*entity, "season"]).alias(f"_{tag}_ps_games")
    )
    keep = [c for c in ev.columns if c.startswith(f"_{tag}_ps")]
    out = ev.select(*entity, "season", *keep).unique(
        subset=[*entity, "season"], keep="last", maintain_order=True
    )
    return out.with_columns((pl.col("season") + 1).cast(pl.Int32).alias("season"))


def priors(
    events: pl.DataFrame,
    group_cols: Sequence[str],
    stats: Sequence[Stat],
    tag: str,
    order: Sequence[str],
) -> pl.DataFrame:
    """Cumulative (inclusive) totals per prior group and season for shrinkage targets.

    Joined with :func:`join_priors` (strictly earlier seasons) this gives a prior
    that never contains same-season information. ``order`` must make the sort
    total (e.g. ``["kickoff_utc", "player_id"]``) so float sums are reproducible.
    """
    group_cols = list(group_cols)
    shrinkable = [s for s in stats if s.shrink]
    if not shrinkable:
        return pl.DataFrame()
    ev = events.sort([*group_cols, "season", *order])
    cols: list[pl.Expr] = []
    for s in shrinkable:
        n, d = _num_den(s)
        part = [*group_cols, "season"]
        cols += [
            n.cum_sum().over(part).alias(_c(tag, "prn", s.name)),
            d.cum_sum().over(part).alias(_c(tag, "prd", s.name)),
        ]
    ev = ev.with_columns(cols)
    keep = [c for c in ev.columns if c.startswith(f"_{tag}_pr")]
    per_season = ev.select(*group_cols, "season", *keep).unique(
        subset=[*group_cols, "season"], keep="last", maintain_order=True
    )
    per_season = per_season.sort([*group_cols, "season"])
    return per_season.with_columns([pl.col(c).cum_sum().over(group_cols) for c in keep]).rename(
        {"season": f"_{tag}_prior_season"}
    )


def asof_join(
    rows: pl.DataFrame,
    state: pl.DataFrame,
    by_left: Sequence[str],
    entity: Sequence[str],
    tag: str,
) -> pl.DataFrame:
    """Attach the entity's last state with ``kickoff < cutoff_ts`` (strict)."""
    rename = {e: f"_{tag}_key{i}" for i, e in enumerate(entity)}
    on_key = f"_{tag}_onkey"
    right = state.rename(rename).with_columns(pl.col(ts_col(tag)).alias(on_key)).sort(on_key)
    out = rows.sort("cutoff_ts").join_asof(
        right,
        left_on="cutoff_ts",
        right_on=on_key,
        by_left=list(by_left),
        by_right=list(rename.values()),
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    return out.drop([c for c in [*rename.values(), on_key] if c in out.columns])


def join_ps(
    df: pl.DataFrame, totals: pl.DataFrame, by_left: Sequence[str], entity: Sequence[str]
) -> pl.DataFrame:
    """Left-join prior-season totals from :func:`season_totals` on ``(entity, season)``."""
    rename = dict(zip(entity, by_left, strict=True))
    right = totals.rename({k: v for k, v in rename.items() if k != v})
    return df.join(right, on=[*by_left, "season"], how="left", coalesce=True)


def join_priors(
    df: pl.DataFrame, prior_table: pl.DataFrame, by_left: Sequence[str], tag: str
) -> pl.DataFrame:
    """Attach cumulative totals of all seasons strictly before each row's season."""
    if prior_table.is_empty():
        return df
    group_cols = [c for c in prior_table.columns if not c.startswith(f"_{tag}_")]
    rename = {g: f"_{tag}_pkey{i}" for i, g in enumerate(group_cols)}
    right = prior_table.rename(rename).sort(f"_{tag}_prior_season")
    out = (
        df.with_columns(pl.col("season").cast(pl.Int32))
        .sort("season")
        .join_asof(
            right.with_columns(pl.col(f"_{tag}_prior_season").cast(pl.Int32)),
            left_on="season",
            right_on=f"_{tag}_prior_season",
            by_left=list(by_left),
            by_right=list(rename.values()),
            strategy="backward",
            allow_exact_matches=False,
            check_sortedness=False,
        )
    )
    return out.drop([c for c in [*rename.values(), f"_{tag}_prior_season"] if c in out.columns])


def finalize(
    df: pl.DataFrame,
    specs: Sequence[tuple[str, Sequence[Stat]]],
    tag: str,
    *,
    keep: pl.Expr | None = None,
    has_ps: bool = True,
) -> pl.DataFrame:
    """Publish window columns from joined state, PS totals and priors; drop internals.

    ``specs`` lists ``(group, stats)`` pairs computed in the same state.
    ``keep`` is a boolean expression; where it is false the windows of
    ``reset`` stats are nulled (the player changed team since his last game)
    and their shrunk values fall back to the prior.
    """
    w0 = float(PARAMS["blend_w0"])
    decay = float(PARAMS["blend_decay"])
    w_ps = pl.max_horizontal(pl.lit(0.0), w0 - decay * (pl.col("week").cast(pl.Float64) - 1.0))
    same = (pl.col(season_col(tag)) == pl.col("season")).fill_null(False)
    exprs: list[pl.Expr] = []
    for group, stats in specs:
        exprs += _finalize_exprs(stats, group, tag, keep, has_ps, w_ps, same)
    out = df.with_columns(exprs)
    internal = [c for c in out.columns if c.startswith(f"_{tag}_") and c != ts_col(tag)]
    return out.drop(internal)


def _finalize_exprs(
    stats: Sequence[Stat],
    group: str,
    tag: str,
    keep: pl.Expr | None,
    has_ps: bool,
    w_ps: pl.Expr,
    same: pl.Expr,
) -> list[pl.Expr]:
    exprs: list[pl.Expr] = []
    for s in stats:
        k_keep = keep if (s.reset and keep is not None) else pl.lit(True)
        k_keep = k_keep.fill_null(False)
        k = float(PARAMS["shrink_k"][s.k]) if s.k is not None else 0.0
        prior = (
            ratio(pl.col(_c(tag, "prn", s.name)), pl.col(_c(tag, "prd", s.name)))
            if s.shrink
            else pl.lit(None, dtype=pl.Float64)
        )
        psv = (
            ratio(pl.col(_c(tag, "psn", s.name)), pl.col(_c(tag, "psd", s.name)))
            if has_ps
            else pl.lit(None, dtype=pl.Float64)
        )
        for w in ("l1", "l3", "l5", "ew"):
            if w in s.windows:
                name = fname(group, s.name, w)
                exprs.append(pl.when(k_keep).then(pl.col(name)).otherwise(None).alias(name))
        sn = sd = None
        if _needs(s, "std"):
            sel = same & k_keep
            sn = pl.when(sel).then(pl.col(_c(tag, "sn", s.name))).otherwise(0.0).fill_null(0.0)
            sd = pl.when(sel).then(pl.col(_c(tag, "sd", s.name))).otherwise(0.0).fill_null(0.0)
        if "std" in s.windows:
            raw = ratio(sn, sd)
            if has_ps:
                std = (
                    pl.when(~k_keep)
                    .then(None)
                    .when(raw.is_null())
                    .then(psv)
                    .when(psv.is_null())
                    .then(raw)
                    .otherwise(w_ps * psv + (1.0 - w_ps) * raw)
                )
            else:
                std = raw
            exprs.append(std.alias(fname(group, s.name, "std")))
        if "ps" in s.windows:
            exprs.append(psv.alias(fname(group, s.name, "ps")))
        cn = cd = None
        if _needs(s, "car"):
            cn = pl.col(_c(tag, "cn", s.name)).fill_null(0.0)
            cd = pl.col(_c(tag, "cd", s.name)).fill_null(0.0)
        # car windows come straight from the state (entity level, not reset)
        if "car" in s.windows:
            name = fname(group, s.name, "car")
            exprs.append(pl.col(name).alias(name))
        ewn = ewd = None
        if "ew" in s.shrink or "ew" in s.n_windows:
            ewn = pl.when(k_keep).then(pl.col(_c(tag, "ewn", s.name))).otherwise(0.0).fill_null(0.0)
            ewd = pl.when(k_keep).then(pl.col(_c(tag, "ewd", s.name))).otherwise(0.0).fill_null(0.0)
        psd = pl.col(_c(tag, "psd", s.name)).fill_null(0.0) if has_ps else pl.lit(0.0)
        sums = {"ew": (ewn, ewd), "std": (sn, sd), "car": (cn, cd), "ps": (None, psd)}
        for w in s.shrink:
            num, den = sums[w]
            shr = (num + k * prior) / (den + k)
            exprs.append(shr.alias(fname(group, s.name, w, "_shrunk")))
        for w in s.n_windows:
            exprs.append(sums[w][1].alias(fname(group, s.name, w, "_n")))
    return exprs


def ordered_sum(
    df: pl.DataFrame, keys: Sequence[str], order: Sequence[str], cols: Sequence[str]
) -> pl.DataFrame:
    """Group sums computed as ordered cumulative sums (reproducible float results)."""
    keys = list(keys)
    ev = df.sort([*keys, *order]).with_columns(
        [pl.col(c).cast(pl.Float64).fill_null(0.0).cum_sum().over(keys).alias(c) for c in cols]
    )
    return ev.select(*keys, *cols).unique(subset=keys, keep="last", maintain_order=True)


__all__ = [
    "asof_join",
    "compute_state",
    "finalize",
    "join_priors",
    "join_ps",
    "ordered_sum",
    "priors",
    "ratio",
    "season_col",
    "season_totals",
    "ts_col",
]
