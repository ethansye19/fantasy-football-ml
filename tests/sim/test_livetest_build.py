"""chain_v2 weekly live-test build: deadline, commitment, sandbox, history sources (D-098)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from livetoy import history_fn, week5_ctx
from regtoy import SYNTHETIC_VERSION, add_candidate, tree

from ffml.models import chain_v2
from ffml.models import registry as reg
from ffml.models.chain import ChainModel
from ffml.models.chain.data import ChainData
from ffml.models.chain.synthetic import synthetic_frames
from ffml.sim import livetest
from ffml.sim.livetest import (
    AlreadyCommitted,
    DeadlinePassed,
    RawSource,
    build_week,
    check_deadline,
    commitment_digest,
    verify_commitment,
    write_commitment,
)
from ffml.sim.predict import PredictionRefused, run_predict

NOW = datetime(2023, 10, 5, 12, tzinfo=UTC)
KICKOFF = datetime(2023, 10, 6, 0, 15, tzinfo=UTC)
VERSION_RE = r"^chain_v2-primary-c2023w05-\d{8}T\d{6}Z$"


@pytest.fixture
def toy_chain(monkeypatch):
    """chain_v2 with structural stages (fast); pins checked without the synthetic rows."""
    real = chain_v2.check_pins

    def new_model(data=None):
        return ChainModel(stage_choice="structural", n_sims=200, seed=chain_v2.SEED, data=data)

    monkeypatch.setattr(chain_v2, "new_model", new_model)
    monkeypatch.setattr(chain_v2, "check_pins", lambda data=None: real())
    return ChainData(frames=synthetic_frames())


def _build(data, tmp_path, *, rehearsal=False, clock=None, fn=None):
    return build_week(
        2023,
        5,
        rehearsal=rehearsal,
        clock=clock or (lambda: NOW),
        kickoff=KICKOFF,
        data=data,
        ctx=week5_ctx(),
        history_fn=fn or history_fn(tmp_path),
        n_sims=200,
    )


# --------------------------------------------------------------------------- #
# Deadline
# --------------------------------------------------------------------------- #


def test_deadline_is_at_or_after_the_first_kickoff():
    check_deadline(KICKOFF - timedelta(microseconds=1), KICKOFF, False, "start")
    with pytest.raises(DeadlinePassed, match="at or after the first kickoff"):
        check_deadline(KICKOFF, KICKOFF, False, "start")
    with pytest.raises(DeadlinePassed):
        check_deadline(KICKOFF + timedelta(days=3), KICKOFF, False, "start")
    check_deadline(KICKOFF + timedelta(days=3), KICKOFF, True, "start")  # rehearsal
    naive = datetime(2023, 10, 6, 0, 15)  # naive times are UTC
    with pytest.raises(DeadlinePassed):
        check_deadline(naive, KICKOFF, False, "start")


def test_cli_after_kickoff_exits_3_and_writes_nothing(registry_env, cli, monkeypatch):
    monkeypatch.setattr(livetest, "first_kickoff", lambda s, w, store=None: KICKOFF)

    def no_work(*a, **k):
        raise AssertionError("work started after the deadline")

    monkeypatch.setattr(chain_v2, "check_pins", no_work)
    result = cli(["livetest", "build-week", "--season", "2023", "--week", "5"])
    assert result.exit_code == 3, result.output
    assert "deadline passed" in result.output and "first kickoff" in result.output
    assert tree(registry_env.data_dir) == {} and tree(registry_env.artifacts_dir) == {}


def test_cli_maps_refusals_to_exit_codes(registry_env, cli, monkeypatch):
    seen = {}

    def fake(season, week, rehearsal=False):
        seen.update(season=season, week=week, rehearsal=rehearsal)
        raise seen["error"]

    monkeypatch.setattr(livetest, "build_week", fake)
    for error, code in (
        (AlreadyCommitted("x"), 4),
        (PredictionRefused("x"), 2),
        (DeadlinePassed("x"), 3),
        (livetest.WeekNotScheduled("x"), 2),
    ):
        seen["error"] = error
        result = cli(["livetest", "build-week", "--season", "2026", "--week", "9", "--rehearsal"])
        assert result.exit_code == code, result.output
    assert seen["season"] == 2026 and seen["week"] == 9 and seen["rehearsal"] is True
    seen["error"] = RuntimeError("boom")
    assert cli(["livetest", "build-week", "--season", "2026", "--week", "9"]).exit_code == 1


def test_deadline_is_checked_again_before_projections(registry_env, toy_chain, tmp_path):
    times = iter([NOW, KICKOFF + timedelta(seconds=1)])
    with pytest.raises(DeadlinePassed, match="before writing projections"):
        _build(toy_chain, tmp_path, clock=lambda: next(times, KICKOFF + timedelta(seconds=2)))
    assert not registry_env.private_predictions_dir.exists()
    assert not livetest.commitment_path(2023, 5).exists()


# --------------------------------------------------------------------------- #
# Build and commitment
# --------------------------------------------------------------------------- #


def test_build_commits_every_output_with_hashes(registry_env, toy_chain, tmp_path, cli):
    res = _build(toy_chain, tmp_path)
    assert re.match(VERSION_RE, res.model_version)
    path = registry_env.data_dir / "livetest" / "2023w05" / "commitment.json"
    assert res.commitment == path and path.exists()
    c = json.loads(path.read_text(encoding="utf-8"))
    assert c["digest"]["value"] == res.digest == commitment_digest(c)
    assert verify_commitment(path) == []
    assert cli(["livetest", "verify", str(path)]).exit_code == 0
    # labels, times, code
    assert (c["model"], c["season"], c["week"], c["rehearsal"]) == ("chain_v2", 2023, 5, False)
    assert c["approval_status"] == c["label"] == "not approved"
    assert c["as_of"] == NOW.isoformat() and c["first_kickoff"] == KICKOFF.isoformat()
    assert datetime.fromisoformat(c["created_at"]) < KICKOFF
    assert re.match(r"^[0-9a-f]{40}$", c["code"]["commit"]) and "clean" in c["code"]
    # every output file is listed with its hash: predictions and model artifact
    outputs = {Path(o["path"]).name: o for o in c["outputs"]}
    for name in (
        "player_summary.parquet",
        "player_summary_raw.parquet",
        "player_sims.parquet",
        "_manifest.json",
        "params.json",
        "train_manifest.json",
        "calibration_state.json",
    ):
        assert name in outputs, name
    files = [Path(o["path"]) for o in c["outputs"]]
    assert set(files) == {
        *livetest.tree_files(res.run_dir),
        *livetest.tree_files(livetest.artifact_root() / res.model_version),
    }
    # artifact and calibrator-state hashes
    artifact = registry_env.models_dir / "chain_v2" / res.model_version
    state_file = artifact / "calibration_state.json"
    assert c["calibrator_state"]["sha256"] == livetest.sha256_file(state_file)
    assert c["model_artifact"]["sha256"] == livetest.digest_of(
        [o for o in c["outputs"] if o["role"] == "model_artifact"]
    )
    assert c["calibrator_state"]["history_last"] == [2023, 4]
    assert c["calibration_history"]["sources"][0]["origin"] == "toy"
    # registry candidate with the state as its calibration
    entry = reg.get(res.model_version)
    assert entry["status"] == "candidate" and entry["name"] == "chain_v2"
    assert entry["calibration"]["sha256"] == c["calibrator_state"]["sha256"]
    manifest = json.loads((artifact / "train_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_version"] == res.model_version and manifest["cutoff"] == [2023, 5]
    # private store, labeled, calibrated summary plus raw rows
    assert res.run_dir.is_relative_to(registry_env.private_predictions_dir)
    run = json.loads((res.run_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert run["environment"] == "private_test" and run["label"] == "not approved"
    assert "rehearsal" not in run
    assert run["extra"]["calibration"]["state_sha256"] == c["calibrator_state"]["sha256"]
    summary = pl.read_parquet(res.run_dir / "player_summary.parquet")
    raw = pl.read_parquet(res.run_dir / "player_summary_raw.parquet")
    assert set(summary["model"]) == {"chain_v2"} and set(raw["model"]) == {"chain_v1"}
    sha12 = c["calibrator_state"]["sha256"][:12]
    assert set(summary["calibration_version"]) == {f"walk_forward:{sha12}"}
    assert set(summary["approval_status"]) == set(raw["approval_status"]) == {"not approved"}
    assert summary.height == raw.height
    assert not summary.select("pred_q10", "pred_q90").equals(raw.select("pred_q10", "pred_q90"))
    assert not (registry_env.data_dir / "predictions").exists()  # public store untouched
    # one real commitment per week
    before = tree(registry_env.data_dir)
    with pytest.raises(AlreadyCommitted):
        _build(toy_chain, tmp_path)
    assert tree(registry_env.data_dir) == before


def test_verify_detects_tampering(registry_env, toy_chain, tmp_path):
    res = _build(toy_chain, tmp_path)
    c = json.loads(res.commitment.read_text(encoding="utf-8"))
    summary = res.run_dir / "player_summary.parquet"
    summary.write_bytes(summary.read_bytes() + b"x")
    assert any("changed" in p for p in verify_commitment(res.commitment))
    c["week"] = 6
    res.commitment.write_text(json.dumps(c), encoding="utf-8")
    assert "digest does not match the commitment body" in verify_commitment(res.commitment)


def test_digest_is_canonical_and_ignores_the_digest_field(tmp_path):
    f = tmp_path / "out.bin"
    f.write_bytes(b"abc")
    rec = {"path": str(f), "role": "x", "sha256": livetest.sha256_file(f), "bytes": 3}
    payload = {"b": 1, "a": {"y": [1, 2], "x": "s"}, "outputs": [rec]}
    path = tmp_path / "commitment.json"
    digest = write_commitment(path, payload)
    reordered = {"outputs": [rec], "a": {"x": "s", "y": [1, 2]}, "b": 1, "digest": {"value": "z"}}
    assert digest == commitment_digest(payload) == commitment_digest(reordered)
    assert verify_commitment(path) == []
    assert path.read_bytes().endswith(b"}\n") and b"\r\n" not in path.read_bytes()


def test_rehearsal_after_kickoff_writes_only_to_the_sandbox(registry_env, toy_chain, tmp_path):
    late = KICKOFF + timedelta(hours=5)
    res = _build(toy_chain, tmp_path, rehearsal=True, clock=lambda: late)
    sandbox = registry_env.data_dir / "livetest" / "rehearsal"
    assert res.commitment == sandbox / "2023w05" / "commitment.json"
    c = json.loads(res.commitment.read_text(encoding="utf-8"))
    assert c["rehearsal"] is True and c["seconds_before_kickoff"] < 0
    assert verify_commitment(res.commitment) == []
    # sandbox registry, artifact and store; the real ones are untouched
    assert not reg.registry_path().exists()
    assert not registry_env.private_predictions_dir.exists()
    assert not (registry_env.models_dir / "chain_v2").exists()
    entry = reg.get(res.model_version, path=sandbox / "REGISTRY.json")
    assert entry["status"] == "candidate" and entry["path"].startswith(str(sandbox.as_posix()))
    assert res.run_dir.is_relative_to(sandbox / "predictions_private")
    run = json.loads((res.run_dir / "_manifest.json").read_text(encoding="utf-8"))
    latest = json.loads((sandbox / "predictions_private" / "LATEST").read_text(encoding="utf-8"))
    assert run["rehearsal"] is True and latest["rehearsal"] is True
    assert latest["label"] == "not approved"
    every = {
        p.relative_to(registry_env.data_dir).as_posix()
        for p in registry_env.data_dir.rglob("*")
        if p.is_file()
    }
    assert every and all(p.startswith("livetest/rehearsal/") for p in every)
    # a rehearsal may run again
    res2 = _build(toy_chain, tmp_path, rehearsal=True, clock=lambda: late + timedelta(seconds=2))
    assert res2.commitment == res.commitment and res2.model_version != res.model_version


# --------------------------------------------------------------------------- #
# Raw history sources
# --------------------------------------------------------------------------- #


def test_history_parts_use_stored_weeks_then_a_cached_walk_forward(registry_env, monkeypatch):
    sources = (RawSource("runA", 2021, (1, 2)), RawSource("runB", 2022, (1,)))
    monkeypatch.setattr(livetest, "STORED_SOURCES", sources)
    weeks = [(2021, 1), (2021, 2), (2022, 1), (2022, 2), (2022, 3), (2022, 4), (2022, 5)]

    def scheduled(first, season, week, store=None):
        return [w for w in weeks if w < (season, week)]

    monkeypatch.setattr(livetest, "scheduled_weeks", scheduled)
    for src in sources:
        for w in src.weeks:
            p = livetest.stored_part(src.season, w)
            p.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"season": [src.season], "week": [w], "x": [1.0]}).write_parquet(p)
    calls = []

    def recompute(data, season, week, scorings=chain_v2.SCORINGS):
        calls.append((season, week))
        return pl.DataFrame({"season": [season], "week": [week], "x": [2.0]}), {"rows": 1}

    monkeypatch.setattr(livetest, "recompute_week", recompute)
    recs, written = livetest.raw_history_parts(2022, 4, data=None)
    assert [(r["season"], r["week"]) for r in recs] == [
        (2021, 1),
        (2021, 2),
        (2022, 1),
        (2022, 2),
        (2022, 3),
    ]
    assert [r["origin"] for r in recs] == ["stored:runA"] * 2 + ["stored:runB"] + [
        "walkforward"
    ] * 2
    assert calls == [(2022, 2), (2022, 3)] and len(written) == 4  # parquet + json per week
    cache = registry_env.data_dir / "livetest" / "walkforward" / "parts" / "chain_v1"
    assert sorted(p.name for p in cache.iterdir()) == [
        "2022_w02.json",
        "2022_w02.parquet",
        "2022_w03.json",
        "2022_w03.parquet",
    ]
    meta = json.loads((cache / "2022_w02.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == livetest.sha256_file(cache / "2022_w02.parquet")
    # cached: the next week reuses them, computes only the new week, never the target
    recs2, written2 = livetest.raw_history_parts(2022, 5, data=None)
    assert calls == [(2022, 2), (2022, 3), (2022, 4)] and len(written2) == 2
    assert [r["sha256"] for r in recs2[:5]] == [r["sha256"] for r in recs]
    # rehearsals keep their own cache
    livetest.raw_history_parts(2022, 3, data=None, rehearsal=True)
    assert calls[-1] == (2022, 2)
    assert (registry_env.data_dir / "livetest" / "rehearsal" / "walkforward").exists()
    # a stored week whose file is gone is an error, never recomputed
    livetest.stored_part(2021, 2).unlink()
    with pytest.raises(FileNotFoundError, match="stored raw part missing"):
        livetest.raw_history_parts(2022, 4, data=None)


def test_stored_sources_are_the_validated_runs():
    runs = {(s.run_id, s.season): s.weeks for s in livetest.STORED_SOURCES}
    assert runs[("default_20261102", 2021)] == tuple(range(1, 19))
    assert runs[("default_20261102", 2024)] == tuple(range(1, 19))
    assert runs[("holdout_2025", 2025)] == tuple(range(1, 19))
    assert runs[("forward_2026", 2026)] == tuple(range(1, 8))
    assert livetest.stored_source(2026, 8) is None and livetest.stored_source(2020, 1) is None


# --------------------------------------------------------------------------- #
# Private-test gate options
# --------------------------------------------------------------------------- #


def test_rehearsal_and_calibrator_are_private_test_options(registry_env, synthetic_model_dir):
    model_dir, data = synthetic_model_dir
    with pytest.raises(PredictionRefused, match="rehearsal"):
        run_predict(season=2023, week=5, rehearsal=True, data=data)
    with pytest.raises(PredictionRefused, match="calibrator"):
        run_predict(season=2023, week=5, replay=True, model_dir=model_dir, calibrator=lambda f: f)
    add_candidate(SYNTHETIC_VERSION, model_dir)  # in the real registry only
    with pytest.raises(PredictionRefused, match="not registered"):
        run_predict(
            season=2023,
            week=5,
            private_test=True,
            rehearsal=True,
            model_version=SYNTHETIC_VERSION,
            data=data,
        )
    with pytest.raises(PredictionRefused, match="rehearsal sandbox store"):
        run_predict(
            season=2023,
            week=5,
            private_test=True,
            rehearsal=True,
            model_version=SYNTHETIC_VERSION,
            root=registry_env.private_predictions_dir,
        )
    assert tree(registry_env.data_dir) == {}


def test_code_state_and_peak_memory():
    state = livetest.code_state()
    assert re.match(r"^[0-9a-f]{40}$", state["commit"])
    assert isinstance(state["clean"], bool) and isinstance(state["status"], list)
    peak = livetest.peak_memory_mb()
    assert peak is None or peak > 10
