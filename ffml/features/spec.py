"""Feature contract constants: version, parameters, naming, and stat specifications.

Every feature column is named ``<group>__<stat>__<window>`` (DECISIONS D-047) or
``<group>__<stat>`` for point-in-time values without a window (game context,
attributes, depth chart). Groups: ``ctx`` (game context), ``team`` (the offense
on the field), ``def`` (the defense on the field), ``opp`` (player
opportunity), ``eff`` (player efficiency), ``attr`` (player attributes and
lagged availability history), ``depth`` (depth chart as of ``asof_ts``) and
``avail`` (same-week injury and roster inputs, not model features in the
primary configuration, D-006). Windows: ``l1, l3, l5`` (last n games played),
``ew`` (exponentially weighted, alpha 0.35), ``std`` (season to date blended
with the prior season early in the year), ``ps`` (prior season), ``car``
(career within the data, 2016 onward). A ``_shrunk`` suffix marks an
empirical-Bayes shrunk rate and ``_n`` a sample size.

A :class:`Stat` describes one windowed statistic as a ratio of sums
``sum(num) / sum(den)`` over the games in a window; a per-game mean is the
special case ``den = None`` (one per game in which ``num`` is not null). The
same specs generate the computation (``windows.py``) and the manifest
(``manifest.py``), so the two cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FEATURE_VERSION = "0.1.0"

POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K")
SKILL: tuple[str, ...] = ("RB", "WR", "TE")
WINDOWS: tuple[str, ...] = ("l1", "l3", "l5", "ew", "std", "ps", "car")
GROUPS: tuple[str, ...] = ("ctx", "team", "def", "opp", "eff", "attr", "depth", "avail")
DATASETS: tuple[str, ...] = ("player_week", "team_week", "dst_week")

FEATURE_DTYPE = "Float32"
TARGET_DTYPE = "Float64"

#: Tunable parameters with documented defaults. Pseudo-counts ``shrink_k`` are in
#: units of the rate's denominator (targets, carries, attempts, ...); they are
#: starting values for the ML Engineer to tune on the validation seasons.
PARAMS: dict[str, Any] = {
    "ew_alpha": 0.35,
    # Cross-season continuation: weight of the prior-season value in the STD
    # window at week w is max(0, blend_w0 - blend_decay * (w - 1)).
    "blend_w0": 0.5,
    "blend_decay": 0.0625,
    # Historical rows: asof_ts = cutoff_ts - asof_offset_hours.
    "asof_offset_hours": 1,
    "shrink_k": {
        # player efficiency (position priors from earlier seasons)
        "targets": 20,
        "receptions": 15,
        "carries": 30,
        "attempts": 60,
        "dropbacks": 60,
        "td": 100,
        "fg_att": 20,
        "pat_att": 30,
        # player share of sparse team events (team-denominator units)
        "share_rz_targets": 10,
        "share_gl_carries": 6,
        "share_pass_td": 5,
        "share_rush_td": 4,
        # opponent defense (league priors from earlier seasons)
        "def_targets": 40,
        "def_carries": 60,
        "def_attempts": 120,
        "def_dropbacks": 120,
        "def_td": 250,
        "def_rz_trips": 20,
        # DST rare events per game
        "dst_games": 16,
    },
    "short_week_max_rest": 5,
    "off_bye_min_rest": 13,
    "primetime_hour_et": 19,
    "games_missed_window": 5,
    "inj_prev_lookback_weeks": 2,
    "weeks_since_out_cap": 52,
    "undrafted_round": 8,
    "undrafted_pick": 300,
    "n_starters": {"QB": 1, "RB": 1, "WR": 3, "TE": 1, "K": 1},
}


def fname(group: str, stat: str, window: str | None = None, suffix: str = "") -> str:
    """Feature column name ``<group>__<stat>__<window><suffix>`` (or ``<group>__<stat>``)."""
    if group not in GROUPS:
        raise ValueError(f"unknown feature group {group!r}")
    if window is None:
        return f"{group}__{stat}"
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}")
    return f"{group}__{stat}__{window}{suffix}"


@dataclass(frozen=True)
class Stat:
    """A windowed statistic ``sum(num) / sum(den)`` over prior games.

    ``num`` and ``den`` are column names of the events frame. A game counts
    toward the ratio only when ``num`` (and ``den``, if given) are not null.
    ``reset`` marks share statistics whose windows restart when the player
    changes team (ground rule 5). ``shrink`` lists windows that get a shrunk
    variant with pseudo-count ``PARAMS["shrink_k"][k]``; ``n_windows`` lists
    windows for which the denominator sum is emitted as ``<window>_n``.
    """

    name: str
    num: str
    den: str | None = None
    windows: tuple[str, ...] = ("l3", "l5", "ew", "std", "ps")
    shrink: tuple[str, ...] = ()
    k: str | None = None
    n_windows: tuple[str, ...] = ()
    reset: bool = False
    positions: tuple[str, ...] = POSITIONS
    sparse: bool = False

    def __post_init__(self) -> None:
        bad = [w for w in (*self.windows, *self.shrink, *self.n_windows) if w not in WINDOWS]
        if bad:
            raise ValueError(f"{self.name}: unknown windows {bad}")
        if self.shrink and self.k is None:
            raise ValueError(f"{self.name}: shrink windows need a pseudo-count key")
        if self.k is not None and self.k not in PARAMS["shrink_k"]:
            raise ValueError(f"{self.name}: unknown pseudo-count key {self.k!r}")
        if self.shrink and self.den is None:
            raise ValueError(f"{self.name}: only ratio statistics can be shrunk")

    def columns(self, group: str) -> list[tuple[str, str | None, str]]:
        """Ordered ``(column, window, variant)`` triples this stat produces."""
        out: list[tuple[str, str | None, str]] = []
        for w in WINDOWS:
            if w in self.windows:
                out.append((fname(group, self.name, w), w, "raw"))
        for w in WINDOWS:
            if w in self.shrink:
                out.append((fname(group, self.name, w, "_shrunk"), w, "shrunk"))
        for w in WINDOWS:
            if w in self.n_windows:
                out.append((fname(group, self.name, w, "_n"), w, "n"))
        return out


@dataclass(frozen=True)
class Col:
    """One output column of a feature dataset, as listed in the manifest."""

    name: str
    dtype: str
    kind: str  # key | meta | feature | sensitivity | avail | target
    group: str | None = None
    stat: str | None = None
    window: str | None = None
    variant: str | None = None
    source: tuple[str, ...] | None = None
    positions: tuple[str, ...] | None = None
    sparse: bool = False
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "dtype": self.dtype, "kind": self.kind}
        for attr in ("group", "stat", "window", "variant", "note"):
            value = getattr(self, attr)
            if value is not None:
                out[attr] = value
        if self.source is not None:
            out["source"] = list(self.source)
        if self.positions is not None:
            out["positions"] = list(self.positions)
        if self.sparse:
            out["sparse"] = True
        return out


def stat_cols(group: str, stats: list[Stat], kind: str = "feature") -> list[Col]:
    """Manifest columns for a list of stats in one group."""
    cols: list[Col] = []
    for s in stats:
        source = (s.num,) if s.den is None else (s.num, s.den)
        for name, window, variant in s.columns(group):
            cols.append(
                Col(
                    name=name,
                    dtype=FEATURE_DTYPE,
                    kind=kind,
                    group=group,
                    stat=s.name,
                    window=window,
                    variant=variant,
                    source=source,
                    positions=s.positions,
                    sparse=s.sparse,
                )
            )
    return cols


def point_col(
    group: str,
    stat: str,
    *,
    kind: str = "feature",
    dtype: str = FEATURE_DTYPE,
    positions: tuple[str, ...] | None = None,
    sparse: bool = False,
    note: str | None = None,
    window: str | None = None,
) -> Col:
    """Manifest column for a point-in-time (non-rolling) value."""
    return Col(
        name=fname(group, stat, window),
        dtype=dtype,
        kind=kind,
        group=group,
        stat=stat,
        window=window,
        variant="point",
        positions=positions,
        sparse=sparse,
        note=note,
    )


def blend_weight(week: int) -> float:
    """Weight of the prior-season value in the STD window at ``week``."""
    return max(0.0, PARAMS["blend_w0"] - PARAMS["blend_decay"] * (week - 1))


__all__ = [
    "DATASETS",
    "FEATURE_DTYPE",
    "FEATURE_VERSION",
    "GROUPS",
    "PARAMS",
    "POSITIONS",
    "SKILL",
    "TARGET_DTYPE",
    "WINDOWS",
    "Col",
    "Stat",
    "blend_weight",
    "fname",
    "point_col",
    "stat_cols",
]
