"""QA-4.1 performance: projection routes warm under 300 ms on a real replay in private_test.

Uses the newest local replay snapshot (``data/predictions_private/season=*/week=*/run=*-replay``
or the test-only folder ``data/predictions_private/replays_test_only/``, D-124; 567 entities x
2,000 simulations for 2024 weeks 10 and 11) copied into a temporary test-only replay folder, the real player directory from ``data/processed``, and a 10-team league built
from the replay's players. Skipped where no replay exists (CI); docs/SECURITY.md
section 6 records a run. The timings are in-process (TestClient); the HTTP numbers
through uvicorn are in the same section.
"""

from __future__ import annotations

import json
import shutil
import statistics
import time
from pathlib import Path

import polars as pl
import pytest

from api.services.players import PlayerDirectory

REPO = Path(__file__).resolve().parents[2]
TARGET_SECONDS = 0.300  # projections, warm (PLAN.md QA-4.1)
REGRESSION_SECONDS = 1.0  # every other projection-based route, warm
WARM_RUNS = 30


def _newest_replay() -> Path | None:
    found = []
    private = REPO / "data" / "predictions_private"
    for store in (private, private / "replays_test_only"):
        for manifest in store.glob("season=*/week=*/run=*/_manifest.json"):
            try:
                m = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            replay = m.get("mode") == "replay" or str(m.get("run_id", "")).endswith("-replay")
            if replay and (manifest.parent / "player_sims.parquet").is_file():
                found.append((str(m.get("written_at") or ""), manifest.parent))
    return max(found)[1] if found else None


REPLAY = _newest_replay()
pytestmark = pytest.mark.skipif(REPLAY is None, reason="no local replay snapshot (data/ is not in git)")


def _league_body(run: Path) -> dict:
    summary = pl.read_parquet(run / "player_summary.parquet")
    if "scoring" in summary.columns:
        summary = summary.filter(pl.col("scoring") == "ppr")
    ranked = summary.sort("pred_mean", descending=True)
    by_pos: dict[str, list[str]] = {}
    for row in ranked.select("entity_id", "position").iter_rows():
        by_pos.setdefault(row[1], []).append(row[0])
    need = {"QB": 2, "RB": 5, "WR": 6, "TE": 2, "K": 1, "DST": 1}
    teams = []
    for i in range(10):
        players = []
        for pos, n in need.items():
            pool = by_pos.get(pos, [])
            players += pool[i * n:(i + 1) * n]
        teams.append({"name": f"Team {i + 1}", "is_me": i == 0, "players": players})
    return {
        "name": "Perf league",
        "season": 2024,
        "num_teams": 10,
        "scoring": {"preset": "half", "overrides": {"pass_td": 6}},
        "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "DEF",
                             "BN", "BN", "BN", "BN", "BN", "BN", "BN"],
        "teams": teams,
    }


def _timed(fn, runs: int) -> list[float]:
    out = []
    for _ in range(runs):
        t = time.perf_counter()
        r = fn()
        out.append(time.perf_counter() - t)
        assert r.status_code == 200, r.text[:300]
    return out


def test_projection_routes_warm_under_300ms_on_a_real_replay(make_client, api_dirs):
    assert REPLAY is not None
    target = api_dirs["test_replays"] / REPLAY.parent.parent.name / REPLAY.parent.name / REPLAY.name
    shutil.copytree(REPLAY, target)
    client = make_client(app_env="private_test")
    client.app.state.players.set(PlayerDirectory.from_store(REPO / "data" / "processed"))
    approval = client.get("/meta/approval").json()
    assert approval["run"]["source"] == "replay" and approval["approval_status"] == "not approved"

    league = client.post("/leagues", json=_league_body(REPLAY))
    assert league.status_code == 201, league.text
    lg = league.json()
    lid = lg["id"]
    mine = next(t for t in lg["teams"] if t["is_me"])
    other = next(t for t in lg["teams"] if not t["is_me"])
    mine_ids = [p["player_id"] for p in mine["players"]]
    other_ids = [p["player_id"] for p in other["players"]]

    t = time.perf_counter()
    assert client.get(f"/leagues/{lid}/projections").status_code == 200
    cold = time.perf_counter() - t

    routes = {
        "GET /projections?scoring=ppr&limit=500": lambda: client.get(
            "/projections", params={"scoring": "ppr", "limit": 500}),
        "GET /leagues/{id}/projections (limit 100)": lambda: client.get(f"/leagues/{lid}/projections"),
        "GET /leagues/{id}/projections (limit 500)": lambda: client.get(
            f"/leagues/{lid}/projections", params={"limit": 500}),
        "POST /leagues/{id}/start-sit (3 players)": lambda: client.post(
            f"/leagues/{lid}/start-sit", json={"player_ids": mine_ids[2:5]}),
        "GET /leagues/{id}/waivers": lambda: client.get(f"/leagues/{lid}/waivers"),
        "POST /leagues/{id}/trades/evaluate (2 for 1)": lambda: client.post(
            f"/leagues/{lid}/trades/evaluate",
            json={"give": mine_ids[2:4], "receive": other_ids[2:3]}),
        "GET /leagues/{id}/ros (limit 500)": lambda: client.get(
            f"/leagues/{lid}/ros", params={"limit": 500}),
        "GET /ros?scoring=ppr&limit=500": lambda: client.get(
            "/ros", params={"scoring": "ppr", "limit": 500}),
    }
    results = {}
    for name, fn in routes.items():
        _timed(fn, 3)  # warm the per-(run, scoring) cache and the code paths
        times = sorted(_timed(fn, WARM_RUNS))
        results[name] = {
            "p50_ms": round(statistics.median(times) * 1000, 1),
            "p95_ms": round(times[int(0.95 * (len(times) - 1))] * 1000, 1),
            "max_ms": round(times[-1] * 1000, 1),
        }
    print(f"\nreplay {REPLAY.name}; cold league projections {cold * 1000:.0f} ms")
    for name, r in results.items():
        print(f"  {name:48s} p50 {r['p50_ms']:7.1f}  p95 {r['p95_ms']:7.1f}  max {r['max_ms']:7.1f} ms")
    # PLAN.md QA-4.1 target: projections under 300 ms warm. The other routes get a
    # regression bound only; waivers is over 300 ms on this league (QA-F10).
    slow = {k: v for k, v in results.items()
            if v["p95_ms"] > (TARGET_SECONDS if "projections" in k else REGRESSION_SECONDS) * 1000}
    assert slow == {}, slow
