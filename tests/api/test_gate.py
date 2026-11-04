"""The approval gate (D-097, D-099): production 503s, private_test labels, defensive manifests."""

from __future__ import annotations

import json

import pytest
from api_factory import (
    APPROVED_LABELS,
    PRIVATE_LABELS,
    live_entry,
    write_registry,
    write_snapshot,
)
from api_helpers import (
    NO_MODEL_BODY,
    all_routes,
    assert_labeled,
    assert_never_approved,
    call,
)

ROUTES = all_routes()


def _assert_blocked(response) -> None:
    assert response.status_code == 503, response.text
    assert response.json() == NO_MODEL_BODY
    assert response.headers["X-FFML-Approval"] == "none"


def _published(api_dirs, *, version="chain_v2-test", labels=APPROVED_LABELS, **kw):
    return write_snapshot(api_dirs["predictions"], model_version=version, labels=labels, **kw)


# --------------------------------------------------------------------------- production


@pytest.mark.parametrize("method,template", ROUTES)
def test_production_default_blocks_every_projection_route(make_client, method, template):
    client = make_client()  # no registry, no snapshot
    _assert_blocked(call(client, method, template))


@pytest.mark.parametrize("method,template", ROUTES)
def test_production_blocks_even_invalid_requests(make_client, method, template):
    client = make_client()
    path = template.replace("{league_id}", "x")
    if method == "GET":
        r = client.get(path, params={"week": "not-a-number", "limit": -5})
    else:
        r = client.post(path, content=b"{not json", headers={"content-type": "application/json"})
    _assert_blocked(r)


def test_env_typo_is_production(make_client, api_dirs):
    write_snapshot(api_dirs["fixtures"], labels=PRIVATE_LABELS)
    client = make_client(app_env="privatetest", api_fixture_predictions_dir=api_dirs["fixtures"])
    assert client.app.state.settings.app_env == "production"
    _assert_blocked(client.get("/projections"))


def test_production_ignores_private_replay_and_fixture_sources(make_client, api_dirs):
    write_registry(api_dirs["artifacts"], [live_entry()])
    write_snapshot(api_dirs["private"], labels=PRIVATE_LABELS)
    write_snapshot(api_dirs["predictions"], mode="replay", update_latest=False)
    write_snapshot(api_dirs["fixtures"], labels=APPROVED_LABELS)
    client = make_client(api_fixture_predictions_dir=api_dirs["fixtures"])
    for method, template in ROUTES:
        _assert_blocked(call(client, method, template))
    assert client.get("/meta/approval").json()["reason"] == "no_latest"


@pytest.mark.parametrize(
    "case",
    [
        "no_registry_entry",
        "candidate_only",
        "rejected_only",
        "live_without_report",
        "no_latest",
        "missing_approval_status",
        "not_approved_status",
        "version_mismatch",
        "private_environment",
        "replay_run",
        "malformed_manifest",
        "malformed_latest",
        "latest_points_nowhere",
        "malformed_registry",
        "pointer_not_approved",
        "path_traversal_run_id",
    ],
)
def test_production_requires_every_condition(make_client, api_dirs, case):
    pred = api_dirs["predictions"]
    entries = [live_entry()]
    if case == "no_registry_entry":
        entries = []
    elif case == "candidate_only":
        entries = [live_entry(status="candidate")]
    elif case == "rejected_only":
        entries = [live_entry(status="rejected")]
    elif case == "live_without_report":
        entries = [dict(live_entry(), evaluator_report=None)]
    if case == "malformed_registry":
        path = api_dirs["artifacts"] / "models" / "REGISTRY.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
    else:
        write_registry(api_dirs["artifacts"], entries)

    if case != "no_latest":
        labels = dict(APPROVED_LABELS)
        if case == "missing_approval_status":
            labels.pop("approval_status")
            labels.pop("label")
        elif case == "not_approved_status":
            labels["approval_status"] = "not approved"
        elif case == "private_environment":
            labels["environment"] = "private_test"
        version = "chain_v1-other" if case == "version_mismatch" else "chain_v2-test"
        mode = "replay" if case == "replay_run" else "live"
        run = _published(api_dirs, version=version, labels=labels, mode=mode)
        if case == "malformed_manifest":
            (run / "_manifest.json").write_text("[1, 2", encoding="utf-8")
        elif case == "malformed_latest":
            (pred / "LATEST").write_text("garbage", encoding="utf-8")
        elif case == "latest_points_nowhere":
            latest = json.loads((pred / "LATEST").read_text(encoding="utf-8"))
            latest["run_id"] = "does-not-exist"
            (pred / "LATEST").write_text(json.dumps(latest), encoding="utf-8")
        elif case == "pointer_not_approved":
            latest = json.loads((pred / "LATEST").read_text(encoding="utf-8"))
            latest["approval_status"] = "not approved"
            (pred / "LATEST").write_text(json.dumps(latest), encoding="utf-8")
        elif case == "path_traversal_run_id":
            latest = json.loads((pred / "LATEST").read_text(encoding="utf-8"))
            latest["run_id"] = "../../../etc"
            (pred / "LATEST").write_text(json.dumps(latest), encoding="utf-8")

    client = make_client()
    for method, template in ROUTES:
        _assert_blocked(call(client, method, template))
    meta = client.get("/meta/approval").json()
    assert meta["serving"] is False
    assert meta["approval_status"] == "not approved"
    assert meta["run"] is None


def test_production_serves_only_when_all_conditions_hold(make_client, api_dirs):
    write_registry(api_dirs["artifacts"], [live_entry()])
    _published(api_dirs)
    client = make_client()
    meta = client.get("/meta/approval").json()
    assert meta["serving"] is True, meta
    assert meta["approval_status"] == "approved"
    assert meta["live_model_version"] == "chain_v2-test"
    r = client.get("/projections")
    assert r.status_code != 503
    assert r.headers["X-FFML-Approval"] == "approved"
    assert r.json()["approval_status"] == "approved"


def test_production_approval_in_extra_is_read(make_client, api_dirs):
    """Older manifests keep extra keys under ``extra``; the gate reads both places."""
    write_registry(api_dirs["artifacts"], [live_entry()])
    run = _published(api_dirs, labels={})
    manifest = json.loads((run / "_manifest.json").read_text(encoding="utf-8"))
    manifest["extra"]["approval_status"] = "approved"
    (run / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    client = make_client()
    assert client.get("/meta/approval").json()["serving"] is True


# --------------------------------------------------------------------------- private_test


@pytest.mark.parametrize("method,template", ROUTES)
def test_private_test_without_snapshot_is_labeled_503(make_client, method, template):
    client = make_client(app_env="private_test")
    r = call(client, method, template)
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "no_private_test_snapshot"
    assert_labeled(r)


@pytest.mark.parametrize("method,template", ROUTES)
def test_private_test_fixture_source_is_labeled(make_client, api_dirs, method, template):
    write_snapshot(api_dirs["fixtures"])
    client = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    r = call(client, method, template)
    assert r.status_code != 503
    assert_never_approved(r)


def test_private_test_never_says_approved_even_if_manifest_does(make_client, api_dirs):
    write_registry(api_dirs["artifacts"], [live_entry()])
    _published(api_dirs)  # an approved public run exists
    write_snapshot(api_dirs["private"], labels=APPROVED_LABELS)  # a mislabeled private run
    client = make_client(app_env="private_test")
    meta = client.get("/meta/approval")
    assert meta.json()["approval_status"] == "not approved"
    assert meta.json()["run"]["source"] == "private_test"
    assert "live_model_version" not in meta.json()
    assert_never_approved(meta)
    for method, template in ROUTES:
        assert_never_approved(call(client, method, template))
    assert_never_approved(client.get("/meta/health"))


def test_private_test_source_order(make_client, api_dirs):
    write_snapshot(api_dirs["fixtures"], run_id="fixture-run")
    client = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    assert client.get("/meta/approval").json()["run"]["source"] == "fixture"

    write_snapshot(api_dirs["predictions"], run_id="old-replay", mode="replay", update_latest=False)
    assert client.get("/meta/approval").json()["run"]["source"] == "replay"
    assert client.get("/meta/approval").json()["run"]["run_id"] == "old-replay"

    write_snapshot(api_dirs["private"], run_id="private-run", labels=PRIVATE_LABELS)
    run = client.get("/meta/approval").json()["run"]
    assert (run["source"], run["run_id"]) == ("private_test", "private-run")


def test_private_test_pinned_replay(make_client, api_dirs):
    write_snapshot(api_dirs["predictions"], week=9, run_id="a-replay", mode="replay",
                   update_latest=False)
    write_snapshot(api_dirs["predictions"], week=10, run_id="b-replay", mode="replay",
                   update_latest=False)
    client = make_client(app_env="private_test", api_replay_run="2024/9/a-replay")
    run = client.get("/meta/approval").json()["run"]
    assert (run["source"], run["run_id"], run["week"]) == ("replay", "a-replay", 9)


def test_private_test_ignores_published_latest(make_client, api_dirs):
    """private_test reads replays from the public store but never its LATEST live run."""
    write_registry(api_dirs["artifacts"], [live_entry()])
    _published(api_dirs)
    client = make_client(app_env="private_test")
    r = client.get("/meta/approval").json()
    assert r["serving"] is False
    assert r["reason"] == "no_private_test_snapshot"


def test_meta_routes_work_in_production_without_model(make_client):
    client = make_client()
    health = client.get("/meta/health")
    assert health.status_code == 200
    assert health.json()["checks"]["projections"] == "off"
    approval = client.get("/meta/approval")
    assert approval.status_code == 200
    body = approval.json()
    assert body["environment"] == "production"
    assert body["serving"] is False
    assert body["reason"] == "no_live_model"
    assert body["message"] == NO_MODEL_BODY["message"]
