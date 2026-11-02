"""League scoring rules applied to stat-line frames (MODELING.md section 1, D-003, D-024).

:class:`ScoringRules` holds a Sleeper-style key map (``pass_yd``, ``rec``,
``fgm_40_49``, ``pts_allow_7_13`` ...) with zero entries removed (a missing key
scores 0, D-024) and turns it into one vectorised Polars expression over the
stat-line vocabulary in :mod:`ffml.scoring.statline`. The same rules score
simulated samples and actual outcomes.

Key semantics
=============

Linear keys multiply one stat column (or a simple combination):

- passing: ``pass_yd`` (per yard), ``pass_td``, ``pass_int``, ``pass_cmp``,
  ``pass_att``, ``pass_inc`` (``pass_att - pass_cmp``)
- rushing: ``rush_yd``, ``rush_td``, ``rush_att``
- receiving: ``rec``, ``rec_yd``, ``rec_td``, ``rec_tgt`` (per target);
  ``bonus_rec_te``, ``bonus_rec_rb``, ``bonus_rec_wr`` add points per
  reception when the row's position is TE, RB or WR
- misc offense: ``fum_lost``
- kicking: ``fgm`` (any made field goal), ``fgm_0_19``, ``fgm_20_29``,
  ``fgm_30_39``, ``fgm_40_49``, ``fgm_50_59``, ``fgm_60p``, legacy ``fgm_50p``
  (``fg_made_50_59 + fg_made_60p``), ``fgmiss``, ``xpm``, ``xpmiss``. Keys are
  additive, as in Sleeper: a league with both ``fgm`` and ``fgm_40_49`` scores
  a 45-yard field goal with the sum.
- team defense (rows with position DST only): ``sack``, ``int``,
  ``fum_rec``, ``safe``, ``blk_kick``, ``def_td``

Composite keys:

- Two-point conversions: the vocabulary has one ``two_pt`` column, so it is
  scored with the **mean of the ``pass_2pt``, ``rush_2pt`` and ``rec_2pt``
  values present** (a missing key is not averaged in; with none present the
  weight is 0). This is exact whenever the present keys share a value (the
  usual case) and an approximation otherwise; a passing two-pointer also
  credits the passer, as in ``fact_player_game.two_pt_conversions``.
- Offensive ``ret_td`` (special-teams return touchdowns): scored with
  ``st_td`` (Sleeper's special-teams *player* touchdown) when present, else
  with ``fum_rec_td``, because the vocabulary has no separate
  fumble-recovery-touchdown column.
- DST ``st_td`` (kick and punt return touchdowns of the unit): scored with
  ``def_st_td`` when present, else with ``st_td``.
- DST forced fumbles (D-055): Sleeper scores defensive forced fumbles with
  ``ff`` and the unit's special-teams forced fumbles with ``def_st_ff``. The
  vocabulary has one ``forced_fumbles`` column, which counts defense and
  special teams **combined** (``fact_dst_game.forced_fumbles``), so both keys
  use the combined count and it is scored with the **mean of the ``ff`` and
  ``def_st_ff`` values present** (same convention as two-point conversions).
  This is exact when the two keys share a value or only defensive forced
  fumbles occur, and an approximation for leagues that weight them
  differently. The individual-player key ``st_ff`` stays unsupported.

Bands (Sleeper semantics, exclusive ranges):

- ``bonus_pass_yd_300`` 300-399 passing yards, ``bonus_pass_yd_400`` 400+
- ``bonus_rush_yd_100`` 100-199, ``bonus_rush_yd_200`` 200+
- ``bonus_rec_yd_100`` 100-199, ``bonus_rec_yd_200`` 200+
- ``bonus_rush_rec_yd_100`` 100-199 rushing plus receiving yards,
  ``bonus_rush_rec_yd_200`` 200+
- ``bonus_pass_cmp_25`` 25+ completions, ``bonus_rush_att_20`` 20+ carries
- DST ``pts_allow_0``, ``pts_allow_1_6``, ``pts_allow_7_13``,
  ``pts_allow_14_20``, ``pts_allow_21_27``, ``pts_allow_28_34``,
  ``pts_allow_35p`` on ``pts_allowed``
- DST ``yds_allow_0_100`` (fewer than 100), ``yds_allow_100_199``,
  ``yds_allow_200_299``, ``yds_allow_300_349``, ``yds_allow_350_399``,
  ``yds_allow_400_449``, ``yds_allow_450_499``, ``yds_allow_500_549``,
  ``yds_allow_550p`` on ``yds_allowed``

Offense, receiving and kicking keys apply to every row whose position is not
DST; DST keys apply only to DST rows (offense rows carry ``pts_allowed = 0``,
which must not earn the shutout band).

Unsupported keys
================

Keys that cannot be computed from the vocabulary (``pass_sack``, distance
touchdown and long-play bonuses such as ``pass_td_40p`` or ``rec_40p``,
first-down keys, return yardage, ``fum`` (total fumbles, only lost fumbles are
tracked), individual special-teams forced fumbles ``st_ff``, special-teams fumble
recoveries ``def_st_fum_rec``/``st_fum_rec``, ``def_pass_def``, ``def_2pt``,
IDP keys, and anything unknown) are listed in
:attr:`ScoringRules.unsupported_keys`, reported with one warning per distinct
key set, and ignored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import reduce
from typing import Any

import polars as pl

from ffml.data.sleeper import normalize_scoring_settings
from ffml.scoring.statline import FG_MADE_COLUMNS

log = logging.getLogger(__name__)

ENGINE_VERSION = "scoring-v1"

# --------------------------------------------------------------------------- #
# Key tables
# --------------------------------------------------------------------------- #

# Masks: which rows a key applies to.
_OFF = "off"  # every non-DST row (offense and kickers)
_DST = "dst"
_TE = "te"
_RB = "rb"
_WR = "wr"

# key -> (list of (column, coefficient), mask)
_LINEAR: dict[str, tuple[tuple[tuple[str, float], ...], str]] = {
    "pass_yd": ((("pass_yds", 1.0),), _OFF),
    "pass_td": ((("pass_td", 1.0),), _OFF),
    "pass_int": ((("pass_int", 1.0),), _OFF),
    "pass_cmp": ((("pass_cmp", 1.0),), _OFF),
    "pass_att": ((("pass_att", 1.0),), _OFF),
    "pass_inc": ((("pass_att", 1.0), ("pass_cmp", -1.0)), _OFF),
    "rush_yd": ((("rush_yds", 1.0),), _OFF),
    "rush_td": ((("rush_td", 1.0),), _OFF),
    "rush_att": ((("rush_att", 1.0),), _OFF),
    "rec": ((("rec", 1.0),), _OFF),
    "rec_yd": ((("rec_yds", 1.0),), _OFF),
    "rec_td": ((("rec_td", 1.0),), _OFF),
    "rec_tgt": ((("targets", 1.0),), _OFF),
    "bonus_rec_te": ((("rec", 1.0),), _TE),
    "bonus_rec_rb": ((("rec", 1.0),), _RB),
    "bonus_rec_wr": ((("rec", 1.0),), _WR),
    "fum_lost": ((("fum_lost", 1.0),), _OFF),
    "fgm": (tuple((c, 1.0) for c in FG_MADE_COLUMNS), _OFF),
    "fgm_0_19": ((("fg_made_0_19", 1.0),), _OFF),
    "fgm_20_29": ((("fg_made_20_29", 1.0),), _OFF),
    "fgm_30_39": ((("fg_made_30_39", 1.0),), _OFF),
    "fgm_40_49": ((("fg_made_40_49", 1.0),), _OFF),
    "fgm_50_59": ((("fg_made_50_59", 1.0),), _OFF),
    "fgm_60p": ((("fg_made_60p", 1.0),), _OFF),
    "fgm_50p": ((("fg_made_50_59", 1.0), ("fg_made_60p", 1.0)), _OFF),
    "fgmiss": ((("fg_missed", 1.0),), _OFF),
    "xpm": ((("xp_made", 1.0),), _OFF),
    "xpmiss": ((("xp_missed", 1.0),), _OFF),
    "sack": ((("sacks", 1.0),), _DST),
    "int": ((("def_int", 1.0),), _DST),
    "fum_rec": ((("fum_rec", 1.0),), _DST),
    "safe": ((("safeties", 1.0),), _DST),
    "blk_kick": ((("blocked_kicks", 1.0),), _DST),
    "def_td": ((("def_td", 1.0),), _DST),
}

# key -> (column or column sum, lower bound inclusive, upper bound exclusive or None, mask)
_BANDS: dict[str, tuple[tuple[str, ...], float, float | None, str]] = {
    "bonus_pass_yd_300": (("pass_yds",), 300, 400, _OFF),
    "bonus_pass_yd_400": (("pass_yds",), 400, None, _OFF),
    "bonus_rush_yd_100": (("rush_yds",), 100, 200, _OFF),
    "bonus_rush_yd_200": (("rush_yds",), 200, None, _OFF),
    "bonus_rec_yd_100": (("rec_yds",), 100, 200, _OFF),
    "bonus_rec_yd_200": (("rec_yds",), 200, None, _OFF),
    "bonus_rush_rec_yd_100": (("rush_yds", "rec_yds"), 100, 200, _OFF),
    "bonus_rush_rec_yd_200": (("rush_yds", "rec_yds"), 200, None, _OFF),
    "bonus_pass_cmp_25": (("pass_cmp",), 25, None, _OFF),
    "bonus_rush_att_20": (("rush_att",), 20, None, _OFF),
    "pts_allow_0": (("pts_allowed",), 0, 1, _DST),
    "pts_allow_1_6": (("pts_allowed",), 1, 7, _DST),
    "pts_allow_7_13": (("pts_allowed",), 7, 14, _DST),
    "pts_allow_14_20": (("pts_allowed",), 14, 21, _DST),
    "pts_allow_21_27": (("pts_allowed",), 21, 28, _DST),
    "pts_allow_28_34": (("pts_allowed",), 28, 35, _DST),
    "pts_allow_35p": (("pts_allowed",), 35, None, _DST),
    "yds_allow_0_100": (("yds_allowed",), float("-inf"), 100, _DST),
    "yds_allow_100_199": (("yds_allowed",), 100, 200, _DST),
    "yds_allow_200_299": (("yds_allowed",), 200, 300, _DST),
    "yds_allow_300_349": (("yds_allowed",), 300, 350, _DST),
    "yds_allow_350_399": (("yds_allowed",), 350, 400, _DST),
    "yds_allow_400_449": (("yds_allowed",), 400, 450, _DST),
    "yds_allow_450_499": (("yds_allowed",), 450, 500, _DST),
    "yds_allow_500_549": (("yds_allowed",), 500, 550, _DST),
    "yds_allow_550p": (("yds_allowed",), 550, None, _DST),
}

TWO_PT_KEYS: tuple[str, ...] = ("pass_2pt", "rush_2pt", "rec_2pt")
# DST forced fumbles: both keys score the combined count with the mean of the values present.
FORCED_FUMBLE_KEYS: tuple[str, ...] = ("ff", "def_st_ff")
# Priority lists: the first key present supplies the weight.
OFFENSE_RET_TD_KEYS: tuple[str, ...] = ("st_td", "fum_rec_td")
DST_ST_TD_KEYS: tuple[str, ...] = ("def_st_td", "st_td")

SUPPORTED_KEYS: frozenset[str] = frozenset(
    set(_LINEAR)
    | set(_BANDS)
    | set(TWO_PT_KEYS)
    | set(FORCED_FUMBLE_KEYS)
    | set(OFFENSE_RET_TD_KEYS)
    | set(DST_ST_TD_KEYS)
)

# Keys known to be unsupported, with the reason (documentation and messages only;
# any key outside SUPPORTED_KEYS is unsupported).
KNOWN_UNSUPPORTED: dict[str, str] = {
    "pass_sack": "sacks taken by the passer are not in the stat line",
    "fum": "total fumbles; only fumbles lost are tracked",
    "st_ff": "individual special-teams forced fumbles are not tracked",
    "def_st_fum_rec": "special-teams fumble recoveries are not tracked separately",
    "st_fum_rec": "special-teams fumble recoveries are not tracked separately",
    "def_pass_def": "passes defended are not tracked",
    "def_2pt": "defensive two-point returns are not tracked",
}

# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #

# Offense matches nflverse ``fantasy_points`` (pass_int -2, fumbles lost -2,
# 2 per two-point conversion, 6 per special-teams touchdown); kicking and DST
# use the common Sleeper defaults.
_BASE_PRESET: dict[str, float] = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -2.0,
    "pass_2pt": 2.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rush_2pt": 2.0,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "rec_2pt": 2.0,
    "fum_lost": -2.0,
    "st_td": 6.0,
    "fum_rec_td": 6.0,
    "fgm_0_19": 3.0,
    "fgm_20_29": 3.0,
    "fgm_30_39": 3.0,
    "fgm_40_49": 4.0,
    "fgm_50_59": 5.0,
    "fgm_60p": 5.0,
    "fgmiss": -1.0,
    "xpm": 1.0,
    "xpmiss": -1.0,
    "sack": 1.0,
    "int": 2.0,
    "fum_rec": 2.0,
    "safe": 2.0,
    "blk_kick": 2.0,
    "def_td": 6.0,
    "def_st_td": 6.0,
    "pts_allow_0": 10.0,
    "pts_allow_1_6": 7.0,
    "pts_allow_7_13": 4.0,
    "pts_allow_14_20": 1.0,
    "pts_allow_21_27": 0.0,
    "pts_allow_28_34": -1.0,
    "pts_allow_35p": -4.0,
}

PRESETS: dict[str, dict[str, float]] = {
    "ppr": {**_BASE_PRESET, "rec": 1.0},
    "half": {**_BASE_PRESET, "rec": 0.5},
    "std": dict(_BASE_PRESET),
}
_PRESET_ALIASES = {"half_ppr": "half", "standard": "std", "full_ppr": "ppr"}


class UnsupportedScoringKeyWarning(UserWarning):
    """A scoring key cannot be computed from the stat-line vocabulary and is ignored."""


_WARNED: set[frozenset[str]] = set()


def _reset_warning_cache() -> None:
    """Forget which unsupported key sets were reported (tests only)."""
    _WARNED.clear()


def _first_present(settings: Mapping[str, float], keys: Iterable[str]) -> float:
    for key in keys:
        if key in settings:
            return settings[key]
    return 0.0


# --------------------------------------------------------------------------- #
# ScoringRules
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoringRules:
    """Immutable scoring rules keyed by Sleeper scoring keys.

    Build with :meth:`from_sleeper`, :meth:`from_dict` (manual input) or
    :meth:`from_preset`; score with :meth:`score` or :meth:`with_points`.
    """

    settings: tuple[tuple[str, float], ...]
    name: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        unsupported = self.unsupported_keys
        if unsupported:
            key_set = frozenset(unsupported)
            if key_set not in _WARNED:
                _WARNED.add(key_set)
                message = (
                    f"scoring keys not computable from the stat line are ignored: "
                    f"{', '.join(unsupported)}"
                )
                log.warning(message)
                warnings.warn(message, UnsupportedScoringKeyWarning, stacklevel=4)

    # -- construction ----------------------------------------------------- #

    @classmethod
    def from_sleeper(
        cls, scoring_settings: Mapping[str, Any] | None, name: str | None = None
    ) -> ScoringRules:
        """Rules from a raw Sleeper ``scoring_settings`` map (zeros and nulls dropped)."""
        normalized = normalize_scoring_settings(scoring_settings)
        return cls(settings=tuple(sorted(normalized.items())), name=name)

    @classmethod
    def from_dict(cls, settings: Mapping[str, Any], name: str | None = None) -> ScoringRules:
        """Rules from a manual key map (same keys and semantics as Sleeper)."""
        return cls.from_sleeper(settings, name=name)

    @classmethod
    def from_preset(cls, preset: str) -> ScoringRules:
        """``"ppr"``, ``"half"`` or ``"std"`` (offense equals nflverse fantasy points)."""
        key = _PRESET_ALIASES.get(preset.lower(), preset.lower())
        if key not in PRESETS:
            raise ValueError(f"unknown scoring preset {preset!r}; choose from {sorted(PRESETS)}")
        return cls.from_sleeper(PRESETS[key], name=key)

    # -- introspection ---------------------------------------------------- #

    @property
    def weights(self) -> dict[str, float]:
        """The normalised key map (zero entries removed)."""
        return dict(self.settings)

    @property
    def supported_keys(self) -> tuple[str, ...]:
        """Keys present in these rules that the engine computes."""
        return tuple(k for k, _ in self.settings if k in SUPPORTED_KEYS)

    @property
    def unsupported_keys(self) -> tuple[str, ...]:
        """Keys present in these rules that cannot be computed and are ignored."""
        return tuple(k for k, _ in self.settings if k not in SUPPORTED_KEYS)

    @property
    def scoring_hash(self) -> str:
        """Stable 16-hex-digit hash of the supported keys and the engine version.

        Unsupported keys do not change any score, so two leagues that differ
        only in ignored keys share a hash (and a cache entry).
        """
        payload = {
            "engine": ENGINE_VERSION,
            "rules": [[k, round(v, 10)] for k, v in self.settings if k in SUPPORTED_KEYS],
        }
        blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    @property
    def two_pt_weight(self) -> float:
        present = [v for k, v in self.settings if k in TWO_PT_KEYS]
        return sum(present) / len(present) if present else 0.0

    @property
    def forced_fumble_weight(self) -> float:
        present = [v for k, v in self.settings if k in FORCED_FUMBLE_KEYS]
        return sum(present) / len(present) if present else 0.0

    @property
    def offense_ret_td_weight(self) -> float:
        return _first_present(self.weights, OFFENSE_RET_TD_KEYS)

    @property
    def dst_st_td_weight(self) -> float:
        return _first_present(self.weights, DST_ST_TD_KEYS)

    def column_weights(self) -> dict[str, dict[str, float]]:
        """Linear weight per stat column and mask (bands excluded); for inspection."""
        out: dict[str, dict[str, float]] = {}
        weights = self.weights
        for key, (terms, mask) in _LINEAR.items():
            if key not in weights:
                continue
            for column, coef in terms:
                bucket = out.setdefault(mask, {})
                bucket[column] = bucket.get(column, 0.0) + coef * weights[key]
        if self.two_pt_weight:
            out.setdefault(_OFF, {})["two_pt"] = self.two_pt_weight
        if self.offense_ret_td_weight:
            out.setdefault(_OFF, {})["ret_td"] = self.offense_ret_td_weight
        if self.dst_st_td_weight:
            out.setdefault(_DST, {})["st_td"] = self.dst_st_td_weight
        if self.forced_fumble_weight:
            out.setdefault(_DST, {})["forced_fumbles"] = self.forced_fumble_weight
        return out

    # -- scoring ---------------------------------------------------------- #

    def expression(self, columns: Iterable[str]) -> pl.Expr:
        """Polars expression for points given the columns present in a frame.

        Missing stat columns count as zero; nulls count as zero. Requires a
        ``position`` column whenever position-specific keys are in use.
        """
        have = set(columns)

        def col(name: str) -> pl.Expr:
            if name in have:
                return pl.col(name).fill_null(0).cast(pl.Float64)
            return pl.lit(0.0, dtype=pl.Float64)

        position = pl.col("position")
        masks = {
            _OFF: position != "DST",
            _DST: position == "DST",
            _TE: position == "TE",
            _RB: position == "RB",
            _WR: position == "WR",
        }

        # Collect linear coefficients per (mask, column) so each column appears once.
        linear: dict[str, dict[str, float]] = self.column_weights()
        terms: list[pl.Expr] = []
        for mask, coefs in linear.items():
            parts = [col(c) * w for c, w in coefs.items() if w != 0.0]
            if not parts:
                continue
            total = reduce(lambda a, b: a + b, parts)
            terms.append(pl.when(masks[mask]).then(total).otherwise(0.0))

        weights = self.weights
        for key, (cols, lower, upper, mask) in _BANDS.items():
            if key not in weights:
                continue
            value = reduce(lambda a, b: a + b, [col(c) for c in cols])
            cond = value >= lower
            if upper is not None:
                cond = cond & (value < upper)
            terms.append(pl.when(masks[mask] & cond).then(weights[key]).otherwise(0.0))

        if not terms:
            return pl.lit(0.0, dtype=pl.Float64)
        return pl.sum_horizontal(terms)

    def _check_position(self, frame: pl.DataFrame) -> None:
        if "position" not in frame.columns:
            raise ValueError("scoring requires a 'position' column (QB, RB, WR, TE, K, DST)")

    def score(self, frame: pl.DataFrame | pl.LazyFrame) -> pl.Series:
        """Fantasy points per row as a Float64 Series named ``points``."""
        if isinstance(frame, pl.LazyFrame):
            frame = frame.collect()
        self._check_position(frame)
        return frame.select(self.expression(frame.columns).alias("points")).to_series()

    def with_points(self, frame: pl.DataFrame, alias: str = "points") -> pl.DataFrame:
        """``frame`` with an added points column."""
        self._check_position(frame)
        return frame.with_columns(self.expression(frame.columns).alias(alias))

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return (
            f"ScoringRules{label}(keys={len(self.settings)}, "
            f"unsupported={len(self.unsupported_keys)}, hash={self.scoring_hash})"
        )


__all__ = [
    "DST_ST_TD_KEYS",
    "ENGINE_VERSION",
    "FORCED_FUMBLE_KEYS",
    "KNOWN_UNSUPPORTED",
    "OFFENSE_RET_TD_KEYS",
    "PRESETS",
    "SUPPORTED_KEYS",
    "TWO_PT_KEYS",
    "ScoringRules",
    "UnsupportedScoringKeyWarning",
]
