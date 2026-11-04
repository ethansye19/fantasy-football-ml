"""BE-4.8: attribution, model card, run info, season mode, health; restricted-data guards."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from api_factory import PRIVATE_LABELS, live_entry, write_registry, write_snapshot
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
    write_registry(api_dirs["artifacts"], [live_entry()])  # even with a live registry model
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


def test_serving_paths_do_not_import_the_ecr_reader(make_client, api_dirs):
    write_snapshot(api_dirs["fixtures"])
    client = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    for path in META:
        client.get(path)
    client.get("/projections")
    assert "ffml.eval.ecr_baseline" not in sys.modules
