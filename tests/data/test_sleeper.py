"""Tests for ffml.data.sleeper against recorded fixtures (tests/fixtures/sleeper).

All tests are offline (respx) except the one marked ``network``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
import respx
from freezegun import freeze_time

from ffml.config import get_settings
from ffml.data import sleeper as s

BASE = "https://sleeper.test/v1"
LEAGUE_ID = "1389750342958223360"
USER_ID = "1257514331365462016"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sleeper"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=load(name))


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/state/nfl", name="state").mock(return_value=ok("state.json"))
        mock.get("/user/ethanye19", name="user_by_name").mock(
            return_value=ok("user_ethanye19.json")
        )
        mock.get(f"/user/{USER_ID}", name="user_by_id").mock(return_value=ok("user_ethanye19.json"))
        mock.get("/user/nobody-here", name="user_unknown").mock(
            return_value=httpx.Response(200, json=None)
        )
        mock.get(f"/user/{USER_ID}/leagues/nfl/2026", name="leagues_2026").mock(
            return_value=ok("user_leagues_2026.json")
        )
        mock.get(f"/user/{USER_ID}/leagues/nfl/2025", name="leagues_2025").mock(
            return_value=ok("user_leagues_2025.json")
        )
        mock.get(f"/user/{USER_ID}/leagues/nfl/2019", name="leagues_none").mock(
            return_value=httpx.Response(200, json=[])
        )
        mock.get(f"/league/{LEAGUE_ID}", name="league").mock(return_value=ok("league.json"))
        mock.get("/league/1", name="league_unknown").mock(
            return_value=httpx.Response(404, json=None)
        )
        mock.get(f"/league/{LEAGUE_ID}/rosters", name="rosters").mock(
            return_value=ok("rosters.json")
        )
        mock.get(f"/league/{LEAGUE_ID}/users", name="users").mock(return_value=ok("users.json"))
        mock.get(f"/league/{LEAGUE_ID}/matchups/8", name="matchups").mock(
            return_value=ok("matchups_week8.json")
        )
        mock.get(f"/league/{LEAGUE_ID}/transactions/1", name="transactions").mock(
            return_value=ok("transactions_round1.json")
        )
        mock.get("/players/nfl/trending/add", name="trending").mock(
            return_value=ok("trending_add.json")
        )
        mock.get("/players/nfl", name="players").mock(return_value=ok("players_subset.json"))
        yield mock


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(api, sleeps):
    c = s.SleeperClient(base_url=BASE, timeout=5, sleep=sleeps.append)
    yield c
    c.close()


# ----------------------------------------------------------------------------- transport
def test_sends_configured_user_agent(api, client):
    client.get_state()
    request = api.routes["state"].calls.last.request
    assert request.headers["user-agent"] == get_settings().http_user_agent
    assert request.headers["accept"] == "application/json"


def test_context_manager_closes_owned_client(api):
    with s.SleeperClient(base_url=BASE) as c:
        assert c.get_state().week == 8
        inner = c._client
    assert inner.is_closed


def test_shared_client_is_not_closed_and_still_sends_user_agent(api):
    shared = httpx.Client()
    with s.SleeperClient(base_url=BASE, client=shared) as c:
        c.get_state()
    sent = api.routes["state"].calls.last.request.headers["user-agent"]
    assert sent == get_settings().http_user_agent
    assert shared.headers["user-agent"].startswith("python-httpx/")  # caller's client untouched
    assert not shared.is_closed
    shared.close()


def test_404_raises_not_found_without_retry(api, client, sleeps):
    with pytest.raises(s.SleeperNotFound) as info:
        client.get_league("1")
    assert info.value.status_code == 404
    assert api.routes["league_unknown"].call_count == 1
    assert sleeps == []


def test_null_body_for_unknown_user_raises_not_found(api, client):
    with pytest.raises(s.SleeperNotFound):
        client.get_user("nobody-here")


def test_retries_on_500_then_succeeds(api, client, sleeps):
    api.routes["state"].side_effect = [httpx.Response(500), ok("state.json")]
    state = client.get_state()
    assert state.season == 2026
    assert api.routes["state"].call_count == 2
    assert sleeps == [0.5]


def test_retry_honours_retry_after_on_429(api, client, sleeps):
    api.routes["state"].side_effect = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        ok("state.json"),
    ]
    assert client.get_state().week == 8
    assert sleeps == [3.0]


def test_retry_after_is_capped(api, sleeps):
    api.routes["state"].side_effect = [
        httpx.Response(429, headers={"Retry-After": "600"}),
        ok("state.json"),
    ]
    with s.SleeperClient(base_url=BASE, sleep=sleeps.append, max_retry_after=10) as c:
        c.get_state()
    assert sleeps == [10.0]


def test_retries_exhausted_raise_server_error(api, client, sleeps):
    api.routes["state"].side_effect = [httpx.Response(503)] * 5
    with pytest.raises(s.SleeperServerError) as info:
        client.get_state()
    assert isinstance(info.value, s.SleeperError)
    assert info.value.status_code == 503
    assert api.routes["state"].call_count == 3
    assert len(sleeps) == 2


def test_persistent_429_raises_rate_limited(api, client):
    api.routes["state"].side_effect = [httpx.Response(429)] * 5
    with pytest.raises(s.SleeperRateLimited):
        client.get_state()
    assert api.routes["state"].call_count == 3


def test_connection_error_is_retried(api, client, sleeps):
    api.routes["state"].side_effect = [httpx.ConnectError("boom"), ok("state.json")]
    assert client.get_state().season == 2026
    assert api.routes["state"].call_count == 2
    assert len(sleeps) == 1


def test_other_4xx_raises_plain_error_without_retry(api, client, sleeps):
    api.routes["state"].side_effect = [httpx.Response(400)]
    with pytest.raises(s.SleeperError) as info:
        client.get_state()
    assert type(info.value) is s.SleeperError
    assert sleeps == []


# ----------------------------------------------------------------------------- rate limiting
def test_token_bucket_waits_when_empty():
    now = [100.0]
    waits: list[float] = []
    bucket = s.TokenBucket(rate_per_minute=60, capacity=2, clock=lambda: now[0], sleep=waits.append)
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == pytest.approx(1.0)  # one token per second
    assert waits == [pytest.approx(1.0)]
    now[0] += 1.0  # refill pays back the reservation
    assert bucket.acquire() == pytest.approx(1.0)
    now[0] += 3.0  # capacity caps the refill
    assert bucket.tokens == pytest.approx(2.0)
    assert bucket.acquire() == 0.0


def test_token_bucket_rejects_bad_parameters():
    with pytest.raises(ValueError):
        s.TokenBucket(rate_per_minute=0)
    with pytest.raises(ValueError):
        s.TokenBucket(capacity=0)


def test_client_rate_limiter_sleeps_between_bursts(api, sleeps):
    with s.SleeperClient(
        base_url=BASE, rate_limit_per_minute=60, burst=1, sleep=sleeps.append
    ) as c:
        c.get_state()
        c.get_state()
    assert sleeps == [pytest.approx(1.0)]
    assert api.routes["state"].call_count == 2


def test_default_rate_limit_is_well_below_sleeper_limit(api, client):
    assert client.bucket.rate_per_second * 60 == 300
    assert client.bucket.capacity == 30


# ----------------------------------------------------------------------------- endpoints
def test_get_state(client):
    state = client.get_state()
    assert (state.season, state.week, state.season_type) == (2026, 8, "regular")
    assert state.season_start_date == date(2026, 9, 9)
    assert state.display_week == 8
    assert state.previous_season == 2025
    assert state.raw["season"] == "2026"


def test_get_user_by_username_and_id(client):
    by_name = client.get_user("ethanye19")
    by_id = client.get_user(USER_ID)
    assert by_name.user_id == by_id.user_id == USER_ID
    assert by_name.username == "ethanye19"
    assert by_name.display_name == "ethanye19"
    assert by_name.avatar == "d55d1f7075eda01948318de4af616075"


def test_get_user_leagues(client):
    leagues_2026 = client.get_user_leagues(USER_ID, 2026)
    assert [lg.league_id for lg in leagues_2026] == [LEAGUE_ID]
    assert leagues_2026[0].season == 2026
    assert leagues_2026[0].status == "in_season"

    leagues_2025 = client.get_user_leagues(USER_ID, "2025")
    assert {lg.league_id for lg in leagues_2025} == {
        "1265845030514339840",
        "1264038218764910593",
        "1257512615593115648",
    }
    assert all(lg.season == 2025 and lg.status == "complete" for lg in leagues_2025)
    assert sorted(lg.total_rosters for lg in leagues_2025) == [10, 12, 12]
    assert client.get_user_leagues(USER_ID, 2019) == []


def test_get_league(client):
    league = client.get_league(LEAGUE_ID)
    assert league.league_id == LEAGUE_ID
    assert league.name == "Bgr fantasy leuge"
    assert league.season == 2026 and isinstance(league.season, int)
    assert league.status == "in_season"
    assert league.sport == "nfl"
    assert league.total_rosters == 8 and league.num_teams == 8
    assert league.previous_league_id == "1264038218764910593"
    assert league.draft_id == "1389750342958223361"
    assert league.avatar is None
    assert league.playoff_week_start == 15
    assert league.settings["trade_deadline"] == 14
    assert league.roster_positions == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF",
        "BN", "BN", "BN", "BN", "BN",
    ]  # fmt: skip
    assert len(league.scoring_settings) == 43
    assert league.scoring_settings["rec"] == 1.0
    assert league.scoring_settings["pass_yd"] == pytest.approx(0.04)
    assert all(isinstance(v, float) for v in league.scoring_settings.values())
    # unknown keys stay reachable
    assert league.model_extra["shard"] == 714
    assert league.raw["last_author_display_name"] == "sys"


def test_get_rosters(client):
    rosters = client.get_rosters(LEAGUE_ID)
    assert len(rosters) == 8
    assert [r.roster_id for r in rosters] == list(range(1, 9))
    mine = next(r for r in rosters if r.owner_id == USER_ID)
    assert mine.roster_id == 3
    assert len(mine.players) == 15 and len(mine.starters) == 10
    assert mine.starters[-1] == "CIN"
    assert mine.co_owners == [] and mine.reserve == [] and mine.taxi == []
    assert (mine.settings.wins, mine.settings.losses, mine.settings.ties) == (4, 3, 0)
    assert mine.settings.points_for == pytest.approx(917.22)
    assert mine.settings.points_against == pytest.approx(953.80)
    assert mine.settings.potential_points == pytest.approx(1061.34)
    assert mine.metadata["record"] == "LLWLWWW"
    assert set(mine.bench) == {"11560", "12514", "2216", "5859", "7543"}
    assert all(set(r.starters) <= set(r.players) for r in rosters)


def test_get_league_users(client):
    users = client.get_league_users(LEAGUE_ID)
    assert len(users) == 9
    by_name = {u.display_name: u for u in users}
    assert by_name["thetobinat0r"].is_owner is True
    assert by_name["thetobinat0r"].team_name == "Tobin's Touchdowns"
    assert by_name["ethanye19"].user_id == USER_ID
    assert by_name["ethanye19"].team_name is None
    assert by_name["ethanye19"].is_owner is False
    assert by_name["Kevigga"].is_owner is False  # null in the payload
    assert by_name["ajsavy"].metadata["team_name"] == "Its gunna be maye"


def test_get_matchups(client):
    matchups = client.get_matchups(LEAGUE_ID, 8)
    assert len(matchups) == 8
    ids = [m.matchup_id for m in matchups]
    assert sorted(ids) == [1, 1, 2, 2, 3, 3, 4, 4]
    mine = next(m for m in matchups if m.roster_id == 3)
    assert mine.points == pytest.approx(115.7)
    assert mine.matchup_id == 2
    assert len(mine.starters) == len(mine.starters_points) == 10
    assert mine.players_points["7564"] == pytest.approx(31.1)
    assert sum(mine.starters_points) == pytest.approx(mine.points)
    assert mine.custom_points is None


def test_get_transactions(client):
    txs = client.get_transactions(LEAGUE_ID, 1)
    assert len(txs) == 5
    assert {t.type for t in txs} == {"waiver", "free_agent"}
    assert all(t.status == "complete" for t in txs)
    first = txs[0]
    assert first.transaction_id == "1405708773443608576"
    assert first.roster_ids == [4]
    assert first.adds == {"11834": 4} and first.drops == {"13294": 4}
    assert first.leg == 1 and first.week == 1
    assert first.draft_picks == [] and first.waiver_budget == []
    assert first.created_at == datetime(2026, 9, 15, 21, 59, 57, 697000, tzinfo=UTC)
    # null adds / drops become empty mappings
    drop_only = next(t for t in txs if t.transaction_id == "1404195892067598336")
    assert drop_only.adds == {} and drop_only.drops == {"1466": 8}


def test_get_trending(api, client):
    trending = client.get_trending("add", lookback_hours=24, limit=25)
    assert len(trending) == 25
    assert trending[0].player_id == "6130" and trending[0].count == 553617
    assert any(t.player_id == "WAS" for t in trending)  # team defenses trend too
    params = api.routes["trending"].calls.last.request.url.params
    assert params["lookback_hours"] == "24" and params["limit"] == "25"
    assert "Sleeper" in s.TRENDING_ATTRIBUTION
    with pytest.raises(ValueError):
        client.get_trending("hold")


def test_get_players_raw(client):
    raw = client.get_players()
    assert len(raw) == 119
    assert raw["KC"]["position"] == "DEF"
    assert raw["4046"]["full_name"] == "Patrick Mahomes"


# ----------------------------------------------------------------------------- players frame
def test_players_frame_schema_and_values(client):
    frame = s.players_frame(client.get_players(), snapshot_date=date(2026, 11, 2))
    assert frame.height == 119
    assert list(frame.schema.items()) == list(s.PLAYERS_SCHEMA.items())
    assert frame["sleeper_id"].to_list() == sorted(frame["sleeper_id"].to_list())
    assert frame["sleeper_id"].n_unique() == 119
    assert frame["snapshot_date"].unique().to_list() == [date(2026, 11, 2)]

    defs = frame.filter(pl.col("position") == "DEF")
    assert defs.height == 9
    kc = defs.filter(pl.col("sleeper_id") == "KC").row(0, named=True)
    assert kc["full_name"] == "Kansas City Chiefs"
    assert kc["team"] == "KC" and kc["active"] is True
    assert kc["fantasy_positions"] == ["DEF"]
    assert kc["status"] is None and kc["age"] is None

    brown = frame.filter(pl.col("sleeper_id") == "5859").row(0, named=True)
    assert brown["full_name"] == "A.J. Brown"
    assert brown["gsis_id"] == "00-0035676"  # Sleeper pads it with a leading space
    assert brown["injury_status"] == "Out"
    assert brown["espn_id"] == "4047646" and brown["yahoo_id"] == "31883"
    assert brown["rotowire_id"] == "13432"
    assert frame.filter(pl.col("gsis_id").str.starts_with(" ")).height == 0
    assert frame.filter(pl.col("position") == "K").height == 8
    assert frame.filter(pl.col("injury_status").is_not_null()).height >= 2


def test_players_frame_tolerates_sparse_entries():
    raw = {
        "1": {"player_id": "1", "first_name": "A", "last_name": "B", "fantasy_positions": None},
        "2": {"position": "WR", "age": "25", "espn_id": 12, "gsis_id": "  ", "active": False},
        "3": "garbage",
    }
    frame = s.players_frame(raw, snapshot_date=date(2026, 1, 1))
    assert frame.height == 2
    rows = {r["sleeper_id"]: r for r in frame.iter_rows(named=True)}
    assert rows["1"]["full_name"] == "A B"
    assert rows["1"]["fantasy_positions"] is None
    assert rows["1"]["active"] is None
    assert rows["2"]["full_name"] is None
    assert rows["2"]["age"] == 25 and rows["2"]["espn_id"] == "12"
    assert rows["2"]["gsis_id"] is None
    assert rows["2"]["active"] is False
    assert list(frame.schema.items()) == list(s.PLAYERS_SCHEMA.items())


def test_players_frame_empty_map_keeps_schema():
    frame = s.players_frame({}, snapshot_date=date(2026, 1, 1))
    assert frame.height == 0
    assert list(frame.schema.items()) == list(s.PLAYERS_SCHEMA.items())


# ----------------------------------------------------------------------------- players cache
def test_players_cache_fetches_once_per_utc_day(api, client, tmp_path):
    root = tmp_path / "raw" / "sleeper_players"
    cache = s.SleeperPlayersCache(root=root, client=client)
    players = api.routes["players"]

    with freeze_time("2026-11-02 03:00:00"):
        frame = cache.get()
        assert frame.height == 119
        assert players.call_count == 1
        snap = root / "snapshot=20261102"
        assert (snap / "players.parquet").exists()
        manifest = json.loads((snap / "_manifest.json").read_text(encoding="utf-8"))
        assert manifest["rows"] == 119
        assert manifest["source"] == "Sleeper API"
        assert manifest["dataset"] == "sleeper_players"
        assert manifest["fetched_at"].startswith("2026-11-02T03:00:00")
        assert "once per UTC day" in manifest["terms"]
        assert manifest["columns"] == list(s.PLAYERS_SCHEMA)
        assert (root / "LATEST").read_text(encoding="utf-8").strip() == "20261102"

        again = cache.get()
        assert players.call_count == 1
        assert again.equals(frame)
        cache.get(max_age_hours=0)  # same UTC day: still no refetch
        assert players.call_count == 1

    with freeze_time("2026-11-03 02:00:00"):  # 23 h later, next UTC day
        cache.get(max_age_hours=24)
        assert players.call_count == 1  # within max_age, reuse yesterday's snapshot
        fresh = cache.get(max_age_hours=12)
        assert players.call_count == 2
        assert fresh["snapshot_date"].unique().to_list() == [date(2026, 11, 3)]
        assert (root / "snapshot=20261103" / "players.parquet").exists()
        assert (root / "LATEST").read_text(encoding="utf-8").strip() == "20261103"
        assert cache.snapshots() == [date(2026, 11, 2), date(2026, 11, 3)]

        cache.get()
        assert players.call_count == 2
        cache.get(force=True)
        assert players.call_count == 3


def test_players_cache_reads_existing_snapshot_without_network(api, tmp_path):
    root = tmp_path / "sleeper_players"
    with freeze_time("2026-11-02 12:00:00"):
        today = date(2026, 11, 2)
        frame = s.players_frame(load("players_subset.json"), snapshot_date=today)
        target = root / "snapshot=20261102"
        target.mkdir(parents=True)
        frame.write_parquet(target / "players.parquet")
        cache = s.SleeperPlayersCache(root=root)  # no client: a fetch would hit the real URL
        assert cache.latest() == today
        assert cache.get().height == 119
        assert api.routes["players"].call_count == 0


def test_players_cache_default_root_follows_settings(tmp_settings):
    cache = s.SleeperPlayersCache()
    assert cache.root == tmp_settings.raw_dir / "sleeper_players"
    assert cache.snapshots() == []


# ----------------------------------------------------------------------------- normalization
def test_normalize_scoring_settings_drops_zero_and_casts():
    out = s.normalize_scoring_settings(
        {"rec": 1, "pass_yd": "0.04", "fum": 0, "pts_allow_21_27": 0.0, "bonus": None}
    )
    assert out == {"pass_yd": 0.04, "rec": 1.0}
    assert all(type(v) is float for v in out.values())
    assert list(out) == sorted(out)
    assert s.normalize_scoring_settings(None) == {}
    assert s.normalize_scoring_settings({}) == {}


def test_normalize_scoring_settings_errors():
    with pytest.raises(ValueError, match="rec_td"):
        s.normalize_scoring_settings({"rec_td": "six"})
    with pytest.raises(ValueError, match="boolean"):
        s.normalize_scoring_settings({"rec": True})
    with pytest.raises(ValueError, match="NaN"):
        s.normalize_scoring_settings({"rec": float("nan")})
    with pytest.raises(TypeError):
        s.normalize_scoring_settings(["rec", 1])


def test_normalize_scoring_settings_on_fixture(client):
    league = client.get_league(LEAGUE_ID)
    out = s.normalize_scoring_settings(league.scoring_settings)
    assert len(out) == 41
    assert "fum" not in out and "pts_allow_21_27" not in out
    assert out["rec"] == 1.0 and out["pass_td"] == 4.0 and out["fum_lost"] == -2.0


def test_normalize_roster_positions():
    out = s.normalize_roster_positions(["qb", " RB", "rb", "Flex", "super_flex", "bn", "BN", "IR"])
    assert out == ["QB", "RB", "RB", "FLEX", "SUPER_FLEX", "BN", "BN", "IR"]
    assert s.normalize_roster_positions(()) == []
    assert s.normalize_roster_positions(None) == []
    assert set(s.normalize_roster_positions(sorted(s.SLEEPER_ROSTER_SLOTS))) == set(
        s.SLEEPER_ROSTER_SLOTS
    )


def test_normalize_roster_positions_errors():
    with pytest.raises(ValueError, match="OP"):
        s.normalize_roster_positions(["QB", "OP", "RB"])
    with pytest.raises(ValueError):
        s.normalize_roster_positions(["QB", None])
    with pytest.raises(TypeError):
        s.normalize_roster_positions("QB,RB")


def test_normalize_roster_positions_on_fixture(client):
    league = client.get_league(LEAGUE_ID)
    assert s.normalize_roster_positions(league.roster_positions) == league.roster_positions


# ----------------------------------------------------------------------------- live
@pytest.mark.network
def test_live_state_and_league():
    with s.SleeperClient() as c:
        state = c.get_state()
        league = c.get_league(LEAGUE_ID)
    assert state.season == 2026
    assert league.total_rosters == 8
    assert league.season == 2026
    assert len(s.normalize_roster_positions(league.roster_positions)) == 15
