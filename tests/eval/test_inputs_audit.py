"""Tests for the release-candidate inputs audit and the secondary-dataset leakage checks (EV-3.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from ffml.config import PROJECT_ROOT, get_settings, reset_settings_cache
from ffml.data.store import SnapshotStore
from ffml.eval.audits import inputs_audit as ia
from ffml.eval.audits import leakage_audit as la

HOLDOUT = get_settings().holdout_season


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def item(report, name):
    matches = [i for i in report.items if i.name == name]
    assert matches, f"no item {name!r} in {[i.name for i in report.items]}"
    return matches[0]


def test_restricted_reads_scan(tmp_path):
    write(tmp_path, "ffml/eval/ecr_baseline.py", 'DATASET = "fact_ecr_weekly"\n')  # allowed
    write(tmp_path, "ffml/data/process.py", 'X = "restricted/fact_ecr_weekly"\n')  # allowed
    write(tmp_path, "ffml/models/ok.py", '"""Never reads fact_ecr_weekly (docstring only)."""\nA = 1\n')
    report = ia.audit_inputs(tmp_path, artifacts_dir=tmp_path / "a", backtests_dir=tmp_path / "b",
                             features_root=tmp_path / "f")
    assert item(report, "restricted data reads").status == "PASS"
    write(tmp_path, "ffml/models/bad.py", "from ffml.eval.ecr_baseline import consensus_predictions\n")
    write(tmp_path, "ffml/sim/bad.py", 'P = store / "restricted"\n')
    write(tmp_path, "web/app/page.tsx", "const x = 'fact_ecr_weekly'\n")
    report = ia.audit_inputs(tmp_path, artifacts_dir=tmp_path / "a", backtests_dir=tmp_path / "b",
                             features_root=tmp_path / "f")
    found = item(report, "restricted data reads")
    assert found.status == "FAIL"
    joined = "\n".join(found.evidence)
    assert "ffml/models/bad.py:1" in joined and "ffml/sim/bad.py:1" in joined and "web/app/page.tsx:1" in joined
    assert "ffml/models/ok.py" not in joined


def _artifact(root: Path, name: str, inputs: dict, version: str = "0.2.0") -> None:
    write(root, f"models/chain/{name}/features.json",
          json.dumps({"feature_version": version, "lgbm_inputs": inputs}))


def _run(root: Path, name: str, seasons, purpose="backtest", features=None, version="0.2.0") -> None:
    run = root / name
    run.mkdir(parents=True)
    pl.DataFrame({"season": list(seasons)}, schema={"season": pl.Int32}).write_parquet(run / "predictions.parquet")
    manifest = {"purpose": purpose, "feature_version": version,
                "input_snapshots": {"features": features or {}}}
    (run / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_candidate_features_and_backtest_runs(tmp_path):
    arts, runs, feats = tmp_path / "artifacts", tmp_path / "backtests", tmp_path / "features"
    _artifact(arts, "chain_v1-primary-c2024w19", {"share_target": ["eff__targets__ew", "depth__rank__l1"]})
    _artifact(arts, "chain_v1-final_report-c2024w19", {"share_target": ["avail__out_target_share_ew"]})
    sid = SnapshotStore(feats).write_snapshot("player_week", {2022: pl.DataFrame({"a": [1]})})
    _run(runs, "good", [2021, 2022], features={"player_week": sid})
    kw = dict(artifacts_dir=arts, backtests_dir=runs, features_root=feats, holdout_season=HOLDOUT)
    report = ia.audit_inputs(tmp_path, **kw)
    assert item(report, "candidate feature inputs").status == "PASS"
    assert item(report, "backtest inputs").status == "PASS"
    assert any("1 avail__ inputs" in e for e in item(report, "candidate feature inputs").evidence)
    # injury inputs in a primary artifact, a target input, a holdout row, a non-backtest purpose
    _artifact(arts, "chain_v2-primary-c2024w19", {"team_plays": ["avail__out_rush_share_ew", "y_plays"]})
    _run(runs, "bad", [2024, HOLDOUT], purpose="holdout_eval")
    report = ia.audit_inputs(tmp_path, **kw)
    cand = item(report, "candidate feature inputs")
    assert cand.status == "FAIL"
    assert any("target columns" in e for e in cand.evidence)
    assert any("same-week injury" in e for e in cand.evidence)
    runs_item = item(report, "backtest inputs")
    assert runs_item.status == "FAIL"
    assert any(f"holdout season {HOLDOUT}" in e for e in runs_item.evidence)
    assert any("purpose 'holdout_eval'" in e for e in runs_item.evidence)


def test_backtest_snapshot_mismatch_warns(tmp_path, tmp_settings):
    reset_settings_cache()
    feats = get_settings().features_dir
    sid = SnapshotStore(feats).write_snapshot("player_week", {2022: pl.DataFrame({"a": [1]})})
    runs = tmp_path / "backtests"
    _run(runs, "same", [2022], features={"player_week": sid})
    _run(runs, "stale", [2022], features={"player_week": "20200101T000000Z"})
    report = ia.audit_inputs(tmp_path, artifacts_dir=tmp_path / "a", backtests_dir=runs, features_root=feats)
    found = item(report, "backtest inputs")
    assert found.status == "WARN"
    assert any("stale: feature snapshots differ" in e for e in found.evidence)
    assert any("same:" in e and "match LATEST" in e for e in found.evidence)
    reset_settings_cache()


def test_real_repository_has_no_restricted_reads_outside_data_and_eval():
    report = ia.audit_inputs(PROJECT_ROOT)
    assert item(report, "restricted data reads").status == "PASS"


def _team_frame(bad: bool = False) -> pl.DataFrame:
    kick = datetime(2022, 9, 11, 17, 0, tzinfo=UTC)
    rows = []
    for week in (1, 2):
        for t in range(300):
            ko = kick + timedelta(days=7 * (week - 1), hours=t % 3)
            cutoff = kick + timedelta(days=7 * (week - 1))
            rows.append({"team": f"T{t}", "season": 2022, "week": week, "kickoff_utc": ko,
                         "cutoff_ts": cutoff + (timedelta(hours=5) if bad and t == 0 else timedelta(0)),
                         "asof_ts": cutoff - timedelta(hours=1), "team__plays__l5": float(t % 17),
                         "y_plays": float((t * 7) % 23)})
    return pl.DataFrame(rows)


def test_leakage_audit_covers_team_and_dst_datasets(tmp_settings, tmp_path):
    reset_settings_cache()
    store = SnapshotStore(get_settings().features_dir)
    team = _team_frame()
    store.write_snapshot("player_week", {2022: team.rename({"team": "player_id"}).with_columns(
        pl.lit("WR").alias("position"))})
    store.write_snapshot("team_week", {2022: _team_frame(bad=True)})
    report = la.audit_leakage(root=tmp_path, seasons=(2022,), run_suite=False)
    assert item(report, "timestamp audit").status == "PASS"
    assert item(report, "timestamp audit (team_week)").status == "FAIL"
    assert item(report, "shuffle probe (team_week)").status == "PASS"
    assert item(report, "timestamp audit (dst_week)").status == "SKIP"
    assert report.status == "FAIL"
    reset_settings_cache()


def _registry(root: Path, status: str = "candidate", sha: str | None = None, cal: bool = True) -> Path:
    import hashlib

    art = root / "artifacts"
    version = "chain_v1-primary-c2024w19-x"
    write(root, f"artifacts/models/chain/{version}/params.json", "{}")
    cal_rel = f"artifacts/models/chain/{version}/calibration.json"
    if cal:
        write(root, cal_rel, '{"offsets": {}}')
    digest = sha or hashlib.sha256(b'{"offsets": {}}').hexdigest()
    entry = {"name": "chain_v1", "version": version, "path": f"artifacts/models/chain/{version}",
             "status": status, "calibration": {"path": cal_rel, "sha256": digest,
                                               "calibration_version": "v2-per-position-isotonic"}}
    write(root, "artifacts/models/REGISTRY.json", json.dumps({"models": [entry]}))
    return art


def test_registry_check(tmp_path):
    kw = dict(backtests_dir=tmp_path / "b", features_root=tmp_path / "f")
    art = _registry(tmp_path)
    good = item(ia.audit_inputs(tmp_path, artifacts_dir=art, **kw), "model registry")
    assert good.status == "PASS" and any("sha256 " in e and "v2-per-position-isotonic" in e for e in good.evidence)
    approved = item(ia.audit_inputs(tmp_path, artifacts_dir=_registry(tmp_path, status="approved"), **kw),
                    "model registry")
    assert approved.status == "FAIL" and any("before the holdout release" in e for e in approved.evidence)
    wrong = item(ia.audit_inputs(tmp_path, artifacts_dir=_registry(tmp_path, sha="0" * 64), **kw), "model registry")
    assert wrong.status == "FAIL" and any("differs from recorded" in e for e in wrong.evidence)
    (tmp_path / "artifacts/models/chain/chain_v1-primary-c2024w19-x/calibration.json").unlink()
    missing = item(ia.audit_inputs(tmp_path, artifacts_dir=_registry(tmp_path, cal=False), **kw), "model registry")
    assert missing.status == "FAIL" and any("missing" in e for e in missing.evidence)
    assert item(ia.audit_inputs(tmp_path / "empty", artifacts_dir=tmp_path / "none", **kw),
                "model registry").status == "SKIP"


def test_registry_check_keeps_earlier_calibrators(tmp_path):
    import hashlib

    kw = dict(backtests_dir=tmp_path / "b", features_root=tmp_path / "f")
    art = _registry(tmp_path)
    reg_path = art / "models" / "REGISTRY.json"
    payload = json.loads(reg_path.read_text(encoding="utf-8"))
    old_rel = "artifacts/models/chain/chain_v1-primary-c2024w19-x/calibration_v2.json"
    write(tmp_path, old_rel, '{"v": 2}')
    payload["models"][0]["calibration_history"] = [{
        "path": "artifacts/models/chain/chain_v1-primary-c2024w19-x/calibration.json",
        "sha256": hashlib.sha256(b'{"v": 2}').hexdigest(), "calibration_version": "v2-per-position-isotonic",
        "archived_as": old_rel}]
    reg_path.write_text(json.dumps(payload), encoding="utf-8")
    found = item(ia.audit_inputs(tmp_path, artifacts_dir=art, **kw), "model registry")
    assert found.status == "PASS" and any("earlier calibrator v2-per-position-isotonic kept" in e for e in found.evidence)
    (tmp_path / old_rel).unlink()
    gone = item(ia.audit_inputs(tmp_path, artifacts_dir=art, **kw), "model registry")
    assert gone.status == "FAIL" and any("not kept" in e for e in gone.evidence)


def test_backtest_run_without_recorded_snapshots_warns(tmp_path):
    runs = tmp_path / "backtests"
    _run(runs, "bare", [2022])
    report = ia.audit_inputs(tmp_path, artifacts_dir=tmp_path / "a", backtests_dir=runs,
                             features_root=tmp_path / "f")
    found = item(report, "backtest inputs")
    assert found.status == "WARN"
    assert any("records no feature snapshots" in e for e in found.evidence)
