"""QA-4.1 compliance, runtime half (docs/SECURITY.md section 4): what the API returns and reads.

An adversarial private_test world in which *every* source claims approval:
a live registry entry with an approval report, an approved live run behind
the public ``LATEST``, and private-store, replay and fixture runs whose
manifests say ``approval_status: "approved"`` and ``environment: "live"``.
Every API operation is called with valid inputs (plus error cases), and
every response must:

(b) carry ``X-FFML-Approval: not-approved`` and no string that claims
    approval (only "not approved" and other negations), with every
    ``approval_status`` field equal to "not approved", and never serve the
    published run;
(c) contain no "lock", "guaranteed", "can't miss" or betting language
    (disclaimers such as "No guarantees" are fine).

(d) While the sweep runs (both environments), no file under a ``restricted``
directory (FantasyPros/ECR data, D-007) is opened, listed or read, and the
API's import closure contains no consensus reader.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import respx
from api_factory import APPROVED_LABELS, live_entry, player_ids, write_registry, write_snapshot
from api_helpers import strings
from qa_support import file_audit
from sleeper_mock import LEAGUE_ID, USERNAME, directory, mock_sleeper, no_sleep_factory

from tests.compliance.qa_text_rules import REPO, approval_claims, banned_language

ENTS = player_ids()
BY_TEAM: dict[str, list[tuple[str, str]]] = {}
for _eid, _pos, _team in ENTS:
    BY_TEAM.setdefault(_team, []).append((_eid, _pos))
MINE = [e for e, _p in BY_TEAM["KC"]] + [e for e, p in BY_TEAM["BUF"] if p == "WR"]
THEIRS = [e for e, _p in BY_TEAM["DET"]]
LEAGUE = {
    "name": "Compliance league",
    "season": 2024,
    "num_teams": 2,
    "scoring": {"preset": "half"},
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN"],
    "teams": [{"name": "Mine", "is_me": True, "players": MINE},
              {"name": "Theirs", "players": THEIRS}],
}
RESTRICTED_MODULES = ("ffml.eval.ecr_baseline", "ffml.data.process.ecr")


def _plant_restricted_decoys(data: Path) -> list[Path]:
    """Files where restricted consensus data would live; nothing may touch them."""
    out = []
    for rel in ("processed/restricted/fact_ecr_weekly/season=2024/part.parquet",
                "raw/restricted/ecr/2024.parquet", "raw/restricted/fantasypros/weekly.csv"):
        path = data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"decoy")
        out.append(path)
    return out


@pytest.fixture
def sleeper():
    with respx.mock(assert_all_called=False) as router:
        mock_sleeper(router)
        yield router


def _claiming_world(api_dirs) -> None:
    report = api_dirs["reports"] / "evaluator_release_chain_v2-test.md"
    report.write_text("Verdict: APPROVED\n", encoding="utf-8")
    entry = live_entry()
    entry["evaluator_report"] = str(report)
    write_registry(api_dirs["artifacts"], [entry])
    write_snapshot(api_dirs["predictions"], labels=APPROVED_LABELS)
    write_snapshot(api_dirs["predictions"], mode="replay", labels=APPROVED_LABELS,
                   update_latest=False)
    write_snapshot(api_dirs["private"], labels=APPROVED_LABELS, run_id="2024w10-claims")
    write_snapshot(api_dirs["fixtures"], labels=APPROVED_LABELS, run_id="2024w10-fixture")


def _sweep(client) -> tuple[list[tuple[str, Any]], set[tuple[str, str]]]:
    """Call every operation with valid inputs and some error cases."""
    responses: list[tuple[str, Any]] = []
    covered: set[tuple[str, str]] = set()

    def do(method: str, template: str, path: str | None = None, **kw: Any) -> Any:
        r = client.request(method, path or template, **kw)
        covered.add((method, template))
        responses.append((f"{method} {path or template} -> {r.status_code}", r))
        return r

    lg = do("POST", "/leagues", json=LEAGUE)
    assert lg.status_code == 201, lg.text
    league = lg.json()
    lid = league["id"]
    me = next(t["id"] for t in league["teams"] if t["is_me"])
    them = next(t["id"] for t in league["teams"] if not t["is_me"])
    tl = "/leagues/{league_id}"

    for path in ("/meta/health", "/meta/approval", "/meta/attribution", "/meta/model-card",
                 "/meta/run-info", "/meta/season"):
        do("GET", path)
    do("GET", "/sleeper/users/{username}/leagues", f"/sleeper/users/{USERNAME}/leagues",
       params={"season": 2026})
    do("POST", "/leagues/import/sleeper", json={"league_id": LEAGUE_ID})
    do("GET", "/leagues")
    if ("GET", "/players") in _operations(client):  # player search (added with UI-4.2)
        do("GET", "/players", params={"search": "ma"})
        do("GET", "/players", params={"search": "x"})  # 422
    do("GET", tl, f"/leagues/{lid}")
    do("GET", tl, "/leagues/00000000-0000-0000-0000-000000000000")
    do("PUT", tl + "/settings", f"/leagues/{lid}/settings",
       json={"scoring": {"preset": "ppr", "overrides": {"pass_td": 6}}})
    do("PUT", tl + "/my-team", f"/leagues/{lid}/my-team", json={"league_team_id": me})
    do("POST", tl + "/sync", f"/leagues/{lid}/sync")  # 409: manual league

    for scoring in ("ppr", "half", "std"):
        do("GET", "/projections", params={"scoring": scoring, "limit": 500})
        do("GET", "/ros", params={"scoring": scoring, "limit": 500})
    do("GET", "/projections", params={"week": 3})  # 404 week_not_available
    for params in ({}, {"availability": "mine"}, {"availability": "free_agents"},
                   {"position": "K"}, {"position": "DST"}, {"sort": "boom"}, {"sort": "bust"},
                   {"search": "zzz"}):
        do("GET", tl + "/projections", f"/leagues/{lid}/projections", params=params)
    qb, wr1, wr2 = MINE[0], MINE[3], MINE[-1]
    for ids in ([wr1, wr2], [qb, THEIRS[0]], [wr1, wr2, MINE[1], MINE[2]], [wr1, "00-9999999"]):
        do("POST", tl + "/start-sit", f"/leagues/{lid}/start-sit", json={"player_ids": ids})
    for params in ({}, {"position": "WR"}, {"league_team_id": them}, {"limit": 100}):
        do("GET", tl + "/waivers", f"/leagues/{lid}/waivers", params=params)
    for give, receive in (([MINE[1]], [THEIRS[3]]), ([MINE[0], MINE[4]], [THEIRS[0]]),
                          ([MINE[2]], [MINE[3]])):
        do("POST", tl + "/trades/evaluate", f"/leagues/{lid}/trades/evaluate",
           json={"give": give, "receive": receive})
    for params in ({}, {"availability": "mine"}, {"include_current_week": "false"},
                   {"position": "TE"}):
        do("GET", tl + "/ros", f"/leagues/{lid}/ros", params=params)
    do("DELETE", tl, f"/leagues/{lid}")
    do("GET", tl + "/projections", f"/leagues/{lid}/projections")  # 404 after delete
    do("GET", "/no/such/route")
    return responses, covered


def _operations(client) -> set[tuple[str, str]]:
    paths = client.get("/openapi.json").json()["paths"]
    return {(m.upper(), p) for p, ops in paths.items() for m in ops}


def _approval_fields(value: Any, path: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "approval_status":
                out.append((f"{path}.{k}", v))
            out += _approval_fields(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _approval_fields(v, f"{path}[{i}]")
    return out


def _body(r) -> Any:
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def test_private_test_never_claims_approval_even_when_every_source_does(
    make_client, api_dirs, sleeper
):
    _claiming_world(api_dirs)
    client = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    client.app.state.players.set(directory())
    client.app.state.sleeper_factory = no_sleep_factory
    responses, covered = _sweep(client)
    missing = _operations(client) - covered
    assert missing == set(), f"operations not swept: {sorted(missing)}"
    served = 0
    problems: list[str] = []
    for label, r in responses:
        if r.headers.get("X-FFML-Approval") != "not-approved":
            problems.append(f"{label}: header {r.headers.get('X-FFML-Approval')!r}")
        body = _body(r)
        for s in strings(body):
            if approval_claims(s):
                problems.append(f"{label}: claims approval in {s[:120]!r}")
        for where, value in _approval_fields(body):
            if value != "not approved":
                problems.append(f"{label}: {where} = {value!r}")
        if isinstance(body, dict) and isinstance(body.get("run"), dict):
            served += 1
            if body["run"].get("source") == "published":
                problems.append(f"{label}: private_test served the published run")
    assert problems == []
    assert served >= 25  # the projection routes really served (not just errors)


@pytest.mark.parametrize("app_env", ["production", "private_test"])
def test_responses_have_no_banned_language(make_client, api_dirs, sleeper, app_env):
    _claiming_world(api_dirs)
    client = make_client(app_env=app_env, api_fixture_predictions_dir=api_dirs["fixtures"])
    client.app.state.players.set(directory())
    client.app.state.sleeper_factory = no_sleep_factory
    responses, _ = _sweep(client)
    problems = [
        f"{label}: {hit} in {s[:100]!r}"
        for label, r in responses
        for s in strings(_body(r))
        for hit in banned_language(s)
    ]
    assert problems == []
    disclaimers = [s for _l, r in responses for s in strings(_body(r)) if s.startswith("No guarantees")]
    if app_env == "private_test":
        assert disclaimers  # start/sit, waivers and trades carry the disclaimer


def _restricted(path: str) -> bool:
    parts = [p.lower() for p in Path(path).parts]
    return any(p == "restricted" or p.startswith("fact_ecr") or "fantasypros" in p
               or "fpecr" in p for p in parts)


@pytest.mark.parametrize("app_env", ["production", "private_test"])
def test_no_route_touches_restricted_consensus_data(make_client, api_dirs, sleeper, app_env):
    decoys = _plant_restricted_decoys(api_dirs["data"])
    _claiming_world(api_dirs)
    before = {m for m in RESTRICTED_MODULES if m in sys.modules}
    with file_audit() as touched:
        client = make_client(app_env=app_env, api_fixture_predictions_dir=api_dirs["fixtures"])
        client.app.state.sleeper_factory = no_sleep_factory  # real directory load from data/
        _sweep(client)
    assert touched, "the audit recorded nothing"
    assert sorted({p for p in touched if _restricted(p)}) == []
    assert all(d.read_bytes() == b"decoy" for d in decoys)
    assert {m for m in RESTRICTED_MODULES if m in sys.modules} - before == set()


def test_file_audit_control_sees_restricted_reads(api_dirs):
    """The audit is only evidence if it records a read when one happens."""
    import polars as pl

    decoys = _plant_restricted_decoys(api_dirs["data"])
    with file_audit() as touched:
        decoys[2].read_bytes()
        with pytest.raises(Exception):  # noqa: B017 - not a real parquet file
            pl.read_parquet(decoys[0])
        list(decoys[1].parent.iterdir())
    hits = {p for p in touched if _restricted(p)}
    assert any(p.endswith("weekly.csv") for p in hits)
    assert any(p.endswith("part.parquet") for p in hits)
    assert any(Path(p).name == "ecr" for p in hits)


def test_api_import_closure_has_no_consensus_reader():
    """Fresh interpreter: import every api module and list what came with it."""
    code = (
        "import importlib, json, pkgutil, sys\n"
        "import api\n"
        "for m in pkgutil.walk_packages(api.__path__, 'api.'):\n"
        "    if not m.name.startswith('api.migrations'):\n"
        "        importlib.import_module(m.name)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True,
                         encoding="utf-8", timeout=600, env={**os.environ, "PYTHONUTF8": "1"})
    assert out.returncode == 0, f"import failed:\n{out.stderr[-2000:]}"
    modules = json.loads(out.stdout.strip().splitlines()[-1])
    bad = [m for m in modules
           if m in RESTRICTED_MODULES or m.startswith("ffml.eval") or ".ecr" in m]
    assert "api.main" in modules
    assert bad == []
