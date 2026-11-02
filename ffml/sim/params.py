"""Typed parameter contracts for the Monte Carlo engine (MODELING.md section 5).

The engine consumes three Polars frames. Stage models (Phase 3 wave two) and
the structural baselines produce them; tests build them by hand or with
:func:`synthetic_slate`. Each contract lists its columns with dtype, default
and meaning; :meth:`ParamsContract.validate` casts types, fills defaults for
missing or null optional columns, checks required columns, keys and ranges,
and returns a clean frame.

TeamGameParams (one row per team per game; both teams of a game are required)
    ``game_id, season, week, team, opponent, home`` keys;
    ``exp_plays`` expected offensive plays (dropbacks plus designed runs and
    scrambles, as ``fact_team_game.plays``), ``sd_plays``;
    ``exp_pass_rate`` expected dropbacks per play, ``pass_rate_conc`` Beta
    concentration; ``exp_off_td`` expected offensive touchdowns,
    ``exp_fg_att`` expected field-goal attempts, ``exp_two_pt_att`` expected
    two-point attempts; ``implied_total`` (null: derived from the lines);
    ``spread_line`` expected margin of ``team`` over ``opponent`` (positive
    when ``team`` is favoured; for the home team this is
    ``dim_game.spread_line``, for the away team its negative);
    ``total_line``; ``margin_sd`` (13.5) and ``total_sd`` (10.0) of the
    game-level draws.

PlayerGameParams (one row per player per game; positions QB, RB, WR, TE, K)
    ``entity_id, game_id, team, position`` keys; ``availability_prob``;
    expected shares of the team's targets, carries and dropbacks
    (``target_share``, ``carry_share``, ``dropback_share``; renormalised over
    active players in every simulation); ``gl_carry_share`` and
    ``rz_target_share`` (used as touchdown weights when the TD shares are
    null); ``pass_td_share`` (share of the team's passing touchdowns caught)
    and ``rush_td_share``; ``share_conc`` Dirichlet concentration (null:
    per-position default); efficiency ``catch_rate``, ``yards_per_rec`` with
    Gamma ``yards_per_rec_shape``, ``yards_per_carry`` with
    ``yards_per_carry_shape``, passing ``comp_rate``, ``yards_per_comp`` with
    ``yards_per_comp_shape``, ``int_rate`` (per attempt), ``sack_rate`` (per
    dropback), ``fumble_rate`` (lost fumbles per touch: dropbacks, carries
    and receptions); ``two_pt_rate`` (weight as the scorer of the team's
    two-point conversions; null: ``target_share + carry_share``);
    ``ret_td_rate`` (return touchdowns per game); kickers carry
    ``fg_att_share_<bucket>`` and ``fg_make_prob_<bucket>`` for the six
    buckets of :data:`ffml.scoring.statline.FG_BUCKETS` and
    ``xp_make_prob``.

DstGameParams (one row per team defense per game; optional)
    ``team, game_id`` keys; ``sack_rate`` (per opponent dropback),
    ``int_rate`` (per opponent pass attempt), ``fum_rec_rate`` (lost fumbles
    forced per opponent touch); each, when not null, is blended with the
    offense's own rate by a geometric mean so that the defense's events and
    the opposing offense's stat lines stay identical; ``def_td_rate``
    (defensive touchdowns per takeaway), ``st_td_rate`` (return touchdowns
    per game), ``safety_rate`` and ``block_rate`` (per game);
    ``yds_allowed_mean`` and ``yds_allowed_sd`` (when present, the simulated
    yards of the opposing offense are re-centred and re-scaled to them, which
    keeps the dependence on the opponent's simulated stat lines).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
import polars as pl

from ffml.scoring.statline import FG_BUCKETS

REQUIRED = object()

SIM_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K")
OFFENSE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")

# Dirichlet concentration per position when share_conc is null (tuned later on
# the validation seasons, MODELING.md section 6).
DEFAULT_SHARE_CONC: dict[str, float] = {"QB": 100.0, "RB": 30.0, "WR": 60.0, "TE": 60.0, "K": 100.0}

DEFAULT_FG_ATT_SHARE: dict[str, float] = {
    "0_19": 0.02,
    "20_29": 0.24,
    "30_39": 0.25,
    "40_49": 0.26,
    "50_59": 0.21,
    "60p": 0.02,
}
DEFAULT_FG_MAKE_PROB: dict[str, float] = {
    "0_19": 0.99,
    "20_29": 0.97,
    "30_39": 0.92,
    "40_49": 0.84,
    "50_59": 0.72,
    "60p": 0.45,
}
FG_ATT_SHARE_COLUMNS: tuple[str, ...] = tuple(f"fg_att_share_{b}" for b in FG_BUCKETS)
FG_MAKE_PROB_COLUMNS: tuple[str, ...] = tuple(f"fg_make_prob_{b}" for b in FG_BUCKETS)


@dataclass(frozen=True)
class Col:
    """One documented column of a parameter contract."""

    dtype: Any
    default: Any = REQUIRED
    doc: str = ""
    nullable: bool = False
    lower: float | None = None
    upper: float | None = None

    @property
    def required(self) -> bool:
        return self.default is REQUIRED


class ParamsContract:
    """Base class: ``COLUMNS`` maps names to :class:`Col`; ``KEYS`` must be unique."""

    NAME: ClassVar[str] = "params"
    COLUMNS: ClassVar[dict[str, Col]] = {}
    KEYS: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def describe(cls) -> pl.DataFrame:
        """The contract as a table (column, dtype, default, doc)."""
        return pl.DataFrame(
            {
                "column": list(cls.COLUMNS),
                "dtype": [str(c.dtype) for c in cls.COLUMNS.values()],
                "default": [
                    "required" if c.required else repr(c.default) for c in cls.COLUMNS.values()
                ],
                "doc": [c.doc for c in cls.COLUMNS.values()],
            }
        )

    @classmethod
    def empty(cls) -> pl.DataFrame:
        return pl.DataFrame(schema={k: c.dtype for k, c in cls.COLUMNS.items()})

    @classmethod
    def _default_expr(cls, name: str, col: Col) -> pl.Expr:
        if isinstance(col.default, Mapping):
            expr = pl.lit(None, dtype=col.dtype)
            for pos, value in col.default.items():
                expr = pl.when(pl.col("position") == pos).then(pl.lit(value)).otherwise(expr)
            return expr
        return pl.lit(col.default, dtype=col.dtype)

    @classmethod
    def validate(cls, frame: pl.DataFrame | None) -> pl.DataFrame:
        """Cast, fill defaults, and check a parameter frame; returns a new frame."""
        if frame is None:
            frame = cls.empty()
        missing = [n for n, c in cls.COLUMNS.items() if c.required and n not in frame.columns]
        if missing:
            raise ValueError(f"{cls.NAME}: missing required columns {missing}")
        out = frame
        for name, col in cls.COLUMNS.items():
            if name not in out.columns:
                if col.required:  # pragma: no cover - checked above
                    continue
                out = out.with_columns(cls._default_expr(name, col).alias(name))
            elif not col.required and col.default is not None:
                out = out.with_columns(
                    pl.col(name).cast(col.dtype).fill_null(cls._default_expr(name, col))
                )
        out = out.with_columns(pl.col(n).cast(c.dtype) for n, c in cls.COLUMNS.items())
        out = out.select(list(cls.COLUMNS))
        if out.height == 0:
            return out
        for name, col in cls.COLUMNS.items():
            if not col.nullable and out[name].null_count():
                raise ValueError(f"{cls.NAME}: column {name!r} has nulls")
            if col.lower is not None and (out[name] < col.lower).any():
                raise ValueError(f"{cls.NAME}: column {name!r} below {col.lower}")
            if col.upper is not None and (out[name] > col.upper).any():
                raise ValueError(f"{cls.NAME}: column {name!r} above {col.upper}")
        if cls.KEYS and out.select(cls.KEYS).is_duplicated().any():
            raise ValueError(f"{cls.NAME}: duplicate keys {cls.KEYS}")
        return out


def _f(default: Any = REQUIRED, doc: str = "", lower=None, upper=None, nullable=False) -> Col:
    return Col(pl.Float64, default, doc, nullable=nullable, lower=lower, upper=upper)


class TeamGameParams(ParamsContract):
    NAME = "TeamGameParams"
    KEYS = ("game_id", "team")
    COLUMNS = {
        "game_id": Col(pl.String, doc="nflverse game id"),
        "season": Col(pl.Int32),
        "week": Col(pl.Int32),
        "team": Col(pl.String, doc="offense of this row"),
        "opponent": Col(pl.String),
        "home": Col(pl.Boolean),
        "exp_plays": _f(doc="expected offensive plays", lower=1),
        "sd_plays": _f(7.0, "sd of plays (Normal, truncated to 40-90)", lower=0),
        "exp_pass_rate": _f(doc="expected dropbacks per play", lower=0, upper=1),
        "pass_rate_conc": _f(60.0, "Beta concentration of the pass rate", lower=1),
        "exp_off_td": _f(doc="expected offensive touchdowns", lower=0),
        "exp_fg_att": _f(doc="expected field-goal attempts", lower=0),
        "exp_two_pt_att": _f(0.12, "expected two-point attempts", lower=0),
        "implied_total": _f(None, "implied team points (null: from lines)", nullable=True),
        "spread_line": _f(doc="expected margin of team over opponent (positive = favoured)"),
        "total_line": _f(doc="game total", lower=0),
        "margin_sd": _f(13.5, "sd of the game margin draw", lower=0),
        "total_sd": _f(10.0, "sd of the game total draw", lower=0),
    }


class PlayerGameParams(ParamsContract):
    NAME = "PlayerGameParams"
    KEYS = ("entity_id", "game_id")
    COLUMNS = {
        "entity_id": Col(pl.String, doc="player_id (gsis)"),
        "game_id": Col(pl.String),
        "team": Col(pl.String),
        "position": Col(pl.String, doc="QB, RB, WR, TE or K"),
        "availability_prob": _f(1.0, "probability of being active", 0, 1),
        "target_share": _f(0.0, "expected share of team targets", 0, 1),
        "carry_share": _f(0.0, "expected share of team carries", 0, 1),
        "dropback_share": _f(0.0, "expected share of team dropbacks (QBs)", 0, 1),
        "gl_carry_share": _f(None, "goal-line carry share (rush TD weight fallback)", 0, 1, True),
        "rz_target_share": _f(None, "red-zone target share (pass TD weight fallback)", 0, 1, True),
        "pass_td_share": _f(None, "share of team passing TDs caught", 0, 1, True),
        "rush_td_share": _f(None, "share of team rushing TDs", 0, 1, True),
        "share_conc": Col(
            pl.Float64, dict(DEFAULT_SHARE_CONC), "Dirichlet concentration", lower=0.01
        ),
        "catch_rate": _f(0.65, "receptions per target", 0, 1),
        "yards_per_rec": _f(10.0, "mean yards per reception", 0.1),
        "yards_per_rec_shape": _f(1.0, "Gamma shape of yards per reception", 0.01),
        "yards_per_carry": _f(4.2, "mean yards per carry", 0.1),
        "yards_per_carry_shape": _f(0.5, "Gamma shape of yards per carry", 0.01),
        "comp_rate": _f(0.64, "completions per attempt", 0, 1),
        "yards_per_comp": _f(11.0, "mean yards per completion", 0.1),
        "yards_per_comp_shape": _f(1.2, "Gamma shape of yards per completion", 0.01),
        "int_rate": _f(0.023, "interceptions per attempt", 0, 1),
        "sack_rate": _f(0.065, "sacks per dropback", 0, 1),
        "fumble_rate": _f(0.005, "lost fumbles per touch", 0, 1),
        "two_pt_rate": _f(None, "scorer weight for two-point conversions", 0, None, True),
        "ret_td_rate": _f(0.0, "return touchdowns per game", 0),
        **{
            f"fg_att_share_{b}": _f(DEFAULT_FG_ATT_SHARE[b], f"share of FG attempts {b}", 0, 1)
            for b in FG_BUCKETS
        },
        **{
            f"fg_make_prob_{b}": _f(DEFAULT_FG_MAKE_PROB[b], f"make probability {b}", 0, 1)
            for b in FG_BUCKETS
        },
        "xp_make_prob": _f(0.95, "extra-point make probability", 0, 1),
    }

    @classmethod
    def validate(cls, frame: pl.DataFrame | None) -> pl.DataFrame:
        if frame is not None and "position" in frame.columns and frame.height:
            bad = sorted(set(frame["position"].drop_nulls().to_list()) - set(SIM_POSITIONS))
            if bad:
                raise ValueError(f"PlayerGameParams: unsupported positions {bad}")
        return super().validate(frame)


class DstGameParams(ParamsContract):
    NAME = "DstGameParams"
    KEYS = ("game_id", "team")
    COLUMNS = {
        "team": Col(pl.String, doc="defense of this row"),
        "game_id": Col(pl.String),
        "sack_rate": _f(None, "sacks per opponent dropback (blended)", 0, 1, True),
        "int_rate": _f(None, "interceptions per opponent attempt (blended)", 0, 1, True),
        "fum_rec_rate": _f(None, "lost fumbles forced per opponent touch (blended)", 0, 1, True),
        "def_td_rate": _f(0.09, "defensive touchdowns per takeaway", 0, 1),
        "st_td_rate": _f(0.04, "return touchdowns per game", 0),
        "safety_rate": _f(0.02, "safeties per game", 0),
        "block_rate": _f(0.05, "blocked kicks per game", 0),
        "yds_allowed_mean": _f(None, "target mean of yards allowed", 0, None, True),
        "yds_allowed_sd": _f(None, "target sd of yards allowed", 0, None, True),
    }


# --------------------------------------------------------------------------- #
# Synthetic slate (tests, timing, demos)
# --------------------------------------------------------------------------- #

_ROSTER: tuple[tuple[str, float, float, float, float], ...] = (
    # position, target_share, carry_share, dropback_share, availability
    ("QB", 0.0, 0.12, 0.97, 0.97),
    ("QB", 0.0, 0.01, 0.03, 1.0),
    ("RB", 0.08, 0.48, 0.0, 0.92),
    ("RB", 0.04, 0.25, 0.0, 0.95),
    ("RB", 0.01, 0.07, 0.0, 0.97),
    ("RB", 0.0, 0.02, 0.0, 0.98),
    ("WR", 0.24, 0.02, 0.0, 0.93),
    ("WR", 0.18, 0.01, 0.0, 0.95),
    ("WR", 0.12, 0.01, 0.0, 0.96),
    ("WR", 0.06, 0.0, 0.0, 0.97),
    ("WR", 0.03, 0.0, 0.0, 0.98),
    ("WR", 0.02, 0.0, 0.0, 0.98),
    ("WR", 0.01, 0.0, 0.0, 0.99),
    ("WR", 0.01, 0.0, 0.0, 0.99),
    ("TE", 0.15, 0.0, 0.0, 0.94),
    ("TE", 0.04, 0.0, 0.0, 0.97),
    ("TE", 0.01, 0.0, 0.0, 0.98),
    ("TE", 0.0, 0.0, 0.0, 0.99),
    ("K", 0.0, 0.0, 0.0, 0.99),
)


def synthetic_slate(
    n_games: int = 16, season: int = 2026, week: int = 9, seed: int = 0
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Plausible team, player and DST parameters for ``n_games`` games (19 players per team)."""
    rng = np.random.default_rng(seed)
    teams, players, dsts = [], [], []
    for g in range(n_games):
        home, away = f"H{g:02d}", f"A{g:02d}"
        game_id = f"{season}_{week:02d}_{away}_{home}"
        spread = float(np.round(rng.normal(0, 4) * 2) / 2)
        total = float(np.round(rng.normal(45, 3) * 2) / 2)
        for team, opp, is_home in ((home, away, True), (away, home, False)):
            margin = spread if is_home else -spread
            implied = (total + margin) / 2
            teams.append(
                {
                    "game_id": game_id,
                    "season": season,
                    "week": week,
                    "team": team,
                    "opponent": opp,
                    "home": is_home,
                    "exp_plays": float(rng.normal(63, 2)),
                    "exp_pass_rate": float(np.clip(rng.normal(0.58, 0.03), 0.45, 0.7)),
                    "exp_off_td": implied / 9.0,
                    "exp_fg_att": 1.7,
                    "implied_total": implied,
                    "spread_line": margin,
                    "total_line": total,
                }
            )
            dsts.append({"team": team, "game_id": game_id})
            for i, (pos, tgt, car, dbk, avail) in enumerate(_ROSTER):
                players.append(
                    {
                        "entity_id": f"{team}-{pos}{i:02d}",
                        "game_id": game_id,
                        "team": team,
                        "position": pos,
                        "availability_prob": avail,
                        "target_share": tgt,
                        "carry_share": car,
                        "dropback_share": dbk,
                        "pass_td_share": tgt,
                        "rush_td_share": car,
                        "catch_rate": {"RB": 0.78, "TE": 0.7, "WR": 0.64}.get(pos, 0.65),
                        "yards_per_rec": {"RB": 7.5, "TE": 10.0, "WR": 12.5}.get(pos, 10.0),
                        "yards_per_carry": 4.4 if pos == "RB" else 5.0,
                        "ret_td_rate": 0.01 if pos == "WR" and i >= 9 else 0.0,
                    }
                )
    return (
        TeamGameParams.validate(pl.DataFrame(teams)),
        PlayerGameParams.validate(pl.DataFrame(players)),
        DstGameParams.validate(pl.DataFrame(dsts)),
    )


__all__ = [
    "DEFAULT_FG_ATT_SHARE",
    "DEFAULT_FG_MAKE_PROB",
    "DEFAULT_SHARE_CONC",
    "FG_ATT_SHARE_COLUMNS",
    "FG_MAKE_PROB_COLUMNS",
    "OFFENSE_POSITIONS",
    "SIM_POSITIONS",
    "Col",
    "DstGameParams",
    "ParamsContract",
    "PlayerGameParams",
    "TeamGameParams",
    "synthetic_slate",
]
