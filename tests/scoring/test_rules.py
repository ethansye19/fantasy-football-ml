"""Unit tests for the scoring engine on hand-built stat lines."""

from __future__ import annotations

import json
import time
import warnings

import numpy as np
import polars as pl
import pytest

from ffml.scoring import STAT_COLUMNS, ScoringRules, UnsupportedScoringKeyWarning
from ffml.scoring.rules import SUPPORTED_KEYS, _reset_warning_cache


def rows(*specs: dict) -> pl.DataFrame:
    """Frame with every stat column (zeros) plus the given positions and stats."""
    records = []
    for spec in specs:
        rec = {c: 0 for c in STAT_COLUMNS}
        rec["entity_id"] = spec.get("entity_id", "x")
        rec["position"] = spec.get("position", "WR")
        rec["season"] = 2024
        rec["week"] = 1
        rec.update({k: v for k, v in spec.items() if k in STAT_COLUMNS})
        records.append(rec)
    return pl.DataFrame(records)


def pts(rules: ScoringRules, *specs: dict) -> list[float]:
    return [round(x, 6) for x in rules.score(rows(*specs)).to_list()]


def rules(**settings: float) -> ScoringRules:
    return ScoringRules.from_dict(settings)


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #


def test_presets_receiver_and_quarterback():
    wr = {"position": "WR", "rec": 5, "targets": 8, "rec_yds": 80, "rec_td": 1}
    qb = {
        "position": "QB",
        "pass_att": 35,
        "pass_cmp": 24,
        "pass_yds": 300,
        "pass_td": 2,
        "pass_int": 1,
        "rush_att": 4,
        "rush_yds": 20,
        "fum_lost": 1,
    }
    ppr = ScoringRules.from_preset("ppr")
    half = ScoringRules.from_preset("half")
    std = ScoringRules.from_preset("std")
    assert pts(ppr, wr) == [19.0]
    assert pts(half, wr) == [16.5]
    assert pts(std, wr) == [14.0]
    # 12 + 8 - 2 + 2 - 2 under every preset (no completion or yardage bonuses)
    for preset in (ppr, half, std):
        assert pts(preset, qb) == [18.0]


def test_preset_aliases_and_unknown():
    assert ScoringRules.from_preset("half_ppr") == ScoringRules.from_preset("half")
    assert ScoringRules.from_preset("STANDARD") == ScoringRules.from_preset("std")
    with pytest.raises(ValueError, match="unknown scoring preset"):
        ScoringRules.from_preset("superflex")


def test_preset_kicker_and_dst():
    ppr = ScoringRules.from_preset("ppr")
    k = {
        "position": "K",
        "fg_made_20_29": 1,
        "fg_made_40_49": 1,
        "fg_made_50_59": 1,
        "fg_made_60p": 1,
        "fg_missed": 1,
        "xp_made": 3,
        "xp_missed": 1,
    }
    assert pts(ppr, k) == [3 + 4 + 5 + 5 - 1 + 3 - 1]
    dst = {
        "position": "DST",
        "sacks": 3,
        "def_int": 1,
        "fum_rec": 1,
        "def_td": 1,
        "st_td": 1,
        "safeties": 1,
        "blocked_kicks": 1,
        "pts_allowed": 10,
        "yds_allowed": 250,
    }
    assert pts(ppr, dst) == [3 + 2 + 2 + 6 + 6 + 2 + 2 + 4]


# --------------------------------------------------------------------------- #
# Key families
# --------------------------------------------------------------------------- #


def test_passing_keys():
    r = rules(pass_yd=0.04, pass_td=4, pass_int=-2, pass_cmp=0.5, pass_inc=-0.25, pass_att=0.1)
    qb = {
        "position": "QB",
        "pass_att": 30,
        "pass_cmp": 20,
        "pass_yds": 250,
        "pass_td": 2,
        "pass_int": 1,
    }
    expected = 250 * 0.04 + 2 * 4 - 2 + 20 * 0.5 - 10 * 0.25 + 30 * 0.1
    assert pts(r, qb) == [round(expected, 6)]


def test_rushing_keys():
    r = rules(rush_yd=0.1, rush_td=6, rush_att=0.25)
    rb = {"position": "RB", "rush_att": 18, "rush_yds": 95, "rush_td": 1}
    assert pts(r, rb) == [round(9.5 + 6 + 4.5, 6)]


def test_receiving_keys_and_position_bonuses():
    r = rules(
        rec=0.5,
        rec_yd=0.1,
        rec_td=6,
        rec_tgt=0.1,
        bonus_rec_te=0.5,
        bonus_rec_rb=0.25,
        bonus_rec_wr=0.1,
    )
    line = {"rec": 4, "targets": 6, "rec_yds": 40, "rec_td": 0}
    base = 4 * 0.5 + 4.0 + 0.6
    te, rb, wr, qb = pts(
        r,
        {**line, "position": "TE"},
        {**line, "position": "RB"},
        {**line, "position": "WR"},
        {**line, "position": "QB"},
    )
    assert te == round(base + 4 * 0.5, 6)
    assert rb == round(base + 4 * 0.25, 6)
    assert wr == round(base + 4 * 0.1, 6)
    assert qb == round(base, 6)


@pytest.mark.parametrize(
    ("key100", "key200", "column"),
    [
        ("bonus_rec_yd_100", "bonus_rec_yd_200", "rec_yds"),
        ("bonus_rush_yd_100", "bonus_rush_yd_200", "rush_yds"),
    ],
)
def test_yardage_bonus_bands_are_exclusive(key100, key200, column):
    r = rules(**{key100: 3, key200: 5})
    out = pts(r, *[{"position": "RB", column: y} for y in (99, 100, 199, 200, 260)])
    assert out == [0, 3, 3, 5, 5]


def test_passing_bonus_bands():
    r = rules(bonus_pass_yd_300=2, bonus_pass_yd_400=4, bonus_pass_cmp_25=1)
    out = pts(
        r,
        {"position": "QB", "pass_yds": 299, "pass_cmp": 24},
        {"position": "QB", "pass_yds": 300, "pass_cmp": 25},
        {"position": "QB", "pass_yds": 399},
        {"position": "QB", "pass_yds": 400},
    )
    assert out == [0, 3, 2, 4]


def test_combined_rush_rec_bonus_and_carry_bonus():
    r = rules(bonus_rush_rec_yd_100=2, bonus_rush_rec_yd_200=4, bonus_rush_att_20=1)
    out = pts(
        r,
        {"position": "RB", "rush_yds": 60, "rec_yds": 39},
        {"position": "RB", "rush_yds": 60, "rec_yds": 50, "rush_att": 20},
        {"position": "RB", "rush_yds": 150, "rec_yds": 50},
    )
    assert out == [0, 3, 4]


def test_two_point_uses_mean_of_present_keys():
    line = {"position": "WR", "two_pt": 1}
    assert pts(rules(pass_2pt=2, rush_2pt=2, rec_2pt=2), line) == [2.0]
    assert pts(rules(pass_2pt=1, rec_2pt=2), line) == [1.5]
    assert pts(rules(rush_2pt=2), line) == [2.0]
    assert pts(rules(rec=1), line) == [0.0]


def test_misc_offense_keys_and_return_td_precedence():
    line = {"position": "WR", "fum_lost": 1, "ret_td": 1}
    assert pts(rules(fum_lost=-2), line) == [-2.0]
    # fum_rec_td maps to ret_td for offense when st_td is absent
    assert pts(rules(fum_rec_td=6), line) == [6.0]
    # st_td (special-teams player touchdown) wins when present
    assert pts(rules(fum_rec_td=6, st_td=4), line) == [4.0]


def test_kicking_keys():
    k = {
        "position": "K",
        "fg_made_0_19": 1,
        "fg_made_20_29": 1,
        "fg_made_30_39": 1,
        "fg_made_40_49": 1,
        "fg_made_50_59": 1,
        "fg_made_60p": 1,
        "fg_missed": 2,
        "xp_made": 4,
        "xp_missed": 1,
    }
    r = rules(
        fgm_0_19=1,
        fgm_20_29=2,
        fgm_30_39=3,
        fgm_40_49=4,
        fgm_50_59=5,
        fgm_60p=6,
        fgmiss=-1,
        xpm=1,
        xpmiss=-2,
    )
    assert pts(r, k) == [21 - 2 + 4 - 2]
    # generic fgm adds per made field goal, on top of bucket keys
    assert pts(rules(fgm=3), k) == [18.0]
    assert pts(rules(fgm=3, fgm_40_49=1), k) == [19.0]
    # legacy fgm_50p covers 50-59 and 60+
    assert pts(rules(fgm_50p=5), k) == [10.0]


def test_dst_event_keys_and_st_td_precedence():
    d = {
        "position": "DST",
        "sacks": 4,
        "def_int": 2,
        "fum_rec": 1,
        "safeties": 1,
        "blocked_kicks": 1,
        "def_td": 1,
        "st_td": 1,
        "pts_allowed": 21,
    }
    r = rules(sack=1, int=2, fum_rec=2, safe=2, blk_kick=2, def_td=6)
    assert pts(r, d) == [4 + 4 + 2 + 2 + 2 + 6]
    assert pts(rules(def_st_td=6), d) == [6.0]
    assert pts(rules(st_td=6), d) == [6.0]
    assert pts(rules(def_st_td=6, st_td=3), d) == [6.0]


def test_points_allowed_bands_boundaries():
    r = rules(
        pts_allow_0=10,
        pts_allow_1_6=7,
        pts_allow_7_13=4,
        pts_allow_14_20=1,
        pts_allow_21_27=0.5,
        pts_allow_28_34=-1,
        pts_allow_35p=-4,
    )
    allowed = [0, 1, 6, 7, 13, 14, 20, 21, 27, 28, 34, 35, 52]
    expect = [10, 7, 7, 4, 4, 1, 1, 0.5, 0.5, -1, -1, -4, -4]
    assert pts(r, *[{"position": "DST", "pts_allowed": a} for a in allowed]) == expect


def test_yards_allowed_bands_boundaries():
    keys = [
        "yds_allow_0_100",
        "yds_allow_100_199",
        "yds_allow_200_299",
        "yds_allow_300_349",
        "yds_allow_350_399",
        "yds_allow_400_449",
        "yds_allow_450_499",
        "yds_allow_500_549",
        "yds_allow_550p",
    ]
    r = rules(**{k: float(i + 1) for i, k in enumerate(keys)})
    yards = [0, 99, 100, 199, 200, 299, 300, 349, 350, 399, 400, 449, 450, 499, 500, 549, 550, 700]
    expect = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9]
    assert pts(r, *[{"position": "DST", "yds_allowed": y} for y in yards]) == expect


def test_dst_keys_do_not_apply_to_offense_and_vice_versa():
    r = ScoringRules.from_preset("ppr")
    # an offense row has pts_allowed = 0 but must not earn the shutout band
    assert pts(r, {"position": "WR"}) == [0.0]
    # a DST row with stray offense stats gets only DST points (14-20 band = 1)
    assert pts(r, {"position": "DST", "rec": 5, "rec_yds": 50, "pts_allowed": 17}) == [1.0]


def test_missing_columns_and_nulls_count_as_zero():
    r = ScoringRules.from_preset("ppr")
    frame = pl.DataFrame(
        {"position": ["WR", "WR", "DST"], "rec": [3, None, 0], "pts_allowed": [0, 0, None]}
    )
    assert r.score(frame).to_list() == [3.0, 0.0, 10.0]


def test_position_column_required():
    with pytest.raises(ValueError, match="position"):
        ScoringRules.from_preset("ppr").score(pl.DataFrame({"rec": [1]}))


def test_with_points_and_lazy_frames():
    r = ScoringRules.from_preset("ppr")
    frame = rows({"position": "WR", "rec": 2})
    assert r.with_points(frame)["points"].to_list() == [2.0]
    assert r.score(frame.lazy()).to_list() == [2.0]


def test_empty_rules_score_zero():
    r = ScoringRules.from_sleeper(None)
    assert r.settings == ()
    assert pts(r, {"position": "QB", "pass_yds": 300}) == [0.0]


def test_scoring_is_vectorised_on_a_million_rows():
    n = 1_000_000
    rng = np.random.default_rng(0)
    frame = pl.DataFrame(
        {
            "position": rng.choice(["QB", "RB", "WR", "TE", "K", "DST"], n),
            **{c: rng.integers(0, 40, n).astype(np.int16) for c in STAT_COLUMNS},
        }
    )
    r = ScoringRules.from_preset("ppr")
    start = time.perf_counter()
    out = r.score(frame)
    assert out.len() == n
    assert time.perf_counter() - start < 10.0


# --------------------------------------------------------------------------- #
# Unsupported keys and the hash
# --------------------------------------------------------------------------- #


def test_unsupported_keys_listed_warned_once_and_ignored():
    _reset_warning_cache()
    settings = {
        "rec": 1,
        "pass_sack": -1,
        "ff": 1,
        "pass_td_40p": 2,
        "idp_tkl": 1,
        "fum": -1,
        "def_pass_def": 1,
        "def_2pt": 2,
        "st_ff": 1,
        "st_fum_rec": 1,
        "def_st_ff": 1,
        "def_st_fum_rec": 1,
        "made_up_key": 3,
    }
    with pytest.warns(UnsupportedScoringKeyWarning, match="pass_sack"):
        r = ScoringRules.from_sleeper(settings)
    assert set(r.unsupported_keys) == set(settings) - {"rec"}
    assert r.supported_keys == ("rec",)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        again = ScoringRules.from_sleeper(settings)  # same key set: no second warning
    assert pts(again, {"position": "WR", "rec": 3, "fum_lost": 1}) == [3.0]
    _reset_warning_cache()


def test_scoring_hash_stability():
    a = ScoringRules.from_sleeper({"rec": 1, "pass_td": 4, "rush_yd": 0.1})
    b = ScoringRules.from_sleeper({"rush_yd": 0.1, "pass_td": 4.0, "rec": 1, "fum": 0})
    c = ScoringRules.from_sleeper({"rec": 0.5, "pass_td": 4, "rush_yd": 0.1})
    _reset_warning_cache()
    with pytest.warns(UnsupportedScoringKeyWarning):
        d = ScoringRules.from_sleeper({"rec": 1, "pass_td": 4, "rush_yd": 0.1, "ff": 1})
    assert a == b and a.scoring_hash == b.scoring_hash
    assert a.scoring_hash != c.scoring_hash
    assert d.scoring_hash == a.scoring_hash  # unsupported keys do not change scores
    assert len(a.scoring_hash) == 16 and int(a.scoring_hash, 16) >= 0
    # pinned value: changes only when the engine version or the hash recipe changes
    assert ScoringRules.from_preset("ppr").scoring_hash == "1ba3759dd4fab472"
    _reset_warning_cache()


def test_test_league_keys(fixtures_dir):
    league = json.loads((fixtures_dir / "sleeper" / "league.json").read_text(encoding="utf-8"))
    _reset_warning_cache()
    with pytest.warns(UnsupportedScoringKeyWarning):
        r = ScoringRules.from_sleeper(league["scoring_settings"])
    assert len(r.settings) == 41
    assert set(r.unsupported_keys) == {"def_st_ff", "def_st_fum_rec", "ff", "st_ff", "st_fum_rec"}
    assert set(r.supported_keys) <= SUPPORTED_KEYS
    assert len(r.supported_keys) == 36
    _reset_warning_cache()
