"""Feature rows for the structural chain, read only through :mod:`ffml.features.access`.

:class:`ChainData` loads the three feature datasets once per process (only the
columns the chain uses), adds derived targets and team denominators, and hands
out training rows strictly before a cutoff and the rows of one predicted week.
Every read goes through :func:`ffml.features.access.load_features`, so the
holdout guard applies; seasons are loaded lazily up to the latest one asked
for, and nothing at or after the cutoff ever reaches a fit.

Derived columns (player_week):

- ``y_ypr`` = rec_yds / receptions, ``y_sack_rate`` = sacks / dropbacks,
  ``y_scramble_rate`` = scrambles / dropbacks (as ``opp__scramble_rate``),
  ``y_touches`` = dropbacks + carries + receptions,
  ``y_fumble_rate`` = fumbles_lost / touches, ``y_fg_pct`` = fg_made / fg_att;
- team denominators summed over the team-game's player rows:
  ``tm_targets``, ``tm_carries``, ``tm_dropbacks``, ``tm_rec_td``,
  ``tm_rush_td``, ``tm_rz_targets``, ``tm_gl_carries``;
- ``pos_code`` (QB 0, RB 1, WR 2, TE 3, K 4) and ``wk_key`` = season * 100 + week.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import polars as pl

from ffml.features.access import load_features
from ffml.features.manifest import columns as manifest_columns

log = logging.getLogger(__name__)

FIRST_SEASON = 2016
DATASETS: tuple[str, ...] = ("team_week", "player_week", "dst_week")
CHAIN_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K")
POS_CODE: dict[str, int] = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4}

KEYS: dict[str, tuple[str, ...]] = {
    "team_week": ("team", "season", "week", "game_id", "opponent", "game_type", "home"),
    "player_week": (
        "player_id",
        "season",
        "week",
        "game_id",
        "team",
        "opponent",
        "position",
        "game_type",
        "home",
    ),
    "dst_week": ("team", "season", "week", "game_id", "opponent", "game_type", "home"),
}


def wk_key(season: int, week: int) -> int:
    return int(season) * 100 + int(week)


def _safe_div(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return pl.when(den > 0).then(num / den).otherwise(None)


def _derive_player(df: pl.DataFrame) -> pl.DataFrame:
    team_keys = ["game_id", "team"]
    sums = {
        "tm_targets": "y_targets",
        "tm_carries": "y_carries",
        "tm_dropbacks": "y_dropbacks",
        "tm_rec_td": "y_rec_td",
        "tm_rush_td": "y_rush_td",
        "tm_rz_targets": "y_rz_targets",
        "tm_gl_carries": "y_gl_carries",
    }
    have = {k: v for k, v in sums.items() if v in df.columns}
    out = df.with_columns(
        [pl.col(v).fill_null(0).sum().over(team_keys).alias(k) for k, v in have.items()]
    )
    exprs = []
    if {"y_rec_yds", "y_receptions"} <= set(out.columns):
        exprs.append(_safe_div(pl.col("y_rec_yds"), pl.col("y_receptions")).alias("y_ypr"))
    if {"y_sacks", "y_dropbacks"} <= set(out.columns):
        exprs.append(_safe_div(pl.col("y_sacks"), pl.col("y_dropbacks")).alias("y_sack_rate"))
        if "y_scrambles" in out.columns:
            exprs.append(
                _safe_div(pl.col("y_scrambles"), pl.col("y_dropbacks")).alias("y_scramble_rate")
            )
    if {"y_dropbacks", "y_carries", "y_receptions"} <= set(out.columns):
        touches = (
            pl.col("y_dropbacks").fill_null(0)
            + pl.col("y_carries").fill_null(0)
            + pl.col("y_receptions").fill_null(0)
        )
        exprs.append(touches.alias("y_touches"))
        if "y_fumbles_lost" in out.columns:
            exprs.append(_safe_div(pl.col("y_fumbles_lost"), touches).alias("y_fumble_rate"))
    if {"y_fg_made", "y_fg_att"} <= set(out.columns):
        exprs.append(_safe_div(pl.col("y_fg_made"), pl.col("y_fg_att")).alias("y_fg_pct"))
    exprs.append(pl.col("position").replace_strict(POS_CODE, default=9).alias("pos_code"))
    return out.with_columns(exprs)


_SHARED: dict[str, ChainData] = {}


class ChainData:
    """Lazy, cached feature frames for the chain.

    :meth:`shared` returns one process-wide instance per player dataset, so the
    primary and final-report models of a backtest read the features once.
    """

    @classmethod
    def shared(cls, player_dataset: str = "player_week") -> ChainData:
        if player_dataset not in _SHARED:
            _SHARED[player_dataset] = cls(player_dataset=player_dataset, purpose="train")
        return _SHARED[player_dataset]

    def __init__(
        self,
        player_dataset: str = "player_week",
        purpose: str = "train",
        frames: dict[str, pl.DataFrame] | None = None,
    ) -> None:
        self.player_dataset = player_dataset
        self.purpose = purpose
        self._frames: dict[str, pl.DataFrame] = {}
        self._loaded_through: int | None = None
        self._snapshot_ids: dict[str, str | None] = {}
        if frames is not None:  # synthetic data (tests) or a pre-loaded set
            for name, frame in frames.items():
                self._frames[name] = self._prepare(name, frame)
            self._loaded_through = 9999

    # -- loading ---------------------------------------------------------- #

    @staticmethod
    def wanted_columns(dataset: str, available: Iterable[str] | None = None) -> list[str]:
        """Keys, model inputs of every configuration, and targets of a dataset."""
        base = "player_week" if dataset.startswith("player_week") else dataset
        cols = [
            c.name
            for c in manifest_columns(base)
            if c.kind in ("key", "feature", "avail", "target") and c.name != "feature_version"
        ]
        cols = [*cols, "feature_version"]
        if available is not None:
            have = set(available)
            cols = [c for c in cols if c in have]
        return cols

    def _prepare(self, name: str, frame: pl.DataFrame) -> pl.DataFrame:
        out = frame.with_columns(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            (pl.col("season").cast(pl.Int64) * 100 + pl.col("week").cast(pl.Int64)).alias("wk_key"),
        )
        if name == "player_week":
            out = out.filter(pl.col("position").is_in(list(CHAIN_POSITIONS)))
            out = _derive_player(out)
        return out

    def prepare(self, name: str, frame: pl.DataFrame) -> pl.DataFrame:
        """Add the chain's derived columns to externally built rows (live predictions)."""
        return self._prepare(name, frame)

    def ensure(self, last_season: int) -> None:
        """Load every dataset for seasons ``FIRST_SEASON..last_season`` (guarded)."""
        if self._loaded_through is not None and self._loaded_through >= last_season:
            return
        seasons = list(range(FIRST_SEASON, int(last_season) + 1))
        for name in DATASETS:
            source = self.player_dataset if name == "player_week" else name
            sid = self._latest_id(source)
            frame = load_features(source, seasons, self.purpose, snapshot_id=sid)
            self._snapshot_ids[source] = sid
            keep = self.wanted_columns(name, frame.columns)
            self._frames[name] = self._prepare(name, frame.select(keep))
            log.info("chain data %s: %d rows x %d cols", name, frame.height, len(keep))
        self._loaded_through = int(last_season)

    @staticmethod
    def _latest_id(dataset: str) -> str | None:
        """Pin the snapshot before reading so a concurrent rebuild cannot mix versions."""
        from ffml.config import get_settings
        from ffml.data.store import SnapshotStore

        return SnapshotStore(get_settings().features_dir).latest_id(dataset)

    @property
    def snapshot_ids(self) -> dict[str, str | None]:
        return dict(self._snapshot_ids)

    def frame(self, name: str) -> pl.DataFrame:
        if name not in self._frames:
            raise KeyError(f"{name} not loaded; call ensure() first")
        return self._frames[name]

    @property
    def feature_version(self) -> str | None:
        f = self._frames.get("player_week")
        if f is None or "feature_version" not in f.columns or f.height == 0:
            return None
        return str(f["feature_version"].drop_nulls().max())

    # -- slicing ---------------------------------------------------------- #

    def train_rows(self, name: str, cutoff: tuple[int, int]) -> pl.DataFrame:
        """Regular-season rows strictly before ``cutoff`` with a final game (targets present)."""
        self.ensure(cutoff[0])
        target = {"team_week": "y_plays", "player_week": "y_played", "dst_week": "y_sacks"}[name]
        f = self._frames[name]
        return f.filter(
            (pl.col("wk_key") < wk_key(*cutoff))
            & (pl.col("game_type") == "REG")
            & pl.col(target).is_not_null()
        )

    def week_rows(self, name: str, season: int, week: int) -> pl.DataFrame:
        """Regular-season rows of one predicted week (targets are never used)."""
        self.ensure(season)
        f = self._frames[name]
        return f.filter(
            (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("game_type") == "REG")
        )


__all__ = ["CHAIN_POSITIONS", "ChainData", "FIRST_SEASON", "POS_CODE", "wk_key"]
