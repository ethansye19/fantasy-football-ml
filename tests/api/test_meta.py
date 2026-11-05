"""BE-4.8: attribution, model card, run info, season mode, health; restricted-data guards."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from api_factory import (
    APPROVED_LABELS,
    PRIVATE_LABELS,
    live_entry,
    write_registry,
    write_snapshot,
)
from api_helpers import assert_never_approved, strings

from api.services.season import SeasonService
from ffml.season import resolve_state

META = ["/meta/health", "/meta/approval", "/meta/attribution", "/meta/model-card",
        "/meta/run-info", "/meta/season"]
SCHEDULES = Path(__file__).resolve().parents[1] / "fixtures" / "schedules_2026.parquet"
API_DIR = Path(__file__).resolve().parents[2] / "api"


@pytest.mark.parametrize("path", META)
def test_meta_routes_answer_in_production_without_a_model(make_client, path):
    r = make_client().get(path)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path", META)
def test_meta_routes_never_say_approved_in_private_test(make_client, api_dirs, path):
    # even with live and approved registry entries
    write_registry(
        api_dirs["artifacts"], [live_entry(), live_entry("chain_v2-appr", status="approved")]
    )
    write_snapshot(api_dirs["private"], labels=PRIVATE_LABELS)
    r = make_client(app_env="private_test").get(path)
    assert r.status_code == 200
    assert_never_approved(r)


def test_attribution_lists_sources_and_ftn(make_client):
    body = make_client().get("/meta/attribution").json()
    ids = [s["id"] for s in body["sources"]]
    assert ids[:1] == ["nflverse"] and {"sleeper", "ftn", "ffopportunity"} <= set(ids)
    nflverse = body["sources"][0]
    assert "CC-BY-4.0" in nflverse["text"] and "Modified" in nflverse["text"]
    ftn = next(s for s in body["sources"] if s["id"] == "ftn")
    assert ftn["text"] == "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0."
    assert ftn["in_use"] is False
    on = make_client(ftn_features_enabled=True).get("/meta/attribution").json()
    assert next(s for s in on["sources"] if s["id"] == "ftn")["in_use"] is True
    text = " ".join(strings(body)).lower()
    for word in ("fantasypros", "ecr", "dynastyprocess ecr"):
        assert word not in text, word


def test_model_card(make_client, api_dirs):
    write_registry(api_dirs["artifacts"], [live_entry("chain_v1-x", status="rejected")])
    card = make_client().get("/meta/model-card").json()
    assert card["approval_status"] == "not approved" and card["serving"] is False
    assert card["models"][0]["registry_status"] == "rejected"
    assert "0.739" in card["status_summary"]
    assert any("low confidence" in s for s in card["limitations"])
    text = " ".join(strings(card)).lower()
    assert "fantasypros" not in text and "consensus" not in text


def test_run_info(make_client, api_dirs):
    prod = make_client().get("/meta/run-info").json()
    assert prod["serving"] is False and prod["run"] is None
    write_snapshot(api_dirs["fixtures"], labels=PRIVATE_LABELS)
    r = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"]).get(
        "/meta/run-info"
    )
    info = r.json()
    assert info["run"]["source"] == "fixture"
    assert info["run"]["rest_of_season_weeks"] is None or isinstance(
        info["run"]["rest_of_season_weeks"], list
    )
    assert_never_approved(r)


def test_season_unknown_without_schedule(make_client):
    body = make_client().get("/meta/season").json()
    assert body["mode"] == "unknown" and body["banner"]
    assert body["offseason"] is None  # unknown is not offseason (BE-4.9 ruling 5)


def test_model_card_registry_status_words(make_client, api_dirs):
    """Never the bare word "approved": approved/live entries are awaiting_release unless serving."""
    entries = [
        live_entry("v-cand", status="candidate"),
        live_entry("v-appr", status="approved"),
        live_entry("v-live", status="live"),
        live_entry("v-rej", status="rejected"),
        live_entry("v-ret", status="retired"),
    ]
    write_registry(api_dirs["artifacts"], entries)
    card = make_client().get("/meta/model-card").json()  # production, nothing published
    words = {m["version"]: m["registry_status"] for m in card["models"]}
    assert words == {
        "v-cand": "candidate",
        "v-appr": "awaiting_release",
        "v-live": "awaiting_release",
        "v-rej": "rejected",
        "v-ret": "retired",
    }
    assert not any(m["serving"] for m in card["models"])
    assert "approved" not in {s.lower() for s in strings(card)}


def test_model_card_live_word_only_when_serving(make_client, api_dirs):
    write_registry(api_dirs["artifacts"], [live_entry("v-live")])
    write_snapshot(api_dirs["predictions"], model_version="v-live", labels=APPROVED_LABELS)
    card = make_client().get("/meta/model-card").json()
    assert card["serving"] is True
    assert card["models"][0]["registry_status"] == "live" and card["models"][0]["serving"]


@pytest.mark.parametrize(
    "now,mode,offseason",
    [
        (datetime(2026, 11, 4, 20, tzinfo=UTC), "regular", False),
        (datetime(2027, 3, 1, tzinfo=UTC), "offseason", True),
    ],
)
def test_season_modes(make_client, now, mode, offseason):
    schedules = pl.read_parquet(SCHEDULES)
    if offseason:  # every game of the season final
        schedules = schedules.with_columns(pl.col("result").fill_null(3))
    client = make_client()
    client.app.state.season = SeasonService(
        Path("."), resolver=lambda _at: resolve_state(schedules, now)
    )
    body = client.get("/meta/season").json()
    assert (body["mode"], body["offseason"]) == (mode, offseason)
    if offseason:
        assert "Offseason" in body["banner"]
    else:
        assert body["projection_week"] is not None and body["banner"] is None


def test_api_code_never_touches_restricted_data_or_holdout_bypass():
    """D-007/D-051: no ECR reader in the API; no holdout unlock or guard bypass."""
    forbidden = (
        "fact_ecr_weekly",
        "ecr_baseline",
        "load_ff_rankings",
        "fantasypros_id",
        "holdout_unlock",
        "HOLDOUT_UNLOCK",
        "release_holdout",
        "assert_seasons_allowed",
        "processed/restricted",
        "raw/restricted",
    )
    hits = []
    for path in API_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {word}" for word in forbidden if word in text]
    assert hits == []


SERVING_PATH_SCRIPT = """
import json, sys
from fastapi.testclient import TestClient
from api.main import create_app
from api.settings import ApiSettings

fixtures, db_url, meta = json.loads(sys.argv[1])
hits = []
for env in ("production", "private_test"):
    settings = ApiSettings(
        app_env=env,
        api_fixture_predictions_dir=fixtures,
        database_url=db_url,
        approval_ttl_seconds=0,
    )
    with TestClient(create_app(settings)) as client:
        for path in meta:
            hits.append((env, path, client.get(path).status_code))
        hits.append((env, "/projections", client.get("/projections").status_code))
        hits.append((env, "/ros", client.get("/ros").status_code))
        league = "/leagues/00000000-0000-0000-0000-000000000000/projections"
        hits.append((env, league, client.get(league).status_code))
loaded = sorted(m for m in sys.modules if m.startswith("ffml.eval"))
print(json.dumps({"hits": hits, "ffml_eval_modules": loaded}))
"""


def test_serving_paths_do_not_import_the_ecr_reader(api_dirs):
    """D-007/D-051: serving never imports the restricted ECR reader.

    Runs in a fresh interpreter so that other test modules (the evaluator's
    tests import ``ffml.eval.ecr_baseline``) cannot make the check pass or
    fail by import order. The gate is exercised in both environments:
    production (blocked) and private_test (serving a fixture run).
    """
    write_snapshot(api_dirs["fixtures"])
    args = json.dumps(
        [
            str(api_dirs["fixtures"]),
            f"sqlite:///{(api_dirs['data'] / 'subprocess.db').as_posix()}",
            META,
        ]
    )
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", SERVING_PATH_SCRIPT, args],
        cwd=API_DIR.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "ffml.eval.ecr_baseline" not in report["ffml_eval_modules"], report
    statuses = {(env_, path): code for env_, path, code in report["hits"]}
    assert statuses[("production", "/projections")] == 503
    assert statuses[("private_test", "/projections")] == 200  # the fixture run was served
    assert statuses[("private_test", "/meta/health")] == 200
