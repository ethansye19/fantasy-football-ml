"""Stage specifications of the structural chain (MODELING.md sections 3-4, FEATURES.md I).

Each :class:`StageSpec` names the dataset, the target (a ``y_`` column or a
derived one from :mod:`ffml.models.chain.data`), the rows it is fitted on,
the sample weight, the structural baseline (MODELING.md section 3), the
LightGBM candidate's objective, feature list and monotone constraints, and
the stage-level selection metric:

- ``mae``: mean absolute error, weighted by ``weight`` when the stage has one
  (rates are weighted by their denominators);
- ``poisson``: mean Poisson deviance (counts);
- ``logloss``: Bernoulli log loss (availability).

Feature lists are curated per stage from the As-built names in
docs/FEATURES.md; names absent from the loaded data are dropped at fit time,
so a feature rebuild that adds columns never breaks a fit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

AVAIL_SHARE_FEATURES: tuple[str, ...] = (
    "avail__teammates_out_same_pos",
    "avail__teammates_out_target_share_ew",
    "avail__teammates_out_rush_share_ew",
    "avail__report_status_ord",
    "avail__on_injury_report",
)


@dataclass(frozen=True)
class StageSpec:
    name: str
    dataset: str  # team_week, player_week, dst_week
    target: str
    kind: str  # continuous, share, rate, positive, count, binary
    metric: str  # mae, poisson, logloss
    structural: str  # structural method name (structural.py)
    structural_args: dict = field(default_factory=dict)
    positions: tuple[str, ...] | None = None
    weight: str | None = None
    played_only: bool = True
    features: tuple[str, ...] = ()
    monotone: dict[str, int] = field(default_factory=dict)
    lgbm: bool = True  # whether a LightGBM candidate exists
    final_report_features: tuple[str, ...] = ()
    clip: tuple[float, float] | None = None
    note: str = ""

    @property
    def objective(self) -> str:
        return {
            "continuous": "regression",
            "positive": "regression",
            "share": "cross_entropy",
            "rate": "cross_entropy",
            "count": "poisson",
            "binary": "binary",
        }[self.kind]


def expand(patterns: Iterable[str]) -> tuple[str, ...]:
    """Expand ``a__b__{x,y}`` brace lists into column names (FEATURES.md notation)."""
    out: list[str] = []
    for p in patterns:
        if "{" in p:
            head, rest = p.split("{", 1)
            body, tail = rest.split("}", 1)
            out.extend(expand([f"{head}{b}{tail}" for b in body.split(",")]))
        else:
            out.append(p)
    return tuple(dict.fromkeys(out))


CTX = expand(
    [
        "ctx__{week,home,spread,total_line,implied_team_total,implied_opp_total,favorite_flag}",
        "ctx__{roof_dome,roof_outdoors,surface_turf,rest_days,opp_rest_days,short_week,off_bye}",
        "ctx__{div_game,primetime}",
    ]
)
TEAM_STATS = (
    "plays",
    "sec_per_play",
    "neutral_sec_per_play",
    "pass_rate",
    "neutral_pass_rate",
    "proe",
    "early_down_pass_rate",
    "epa_per_play",
    "pass_epa_per_play",
    "rush_epa_per_play",
    "success_rate",
    "points",
    "pass_td",
    "rush_td",
    "rz_trips",
    "rz_td_rate",
    "gl_plays",
    "sack_rate_allowed",
    "int_rate",
    "fumble_rate",
    "team_targets",
    "team_carries",
    "pass_att",
    "dropbacks",
    "field_goal_att",
)
DEF_STATS = (
    "plays_faced",
    "sec_per_play_faced",
    "pass_rate_faced",
    "proe_faced",
    "epa_per_play_allowed",
    "pass_epa_allowed",
    "rush_epa_allowed",
    "success_rate_allowed",
    "ypc_allowed",
    "rush_td_rate_allowed",
    "rec_td_rate_allowed",
    "sack_rate",
    "int_rate",
    "points_allowed",
    "rz_td_rate_allowed",
    "qb_pass_yds_allowed",
    "rb_rush_yds_allowed",
)
DST_STATS = (
    "dst_sacks",
    "dst_interceptions",
    "dst_fumble_recoveries",
    "dst_forced_fumbles",
    "dst_def_td",
    "dst_return_td",
    "dst_safeties",
    "dst_blocked_kicks",
    "dst_points_allowed",
    "dst_yards_allowed",
    "dst_sack_rate",
    "dst_int_rate",
)


def _windows(prefix: str, stats: Sequence[str], windows: Sequence[str]) -> tuple[str, ...]:
    return tuple(f"{prefix}__{s}__{w}" for s in stats for w in windows)


TEAM_ENV = (
    *CTX,
    *_windows("team", TEAM_STATS, ("l5", "ew", "ps")),
    "team__qb_change_flag",
    *_windows("def", DEF_STATS, ("l5", "ew", "ps")),
)
DST_ENV = (
    *CTX,
    *_windows("team", TEAM_STATS, ("ew", "ps")),
    "team__qb_change_flag",
    *_windows("def", DEF_STATS, ("ew", "ps")),
    *_windows("def", DST_STATS, ("l5", "ew", "std", "car")),
    *expand(["def__dst_{def_td,return_td,safeties,blocked_kicks}__{std_shrunk,car_shrunk}"]),
)
PLAYER_CONTEXT = (
    "pos_code",
    *expand(
        [
            "ctx__{week,home,spread,total_line,implied_team_total,roof_dome,roof_outdoors}",
            "ctx__{surface_turf,rest_days,short_week,off_bye,div_game,primetime}",
            "team__{plays,pass_rate,neutral_pass_rate,proe,points,epa_per_play}__ew",
            "team__{pass_epa_per_play,rush_epa_per_play,rz_trips,team_targets,team_carries}__ew",
            "team__qb_change_flag",
            "attr__{age,years_exp,rookie_flag,draft_round,draft_pick,undrafted_flag,height,weight}",
            "attr__{weeks_since_last_game,new_team_flag,weeks_on_team,inj_prev_status}",
            "attr__{inj_weeks_since_out,games_missed__l5}",
            "attr__games__{std_n,ps_n,car_n}",
            "depth__{rank,players_ahead,starter_flag,rank_change,same_pos_count,chart_age_days}",
        ]
    ),
)
SNAP = expand(["opp__snap_share__{l1,l3,l5,ew,std,ps,car}"])


def _player(*patterns: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*PLAYER_CONTEXT, *expand(patterns))))


SKILL = ("RB", "WR", "TE")
RUSHERS = ("QB", "RB", "WR")

STAGES: tuple[StageSpec, ...] = (
    # ---- stage 1: team environment (team_week) --------------------------- #
    StageSpec(
        "team_plays",
        "team_week",
        "y_plays",
        "continuous",
        "mae",
        "l5_mean",
        {"columns": ("team__plays__l5", "team__plays__ps")},
        features=TEAM_ENV,
        note="L5 mean of offensive plays (fallback prior season, league mean)",
    ),
    StageSpec(
        "team_pass_rate",
        "team_week",
        "y_pass_rate",
        "rate",
        "mae",
        "ew_spread",
        {"columns": ("team__pass_rate__ew", "team__pass_rate__ps")},
        weight="y_plays",
        features=TEAM_ENV,
        monotone={"team__pass_rate__ew": 1},
        note="EW dropbacks per play plus b * spread (one parameter, least squares)",
    ),
    StageSpec(
        "team_off_td",
        "team_week",
        "y_offensive_td",
        "count",
        "poisson",
        "implied_ratio",
        features=TEAM_ENV,
        monotone={"ctx__implied_team_total": 1},
        note="implied team total / k with k fitted (MODELING.md quotes 7; fitted k is about 9.4)",
    ),
    StageSpec(
        "team_fg_att",
        "team_week",
        "y_field_goal_att",
        "count",
        "poisson",
        "ew_blend",
        {"columns": ("team__field_goal_att__ew", "team__field_goal_att__ps")},
        features=TEAM_ENV,
        note="EW field-goal attempts shrunk to the league mean (weight fitted)",
    ),
    StageSpec(
        "team_two_pt_att",
        "team_week",
        "y_two_pt_att",
        "count",
        "poisson",
        "implied_ratio",
        features=TEAM_ENV,
        note="implied team total times a fitted rate",
    ),
    # ---- stage 2: opportunity shares (player_week, played rows) ---------- #
    StageSpec(
        "share_target",
        "player_week",
        "y_target_share",
        "share",
        "mae",
        "share_ew",
        {
            "columns": (
                "opp__target_share__ew",
                "opp__prev_team_target_share__ew",
                "opp__target_share__ps",
                "opp__target_share__car",
            )
        },
        positions=SKILL,
        weight="tm_targets",
        features=_player(
            "opp__target_share__{l1,l3,l5,ew,std,ps,car}",
            "opp__air_yards_share__{l3,l5,ew,std,ps}",
            "opp__rz_target_share__{ew,ew_shrunk}",
            "opp__ez_target_share__ew",
            "opp__route_proxy__{l3,ew,std}",
            "opp__targets__{l3,ew,std,ps}",
            "opp__adot__{ew,car}",
            "opp__wopr__ew",
            "opp__prev_team_target_share__ew",
            "opp__prev_team_snap_share__ew",
            "opp__rush_share__ew",
            "def__tgt_share_allowed_{rb,wr,te}__{ew,std}",
            "opp__snap_share__{l1,l3,l5,ew,std,ps,car}",
        ),
        monotone={"opp__target_share__ew": 1},
        final_report_features=AVAIL_SHARE_FEATURES,
        note="EW target share (fallback previous team, prior season, career, depth prior)",
    ),
    StageSpec(
        "share_carry",
        "player_week",
        "y_carry_share",
        "share",
        "mae",
        "share_ew",
        {
            "columns": (
                "opp__rush_share__ew",
                "opp__prev_team_rush_share__ew",
                "opp__rush_share__ps",
                "opp__rush_share__car",
            )
        },
        positions=("QB", "RB", "WR", "TE"),
        weight="tm_carries",
        features=_player(
            "opp__rush_share__{l1,l3,l5,ew,std,ps,car}",
            "opp__gl_carry_share__{ew,ew_shrunk}",
            "opp__rz_carry_share__ew",
            "opp__carries__{l3,ew,std,ps}",
            "opp__prev_team_rush_share__ew",
            "opp__prev_team_snap_share__ew",
            "opp__designed_rush_rate__{ew,car}",
            "opp__scramble_rate__ew",
            "opp__target_share__ew",
            "def__{ypc_allowed,rush_epa_allowed}__ew",
            "opp__snap_share__{l1,l3,l5,ew,std,ps,car}",
        ),
        monotone={"opp__rush_share__ew": 1},
        final_report_features=AVAIL_SHARE_FEATURES,
        note="EW share of team carries (TEs have no window and use the depth prior)",
    ),
    StageSpec(
        "share_dropback",
        "player_week",
        "y_dropback_share",
        "share",
        "mae",
        "share_ew",
        {
            "columns": (
                "opp__dropback_share__ew",
                "opp__dropback_share__ps",
                "opp__dropback_share__car",
            )
        },
        positions=("QB",),
        weight="tm_dropbacks",
        features=_player(
            "opp__dropback_share__{l1,l3,l5,ew,std,ps,car}",
            "opp__dropbacks__{l1,l3,ew,std}",
            "opp__pass_att__{ew,std}",
            "opp__snap_share__{l1,l3,l5,ew,std,ps,car}",
        ),
        monotone={"opp__dropback_share__ew": 1},
        final_report_features=AVAIL_SHARE_FEATURES,
        note="EW dropback share of QBs",
    ),
    StageSpec(
        "share_gl_carry",
        "player_week",
        "y_gl_carry_share",
        "share",
        "mae",
        "share_ew",
        {"columns": ("opp__gl_carry_share__ew_shrunk", "opp__gl_carry_share__ps")},
        positions=RUSHERS,
        weight="tm_gl_carries",
        features=_player(
            "opp__gl_carry_share__{l1,l3,l5,ew,std,ps,car,ew_shrunk}",
            "opp__rush_share__{ew,std,ps}",
            "opp__rz_carry_share__{l3,l5,ew,std,ps}",
            "opp__snap_share__{l3,ew,std}",
        ),
        monotone={"opp__gl_carry_share__ew_shrunk": 1},
        note="shrunk EW goal-line carry share (TD weight fallback and stage-4 input)",
    ),
    StageSpec(
        "share_rz_target",
        "player_week",
        "y_rz_target_share",
        "share",
        "mae",
        "share_ew",
        {"columns": ("opp__rz_target_share__ew_shrunk", "opp__rz_target_share__ps")},
        positions=SKILL,
        weight="tm_rz_targets",
        features=_player(
            "opp__rz_target_share__{l1,l3,l5,ew,std,ps,car,ew_shrunk}",
            "opp__ez_target_share__{l3,l5,ew,std,ps}",
            "opp__target_share__{ew,std,ps}",
            "opp__snap_share__{l3,ew,std}",
        ),
        monotone={"opp__rz_target_share__ew_shrunk": 1},
        note="shrunk EW red-zone target share",
    ),
    # ---- stage 3: efficiency --------------------------------------------- #
    StageSpec(
        "eff_catch_rate",
        "player_week",
        "y_catch_rate",
        "rate",
        "mae",
        "rate_shrunk",
        {"columns": ("eff__catch_rate__ew_shrunk", "eff__catch_rate__car_shrunk")},
        positions=SKILL,
        weight="y_targets",
        features=_player(
            "eff__catch_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}",
            "eff__yards_per_target__{ew_shrunk,car_shrunk}",
            "eff__epa_per_target__{ew_shrunk,car_shrunk}",
            "opp__adot__{ew,car}",
            "opp__target_share__ew",
            "eff__ngs_{avg_separation,avg_cushion}__ew",
            "def__{pass_epa_allowed,epa_per_play_allowed}__ew",
        ),
        monotone={"eff__catch_rate__ew_shrunk": 1},
        note="shrunk EW receptions per target",
    ),
    StageSpec(
        "eff_ypr",
        "player_week",
        "y_ypr",
        "positive",
        "mae",
        "rate_shrunk",
        {
            "columns": (
                "eff__yards_per_reception__ew_shrunk",
                "eff__yards_per_reception__car_shrunk",
            )
        },
        positions=SKILL,
        weight="y_receptions",
        features=_player(
            "eff__yards_per_reception__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n}",
            "eff__yac_per_rec__{ew,ew_shrunk,car_shrunk}",
            "eff__yards_per_target__{ew_shrunk,car_shrunk}",
            "eff__exp_rec_yds_per_target__{ew_shrunk,car_shrunk}",
            "opp__adot__{ew,car}",
            "eff__ngs_{yac_above_expectation,intended_air_yards}__ew",
            "def__ypt_allowed_{rb,wr,te}__{ew,std_shrunk}",
            "def__pass_epa_allowed__ew",
        ),
        monotone={"eff__yards_per_reception__ew_shrunk": 1},
        clip=(2.0, 30.0),
        note="shrunk EW yards per reception",
    ),
    StageSpec(
        "eff_ypc",
        "player_week",
        "y_yards_per_carry",
        "positive",
        "mae",
        "rate_shrunk",
        {"columns": ("eff__yards_per_carry__ew_shrunk", "eff__yards_per_carry__car_shrunk")},
        positions=RUSHERS,
        weight="y_carries",
        features=_player(
            "eff__yards_per_carry__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n}",
            "eff__rush_first_down_rate__{ew_shrunk,car_shrunk}",
            "eff__epa_per_carry__{ew_shrunk,car_shrunk}",
            "eff__exp_rush_yds_per_carry__{ew_shrunk,car_shrunk}",
            "eff__ngs_{ryoe_per_att,rush_efficiency}__ew",
            "opp__scramble_rate__ew",
            "def__ypc_allowed__{ew,std_shrunk}",
            "def__rush_epa_allowed__ew",
        ),
        monotone={"eff__yards_per_carry__ew_shrunk": 1, "def__ypc_allowed__std_shrunk": 1},
        clip=(0.5, 12.0),
        note="shrunk EW yards per carry",
    ),
    StageSpec(
        "eff_comp_rate",
        "player_week",
        "y_completion_rate",
        "rate",
        "mae",
        "rate_shrunk",
        {"columns": ("eff__completion_rate__ew_shrunk", "eff__completion_rate__car_shrunk")},
        positions=("QB",),
        weight="y_pass_att",
        features=_player(
            "eff__completion_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}",
            "eff__cpoe__{ew_shrunk,car_shrunk}",
            "eff__yards_per_attempt__{ew_shrunk,car_shrunk}",
            "eff__ngs_{time_to_throw,aggressiveness,intended_air_yards}__ew",
            "def__{pass_epa_allowed,epa_per_play_allowed}__ew",
        ),
        monotone={"eff__completion_rate__ew_shrunk": 1},
        note="shrunk EW completion rate (consistency check only after D-057)",
    ),
    StageSpec(
        "eff_int_rate",
        "player_week",
        "y_int_per_attempt",
        "rate",
        "mae",
        "rate_shrunk",
        {"columns": ("eff__int_rate__ew_shrunk", "eff__int_rate__car_shrunk")},
        positions=("QB",),
        weight="y_pass_att",
        features=_player(
            "eff__int_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}",
            "eff__ngs_aggressiveness__ew",
            "def__int_rate__{ew,std_shrunk}",
            "team__int_rate__ew",
        ),
        monotone={"def__int_rate__std_shrunk": 1},
        note="shrunk EW interceptions per attempt",
    ),
    StageSpec(
        "eff_sack_rate",
        "player_week",
        "y_sack_rate",
        "rate",
        "mae",
        "rate_shrunk",
        {"columns": ("eff__sack_rate__ew_shrunk", "eff__sack_rate__car_shrunk")},
        positions=("QB",),
        weight="y_dropbacks",
        features=_player(
            "eff__sack_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}",
            "eff__ngs_time_to_throw__ew",
            "def__sack_rate__{ew,std_shrunk}",
            "team__sack_rate_allowed__ew",
        ),
        monotone={"def__sack_rate__std_shrunk": 1},
        note="shrunk EW sacks per dropback",
    ),
    StageSpec(
        "eff_scramble_rate",
        "player_week",
        "y_scramble_rate",
        "rate",
        "mae",
        "rate_shrunk",
        {"columns": ("opp__scramble_rate__ew", "opp__scramble_rate__car")},
        positions=("QB",),
        weight="y_dropbacks",
        features=_player(
            "opp__scramble_rate__{ew,std,ps,car}",
            "opp__designed_rush_rate__{ew,std,car}",
            "eff__sack_rate__ew_shrunk",
            "def__sack_rate__ew",
        ),
        note="EW scrambles per dropback (needed for the D-057 carry split)",
    ),
    StageSpec(
        "eff_fumble_rate",
        "player_week",
        "y_fumble_rate",
        "rate",
        "mae",
        "position_prior",
        positions=("QB", "RB", "WR", "TE"),
        weight="y_touches",
        features=_player(
            "team__fumble_rate__{ew,std,ps}",
            "opp__{carries,targets,dropbacks}__ew",
            "def__{epa_per_play_allowed,success_rate_allowed}__ew",
        ),
        note="position rate (no lagged player fumble window exists in the features)",
    ),
    # ---- stage 4: touchdown shares --------------------------------------- #
    StageSpec(
        "td_pass_share",
        "player_week",
        "y_pass_td_share",
        "share",
        "mae",
        "share_ew",
        {"columns": ("opp__pass_td_share__ew_shrunk", "opp__pass_td_share__ps")},
        positions=SKILL,
        weight="tm_rec_td",
        features=_player(
            "opp__pass_td_share__{l3,l5,ew,std,ps,ew_shrunk}",
            "opp__rz_target_share__{ew,std,ew_shrunk}",
            "opp__ez_target_share__{ew,std}",
            "opp__target_share__{ew,std}",
            "eff__td_per_target__{ew_shrunk,car_shrunk}",
            "eff__exp_rec_td_per_target__{ew_shrunk,car_shrunk}",
            "def__rec_td_rate_allowed__std_shrunk",
        ),
        monotone={"opp__pass_td_share__ew_shrunk": 1},
        final_report_features=AVAIL_SHARE_FEATURES,
        note="shrunk EW share of team receiving TDs",
    ),
    StageSpec(
        "td_rush_share",
        "player_week",
        "y_rush_td_share",
        "share",
        "mae",
        "share_ew",
        {"columns": ("opp__rush_td_share__ew_shrunk", "opp__rush_td_share__ps")},
        positions=RUSHERS,
        weight="tm_rush_td",
        features=_player(
            "opp__rush_td_share__{l3,l5,ew,std,ps,ew_shrunk}",
            "opp__gl_carry_share__{ew,std,ew_shrunk}",
            "opp__rz_carry_share__{ew,std}",
            "opp__rush_share__{ew,std}",
            "eff__td_per_carry__{ew_shrunk,car_shrunk}",
            "eff__exp_rush_td_per_carry__{ew_shrunk,car_shrunk}",
            "def__rush_td_rate_allowed__std_shrunk",
        ),
        monotone={"opp__rush_td_share__ew_shrunk": 1},
        final_report_features=AVAIL_SHARE_FEATURES,
        note="shrunk EW share of team rushing TDs",
    ),
    # ---- availability (all rows, lagged information only) ---------------- #
    StageSpec(
        "availability",
        "player_week",
        "y_played",
        "binary",
        "logloss",
        "availability_logit",
        played_only=False,
        features=_player(
            "opp__snap_share__{l1,l3,l5,ew,std,ps}",
            "opp__{targets,carries,dropbacks}__{l1,ew}",
            "opp__fg_att__ew",
        ),
        monotone={"attr__games_missed__l5": -1},
        note="per-position logistic regression on lagged availability and depth",
    ),
    # ---- kicker ----------------------------------------------------------- #
    StageSpec(
        "k_fg_pct",
        "player_week",
        "y_fg_pct",
        "rate",
        "mae",
        "rate_shrunk",
        {"columns": ("eff__fg_pct__car_shrunk", "eff__fg_pct__ew_shrunk")},
        positions=("K",),
        weight="y_fg_att",
        features=_player(
            "eff__fg_pct__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}",
            "eff__fg_pct_{0_19,20_29,30_39,40_49,50_59,60p}__car_shrunk",
            "opp__fg_att_share_{30_39,40_49,50_59,60p}__ew_shrunk",
            "opp__fg_att__ew",
            "eff__pat_pct__car_shrunk",
        ),
        monotone={"eff__fg_pct__car_shrunk": 1},
        note="shrunk career FG% (bucket make rates are the shrunk band features)",
    ),
    # ---- DST -------------------------------------------------------------- #
    StageSpec(
        "dst_sacks",
        "dst_week",
        "y_sacks",
        "count",
        "poisson",
        "dst_blend",
        {
            "own": "def__dst_sack_rate__ew",
            "opp": "team__sack_rate_allowed__ew",
            "volume": "team__dropbacks__ew",
        },
        features=DST_ENV,
        monotone={"team__sack_rate_allowed__ew": 1},
        note="sqrt(own sack rate x opponent sack rate allowed) x opponent dropbacks (EW)",
    ),
    StageSpec(
        "dst_int",
        "dst_week",
        "y_interceptions",
        "count",
        "poisson",
        "dst_blend",
        {
            "own": "def__dst_int_rate__ew",
            "opp": "team__int_rate__ew",
            "volume": "team__pass_att__ew",
        },
        features=DST_ENV,
        monotone={"team__int_rate__ew": 1},
        note="sqrt(own INT rate x opponent INT rate) x opponent attempts (EW)",
    ),
    StageSpec(
        "dst_fum_rec",
        "dst_week",
        "y_fumble_recoveries",
        "count",
        "poisson",
        "dst_blend",
        {
            "own_count": "def__dst_fumble_recoveries__ew",
            "own_den": "def__plays_faced__ew",
            "opp": "team__fumble_rate__ew",
            "volume": "team__plays__ew",
        },
        features=DST_ENV,
        note="sqrt(own recoveries per play x opponent lost fumbles per play) x plays",
    ),
    StageSpec(
        "dst_ff",
        "dst_week",
        "y_forced_fumbles",
        "count",
        "poisson",
        "ew_blend",
        {"columns": ("def__dst_forced_fumbles__ew", "def__dst_forced_fumbles__car")},
        features=DST_ENV,
        note="EW forced fumbles shrunk to the league mean (weight fitted)",
    ),
    StageSpec(
        "dst_def_td",
        "dst_week",
        "y_def_td",
        "count",
        "poisson",
        "direct",
        {"columns": ("def__dst_def_td__std_shrunk", "def__dst_def_td__car_shrunk")},
        features=DST_ENV,
        note="shrunk season-to-date defensive TDs per game",
    ),
    StageSpec(
        "dst_ret_td",
        "dst_week",
        "y_return_td",
        "count",
        "poisson",
        "direct",
        {"columns": ("def__dst_return_td__std_shrunk", "def__dst_return_td__car_shrunk")},
        features=DST_ENV,
        note="shrunk season-to-date return TDs per game",
    ),
    StageSpec(
        "dst_safety",
        "dst_week",
        "y_safeties",
        "count",
        "poisson",
        "direct",
        {"columns": ("def__dst_safeties__std_shrunk", "def__dst_safeties__car_shrunk")},
        features=DST_ENV,
        note="shrunk season-to-date safeties per game",
    ),
    StageSpec(
        "dst_blocks",
        "dst_week",
        "y_blocked_kicks",
        "count",
        "poisson",
        "direct",
        {"columns": ("def__dst_blocked_kicks__std_shrunk", "def__dst_blocked_kicks__car_shrunk")},
        features=DST_ENV,
        note="shrunk season-to-date blocked kicks per game",
    ),
)

STAGE_BY_NAME: dict[str, StageSpec] = {s.name: s for s in STAGES}


def stage(name: str) -> StageSpec:
    return STAGE_BY_NAME[name]


__all__ = ["AVAIL_SHARE_FEATURES", "STAGES", "STAGE_BY_NAME", "StageSpec", "expand", "stage"]
