"""QA-4.1 rate limits (docs/SECURITY.md section 5).

The API has no inbound rate limiting and no hook for one (QA-F03, design in
docs/SECURITY.md). These tests pin what exists today:

- outbound Sleeper traffic per request is bounded (a league import makes a
  handful of Sleeper calls, never a players-map download when the directory
  is loaded);
- the process-wide Sleeper budget of D-025 (300 requests per minute, burst
  30, one shared client per process) is NOT enforced by the API, because
  every request builds its own SleeperClient and token bucket (QA-F04,
  strict xfail).
"""

from __future__ import annotations

import pytest
import respx
from sleeper_mock import LEAGUE_ID, USERNAME, directory, mock_sleeper

from ffml.data import sleeper as sleeper_module


@pytest.fixture
def sleeper():
    with respx.mock(assert_all_called=False) as router:
        mock_sleeper(router)
        yield router


@pytest.fixture
def client(make_client, sleeper):
    c = make_client()
    c.app.state.players.set(directory())
    return c  # the app's own Sleeper factory, as in production


def test_one_import_makes_a_bounded_number_of_sleeper_calls(client, sleeper):
    r = client.post("/leagues/import/sleeper", json={"league_id": LEAGUE_ID})
    assert r.status_code == 201, r.text
    urls = [str(c.request.url) for c in sleeper.calls]
    assert 1 <= len(urls) <= 6, urls
    assert not any(u.endswith("/players/nfl") for u in urls)  # no 14.7 MB map per request


def test_one_lookup_makes_at_most_two_sleeper_calls(client, sleeper):
    r = client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026})
    assert r.status_code == 200, r.text
    assert 1 <= len(sleeper.calls) <= 2


QA_F04 = pytest.mark.xfail(
    strict=True,
    reason="QA-F04: api/deps.py:24-29 builds a new SleeperClient (and TokenBucket) per "
    "request, so D-025's process-wide 300/min budget with burst 30 is not enforced. "
    "Remove this marker with the fix.",
)


@QA_F04
def test_sleeper_budget_is_shared_across_requests(client, sleeper, monkeypatch):
    buckets: list[object] = []  # the objects themselves, so no id() is reused after GC
    original = sleeper_module.TokenBucket.acquire

    def acquire(self, *args, **kwargs):
        buckets.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(sleeper_module.TokenBucket, "acquire", acquire)
    for _ in range(3):
        assert client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026}).status_code == 200
    assert len(buckets) >= 3
    distinct = {id(b) for b in buckets}  # all still referenced, so ids are unique
    assert len(distinct) == 1, f"{len(distinct)} token buckets for 3 requests"
