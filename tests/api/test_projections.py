"""BE-4.3 to BE-4.7 in private_test (fixture snapshot): labels, scoring at request time,
start/sit, waivers, trades, rest of season, caching, offseason, timing."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from api_factory import PRIVATE_LABELS, player_ids, write_snapshot
from api_helpers import assert_labeled, strings

from api.services.lineup import optimal
from api.services.season import SeasonService
from ffml.models.chain.summary import summarize_chain
from ffml.scoring.rules import ScoringRules

ENTS = player_ids()
BY_TEAM: dict[str, list[tuple[str, str]]] = {}
for _eid, _pos, _team in ENTS:
    BY_TEAM.setdefault(_team, []).append((_eid, _pos))


def _pick(team: str, pos: str, n: int = 0) -> str:
    return [e for e, p in BY_TEAM[team] if p == pos][n]


MY_PLAYERS = [
    _pick("KC", "QB"), _pick("KC", "RB", 0), _pick("BUF", "RB", 1), _pick("KC", "WR", 0),
    _pick("BUF", "WR", 2), _pick("KC", "TE"), _pick("KC", "K"), "KC", _pick("MIN", "WR", 2),
]
THEIR_PLAYERS = [
    _pick("DET", "QB"), _pick("DET", "RB", 0), _pick("DET", "RB", 1), _pick("DET", "WR", 0),
    _pick("DET", "WR", 1), _pick("DET", "TE"), _pick("DET", "K"), "DET", _pick("SF", "WR", 0),
]
LEAGUE = {
    "name": "Fixture league",
    "season": 2024,
    "num_teams": 2,
    "scoring": {"preset": "ppr"},
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"],
    "teams": [
        {"name": "Mine", "is_me": True, "players": MY_PLAYERS},
        {"name": "Theirs", "players": THEIR_PLAYERS},
    ],
}


@pytest.fixture
def env(make_client, api_dirs):
    run = write_snapshot(api_dirs["fixtures"], labels=PRIVATE_LABELS, n_sims=400)
    client = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    league = client.post("/leagues", json=LEAGUE).json()
    return client, league, run


def _routes(league):
    lid = league["id"]
    me = MY_PLAYERS
    return [
        ("GET", "/projections", None),
        ("GET", "/ros", None),
        ("GET", f"/leagues/{lid}/projections", None),
        ("POST", f"/leagues/{lid}/start-sit", {"player_ids": [me[3], me[4]]}),
        ("GET", f"/leagues/{lid}/waivers", None),
        ("POST", f"/leagues/{lid}/trades/evaluate", {"give": [me[1]], "receive": [THEIR_PLAYERS[3]]}),
        ("GET", f"/leagues/{lid}/ros", None),
    ]


def _call(client, method, path, body):
    return client.get(path) if method == "GET" else client.post(path, json=body)


# --------------------------------------------------------------------------- labels


def test_every_projection_route_serves_labeled_data(env):
    client, league, _ = env
    for method, path, body in _routes(league):
        r = _call(client, method, path, body)
        assert r.status_code == 200, (path, r.text)
        assert_labeled(r)
        b = r.json()
        assert b["environment"] == "private_test"
        assert b["run"]["source"] == "fixture"
        assert any("Not approved" in n for n in b["notices"])
        assert any("Fixture data" in n for n in b["notices"])


def test_trade_and_start_sit_carry_no_guarantees(env):
    client, league, _ = env
    for method, path, body in _routes(league):
        if "start-sit" in path or "trades" in path or "waivers" in path:
            text = _call(client, method, path, body).json()["disclaimer"]
            assert text.startswith("No guarantees")


# --------------------------------------------------------------------------- projections


def test_league_scoring_applied_at_request_time(env):
    client, league, _ = env
    lid = league["id"]
    ppr = client.get(f"/leagues/{lid}/projections", params={"position": "WR"}).json()
    client.put(f"/leagues/{lid}/settings", json={"scoring": {"preset": "std"}})
    std = client.get(f"/leagues/{lid}/projections", params={"position": "WR"}).json()
    assert ppr["scoring"]["hash"] != std["scoring"]["hash"]
    assert std["scoring"]["preset_like"] == "std"
    a = {p["player_id"]: p["median"] for p in ppr["players"]}
    b = {p["player_id"]: p["median"] for p in std["players"]}
    assert all(a[k] >= b[k] for k in a) and sum(a.values()) > sum(b.values()) + 10
    # a custom key changes the numbers without retraining
    client.put(f"/leagues/{lid}/settings", json={"scoring": {"preset": "std", "overrides": {"rec_td": 10}}})
    custom = client.get(f"/leagues/{lid}/projections", params={"position": "WR"}).json()
    assert custom["scoring"]["hash"] not in (ppr["scoring"]["hash"], std["scoring"]["hash"])


def test_summary_matches_the_chain_conventions(env):
    """Request-time numbers equal ffml.models.chain.summary for a preset (D-017, D-067)."""
    client, _, run = env
    body = client.get("/projections", params={"scoring": "half", "limit": 500}).json()
    sims = pl.read_parquet(run / "player_sims.parquet")
    ref, _ = summarize_chain(sims, ScoringRules.from_preset("half"))
    ref = {r["entity_id"]: r for r in ref.iter_rows(named=True)}
    checked = 0
    for p in body["players"]:
        r = ref[p["player_id"]]
        if p["low_sample"]:
            continue
        assert p["median"] == pytest.approx(r["pred_q50"], abs=0.011)
        assert p["floor"] == pytest.approx(r["pred_q10"], abs=0.011)
        assert p["ceiling"] == pytest.approx(r["pred_q90"], abs=0.011)
        assert p["active_prob"] == pytest.approx(r["active_prob"], abs=1e-4)
        if r["boom_prob"] is not None:
            assert p["boom_prob"] == pytest.approx(r["boom_prob"], abs=1e-4)
            assert p["bust_prob"] == pytest.approx(r["bust_prob"], abs=1e-4)
        checked += 1
    assert checked >= 40


def test_projection_filters_sort_and_pages(env):
    client, league, _ = env
    url = f"/leagues/{league['id']}/projections"
    all_rows = client.get(url, params={"limit": 500}).json()
    assert all_rows["total"] == len(ENTS) and all_rows["week"] == 10
    meds = [p["median"] for p in all_rows["players"] if p["median"] is not None]
    assert meds == sorted(meds, reverse=True)
    mine = client.get(url, params={"availability": "mine"}).json()
    assert {p["player_id"] for p in mine["players"]} == set(MY_PLAYERS)
    assert all(p["roster"]["is_mine"] for p in mine["players"])
    fa = client.get(url, params={"availability": "free_agents", "limit": 500}).json()
    assert fa["total"] == len(ENTS) - len(MY_PLAYERS) - len(THEIR_PLAYERS)
    assert all(p["roster"] is None for p in fa["players"])
    k = client.get(url, params={"position": "K"}).json()
    assert {p["boom_bust_confidence"] for p in k["players"]} == {"low"}
    qb = client.get(url, params={"position": "QB"}).json()
    assert {p["boom_bust_confidence"] for p in qb["players"]} == {"low"}  # D-097, D-104
    assert qb["boom_bust_low_confidence"] == ["DST", "K", "QB"]
    wr = client.get(url, params={"position": "WR"}).json()
    assert {p["boom_bust_confidence"] for p in wr["players"]} == {"normal"}
    assert [p["position_rank"] for p in qb["players"]] == list(range(1, 7))
    page = client.get(url, params={"limit": 5, "offset": 5}).json()
    assert [p["player_id"] for p in page["players"]] == [
        p["player_id"] for p in all_rows["players"][5:10]
    ]
    bust = client.get(url, params={"sort": "bust", "position": "WR"}).json()["players"]
    vals = [p["bust_prob"] for p in bust]
    assert vals == sorted(vals)
    dst = client.get(url, params={"search": "kc"}).json()["players"]
    assert "KC" in {p["player_id"] for p in dst}


def test_projection_errors(env):
    client, league, _ = env
    url = f"/leagues/{league['id']}/projections"
    r = client.get(url, params={"week": 11})
    assert r.status_code == 404 and r.json()["error"]["code"] == "week_not_available"
    assert r.json()["error"]["details"]["available_weeks"] == [10]
    assert client.get(url, params={"position": "LB"}).status_code == 422
    assert client.get(url, params={"limit": 0}).status_code == 422
    r = client.get("/leagues/00000000-0000-0000-0000-000000000000/projections")
    assert r.status_code == 404 and r.json()["error"]["code"] == "league_not_found"


def test_drivers_are_plain_language(env):
    client, league, _ = env
    players = client.get(f"/leagues/{league['id']}/projections").json()["players"]
    texts = [d["text"] for p in players for d in p["drivers"]]
    assert texts and all(t.endswith(".") and "pts" in t for t in texts)
    assert not any("_" in t for t in texts)


def test_scored_runs_are_cached_per_run_and_scoring(env):
    client, league, _ = env
    service = client.app.state.projections
    client.get(f"/leagues/{league['id']}/projections")
    client.get(f"/leagues/{league['id']}/projections")
    client.get("/projections", params={"scoring": "std"})
    keys = list(service._scored)
    assert len(keys) == 2 and len({k[0] for k in keys}) == 1


def test_warm_projections_under_300ms(env):
    client, league, _ = env
    url = f"/leagues/{league['id']}/projections"
    client.get(url)
    times = []
    for _ in range(5):
        t = time.perf_counter()
        assert client.get(url).status_code == 200
        times.append(time.perf_counter() - t)
    assert sorted(times)[2] < 0.3, times


REPLAY = Path(__file__).resolve().parents[2] / "data" / "predictions" / "season=2024" / "week=10"


@pytest.mark.skipif(not REPLAY.is_dir(), reason="no local 2024 week 10 replay snapshot")
def test_warm_projections_under_300ms_on_real_replay(make_client, api_dirs):
    """The real 2024 week 10 replay (567 entities x 2,000 simulations)."""
    import shutil

    target = api_dirs["predictions"] / "season=2024" / "week=10"
    shutil.copytree(REPLAY, target)
    client = make_client(app_env="private_test")
    assert client.get("/meta/approval").json()["run"]["source"] == "replay"
    client.get("/projections", params={"scoring": "ppr"})  # cold load and score
    times = []
    for _ in range(5):
        t = time.perf_counter()
        r = client.get("/projections", params={"scoring": "ppr", "limit": 500})
        times.append(time.perf_counter() - t)
        assert r.status_code == 200
    assert_labeled(r)
    assert sorted(times)[2] < 0.3, times


# --------------------------------------------------------------------------- start/sit


def test_start_sit(env):
    client, league, _ = env
    ids = [MY_PLAYERS[3], MY_PLAYERS[4], MY_PLAYERS[8]]
    r = client.post(f"/leagues/{league['id']}/start-sit", json={"player_ids": ids})
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["pairwise"]) == 3
    assert sum(b["p_highest"].values()) == pytest.approx(1.0, abs=1e-3)
    best = max(b["p_highest"], key=b["p_highest"].get)
    assert b["recommendation"]["player_id"] == best
    assert b["recommendation"]["strength"] in ("toss-up", "lean", "clear")
    for pair in b["pairwise"]:
        assert 0 <= pair["p_a_beats_b"] <= 1
    assert isinstance(b["explanation"], list)


@pytest.mark.parametrize(
    "ids,status,code",
    [
        (["00-0000001"], 422, "validation_error"),
        (["a", "b", "c", "d", "e"], 422, "validation_error"),
        (["KC", "KC"], 422, "validation_error"),
        (["KC", "00-9999999"], 422, "player_not_found"),
    ],
)
def test_start_sit_validation(env, ids, status, code):
    client, league, _ = env
    r = client.post(f"/leagues/{league['id']}/start-sit", json={"player_ids": ids})
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_pairwise_probabilities_are_paired(env):
    """P(A beats B) + P(B beats A) = 1 with ties split, from the same simulations."""
    client, league, _ = env
    a, b = MY_PLAYERS[3], THEIR_PLAYERS[3]
    ab = client.post(f"/leagues/{league['id']}/start-sit", json={"player_ids": [a, b]}).json()
    ba = client.post(f"/leagues/{league['id']}/start-sit", json={"player_ids": [b, a]}).json()
    assert ab["pairwise"][0]["p_a_beats_b"] + ba["pairwise"][0]["p_a_beats_b"] == pytest.approx(1)


# --------------------------------------------------------------------------- waivers


def test_waivers(env):
    client, league, _ = env
    r = client.get(f"/leagues/{league['id']}/waivers", params={"limit": 10})
    assert r.status_code == 200, r.text
    b = r.json()
    rostered = set(MY_PLAYERS) | set(THEIR_PLAYERS)
    assert b["weeks"][0] == 10 and b["weeks"][-1] == 18
    assert b["options"], "free agents should help a thin roster"
    scores = [o["score"] for o in b["options"]]
    assert scores == sorted(scores, reverse=True)
    for o in b["options"]:
        assert o["add"]["player_id"] not in rostered
        assert o["drop"] is None or o["drop"]["player_id"] in MY_PLAYERS
        assert o["gain_rest_of_season"] > 0
    assert {d["player_id"] for d in b["drop_candidates"]} <= set(MY_PLAYERS)
    qb = client.get(f"/leagues/{league['id']}/waivers", params={"position": "QB"}).json()
    assert all(o["add"]["position"] == "QB" for o in qb["options"])


def test_waivers_need_a_team(env):
    client, league, _ = env
    body = dict(LEAGUE, teams=[{"name": "A", "players": MY_PLAYERS}])
    other = client.post("/leagues", json=body).json()
    r = client.get(f"/leagues/{other['id']}/waivers")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_team_selected"
    team = other["teams"][0]["id"]
    ok = client.get(f"/leagues/{other['id']}/waivers", params={"league_team_id": team})
    assert ok.status_code == 200


# --------------------------------------------------------------------------- trades


def test_trade(env):
    client, league, _ = env
    body = {"give": [MY_PLAYERS[1]], "receive": [THEIR_PLAYERS[3], THEIR_PLAYERS[8]]}
    r = client.post(f"/leagues/{league['id']}/trades/evaluate", json=body)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["partner_team_id"] == next(t["id"] for t in league["teams"] if t["team_name"] == "Theirs")
    assert b["fairness"]["band"] in (
        "fair", "favors_you", "favors_partner", "lopsided_for_you", "lopsided_for_partner"
    )
    v = b["value"]
    assert v["net"] == pytest.approx(v["receive"] - v["give"], abs=0.02)
    assert v["give"] >= 0 and v["receive"] >= 0
    fit = b["roster_fit"]["mine"]
    assert fit["change"] == pytest.approx(fit["after"] - fit["before"], abs=0.02)
    assert set(b["replacement_level"]) >= {"QB", "RB", "WR", "TE", "K", "DST"}
    assert "No guarantees" in b["disclaimer"]


@pytest.mark.parametrize(
    "give,receive,code",
    [
        ([MY_PLAYERS[1]], [MY_PLAYERS[1]], "invalid_trade"),
        ([MY_PLAYERS[1]], ["00-0000999"], "invalid_trade"),
        ([THEIR_PLAYERS[1]], [THEIR_PLAYERS[2]], "invalid_trade"),
    ],
)
def test_trade_validation(env, give, receive, code):
    client, league, _ = env
    r = client.post(
        f"/leagues/{league['id']}/trades/evaluate", json={"give": give, "receive": receive}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_fairness_band_rules():
    from api.services.recommend import _fairness

    assert _fairness(3.0, 40.0, 43.0)["band"] == "fair"
    assert _fairness(12.0, 40.0, 52.0)["band"] == "favors_you"
    assert _fairness(-12.0, 52.0, 40.0)["band"] == "favors_partner"
    assert _fairness(30.0, 10.0, 40.0)["band"] == "lopsided_for_you"


# --------------------------------------------------------------------------- rest of season


def test_ros(env):
    client, league, _ = env
    b = client.get(f"/leagues/{league['id']}/ros", params={"limit": 500}).json()
    assert (b["first_week"], b["last_week"]) == (10, 18)
    ranks = [p["overall_rank"] for p in b["players"]]
    assert ranks == sorted(ranks)
    la = [p for p in b["players"] if p["team"] == "LA"]
    assert la and all(11 in p["bye_weeks"] and p["games_remaining"] == 8 for p in la)
    later = client.get(f"/leagues/{league['id']}/ros", params={"include_current_week": False}).json()
    assert later["first_week"] == 11
    top = {p["player_id"]: p["ros_mean"] for p in b["players"]}
    for p in later["players"]:
        assert p["ros_mean"] <= top[p["player_id"]] + 1e-6
    preset = client.get("/ros", params={"scoring": "half", "position": "TE"}).json()
    assert {p["position"] for p in preset["players"]} == {"TE"}


def test_low_confidence_positions_come_from_the_manifest(make_client, api_dirs):
    """D-104: low_confidence_positions in the manifest overrides the default set."""
    write_snapshot(
        api_dirs["private"],
        labels={**PRIVATE_LABELS, "low_confidence_positions": ["K"]},
        n_sims=100,
    )
    client = make_client(app_env="private_test")
    body = client.get("/projections", params={"limit": 500}).json()
    assert body["boom_bust_low_confidence"] == ["K"]
    by_pos: dict[str, set[str]] = {}
    for p in body["players"]:
        by_pos.setdefault(p["position"], set()).add(p["boom_bust_confidence"])
    assert by_pos["K"] == {"low"}
    assert by_pos["QB"] == {"normal"} and by_pos["DST"] == {"normal"}


def test_bad_manifest_low_confidence_falls_back_to_default(make_client, api_dirs):
    write_snapshot(
        api_dirs["private"],
        labels={**PRIVATE_LABELS, "low_confidence_positions": ["QB", "OL"]},
        n_sims=100,
    )
    client = make_client(app_env="private_test")
    body = client.get("/projections", params={"limit": 5}).json()
    assert body["boom_bust_low_confidence"] == ["DST", "K", "QB"]


def test_start_sit_unknown_body_ids_are_422_with_the_list(env):
    client, league, _ = env
    r = client.post(
        f"/leagues/{league['id']}/start-sit",
        json={"player_ids": ["KC", "00-9999999", "00-9999998"]},
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "player_not_found"
    assert err["details"]["player_ids"] == ["00-9999999", "00-9999998"]


# --------------------------------------------------------------------------- offseason


def test_offseason_pauses_weekly_features_for_live_runs(make_client, api_dirs):
    write_snapshot(api_dirs["private"], labels=PRIVATE_LABELS)
    client = make_client(app_env="private_test")
    client.app.state.season = SeasonService(
        Path("."),
        resolver=lambda _now: type(
            "S", (), {"season": 2026, "mode": "offseason", "projection_week": None,
                      "week_in_progress": False, "last_completed_week": 18,
                      "next_kickoff_utc": None}
        )(),
    )
    lg = client.post("/leagues", json=LEAGUE).json()
    r = client.get(f"/leagues/{lg['id']}/projections")
    assert r.status_code == 409 and r.json()["error"]["code"] == "offseason"
    r = client.post(f"/leagues/{lg['id']}/start-sit", json={"player_ids": MY_PLAYERS[3:5]})
    assert r.status_code == 409
    assert client.get(f"/leagues/{lg['id']}/ros").status_code == 200  # season values stay
    assert r.headers["X-FFML-Approval"] == "not-approved"


# --------------------------------------------------------------------------- lineup


def test_lineup_optimizer_handles_flex_combinations():
    slots = ["QB", "RB", "WR", "TE", "FLEX", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX"]
    pos = {"q1": "QB", "q2": "QB", "r1": "RB", "r2": "RB", "w1": "WR", "w2": "WR", "w3": "WR",
           "t1": "TE", "t2": "TE"}
    val = {"q1": 20, "q2": 18, "r1": 15, "r2": 9, "w1": 14, "w2": 12, "w3": 11, "t1": 8, "t2": 7}
    total, chosen = optimal(slots, list(pos), pos, val)
    # best 8 of 9 with eligibility: everyone except the lowest usable (t2 at 7)
    assert total == pytest.approx(sum(val.values()) - 7)
    assert chosen["q2"] == "SUPER_FLEX" and "t2" not in chosen
    empty, _ = optimal(["QB", "K"], ["r1"], pos, val)
    assert empty == 0.0
    neg, chosen = optimal(["QB"], ["q1"], pos, {"q1": -3.0})
    assert neg == 0.0 and chosen == {}


def test_private_test_bodies_never_contain_approved(env):
    client, league, _ = env
    for method, path, body in _routes(league):
        r = _call(client, method, path, body)
        assert "approved" not in {s.lower() for s in strings(r.json())}


def test_numpy_free_json(env):
    """Responses are plain JSON numbers (no numpy scalars leaking into serialization)."""
    client, league, _ = env
    b = client.get(f"/leagues/{league['id']}/projections").json()
    assert isinstance(b["players"][0]["median"], float | int)
    assert not any(isinstance(v, np.generic) for v in b["players"][0].values())
