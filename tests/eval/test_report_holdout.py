"""Tests for the holdout release report (EV-3.4) on synthetic frames and runs; no real or restricted rows."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from test_report_backtest import (  # sibling test module
    POS_COUNTS,
    _synthetic_consensus,
    model_rows,
    truth,
)

from ffml.eval import metrics as M
from ffml.eval import report_backtest as RB
from ffml.eval import report_holdout as H

FOCUS = "chain_v1"


def _g(root, *args):
    out = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _commit(root, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        _g(root, "add", rel)
    _g(root, "commit", "-q", "-m", message)
    return _g(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repository: R (root) then X (the frozen commit); HEAD at X."""
    _g(tmp_path, "init", "-q")
    _g(tmp_path, "config", "user.email", "test@example.com")
    _g(tmp_path, "config", "user.name", "test")
    r = _commit(tmp_path, {"README.md": "r\n"}, "R")
    x = _commit(tmp_path, {"ffml/code.py": "x = 1\n"}, "X")
    return {"root": tmp_path, "R": r, "X": x}


# --------------------------------------------------------------------------- #
# The 22 lines
# --------------------------------------------------------------------------- #


def test_prereg_lines_are_the_22_registered_lines():
    lines = H.prereg_lines()
    assert len(lines) == 22 and [line.id for line in lines] == [f"L{i:02d}" for i in range(1, 23)]
    kinds = [line.kind for line in lines]
    assert kinds.count("beats_baseline") == 12 and kinds.count("startsit_consensus") == 4
    assert kinds.count("coverage") == 6
    beats = {(line.comparator, line.position) for line in lines if line.kind == "beats_baseline"}
    assert beats == {(c, p) for c in ("rolling_avg_4", "prior_season", "rolling_median_8")
                     for p in ("QB", "RB", "WR", "TE")}
    assert [line.position for line in lines if line.kind == "coverage"] == ["QB", "RB", "WR", "TE", "K", "DST"]
    assert len(H.lines_spec_sha256()) == 64 and H.lines_spec_sha256() == H.lines_spec_sha256()


def _boot(points, deltas, n=200):
    """BootResult where each comparator carries its own delta to a shared focus array (known intervals)."""
    rng = np.random.default_rng(0)
    boot = RB.BootResult(points={}, boots={}, n_boot=n)
    focus_arr: dict[tuple, np.ndarray] = {}
    for (pos, focus, other, metric), (d, spread) in deltas.items():
        key = (pos, focus, metric)
        if key not in focus_arr:
            focus_arr[key] = 5.0 + rng.normal(0, 0.01, n)
            boot.points.setdefault(pos, {}).setdefault(focus, {})[metric] = 5.0
            boot.boots.setdefault(pos, {}).setdefault(focus, {})[metric] = focus_arr[key]
        # focus - other = d, with replicate spread ``spread``
        boot.points.setdefault(pos, {}).setdefault(other, {})[metric] = 5.0 - d
        boot.boots.setdefault(pos, {}).setdefault(other, {})[metric] = focus_arr[key] - d + rng.normal(0, spread, n)
    for (pos, label, metric), v in points.items():
        boot.points.setdefault(pos, {}).setdefault(label, {})[metric] = v
    return boot


def _passing_results(fail_at: dict[str, tuple] | None = None):
    fail_at = fail_at or {}
    deltas = {}
    for comp in H.LINE_BASELINES:
        for pos in ("QB", "RB", "WR", "TE"):
            for metric in H.BEATS_METRICS:
                d = fail_at.get(("beats", comp, pos, metric), (-0.5, 0.05))
                deltas[(pos, FOCUS + RB.CAL, comp + RB.CAL, metric)] = d
    cov = {(pos, FOCUS + RB.CAL, "coverage_tie_aware"): fail_at.get(("cov", pos), 0.80)
           for pos in ("QB", "RB", "WR", "TE", "K", "DST")}
    main = _boot(cov, deltas)
    cdeltas = {(pos, FOCUS + RB.CAL, RB.CONSENSUS, "start_sit_common"): fail_at.get(("ss", pos), (0.0, 0.01))
               for pos in ("QB", "RB", "WR", "TE")}
    cons = _boot({}, cdeltas)
    return H.evaluate_lines(main, cons, FOCUS)


def test_line_logic_pass_and_fail():
    ok = _passing_results()
    assert all(r.passed for r in ok) and len(ok) == 22
    res = _passing_results({
        ("beats", "rolling_median_8", "TE", "pinball_q90"): (-0.01, 0.2),  # interval straddles 0
        ("ss", "RB"): (-0.10, 0.01),  # start/sit interval entirely below 0: worse
        ("ss", "WR"): (+0.10, 0.01),  # entirely above 0: better, passes
        ("cov", "K"): 0.74,
        ("cov", "DST"): 0.85,  # the band is closed
    })
    by_id = {r.line.id: r for r in res}
    failed = sorted(i for i, r in by_id.items() if not r.passed)
    assert failed == ["L12", "L14", "L21"]
    assert "does not beat `rolling_median_8+cal` at TE on pinball loss at q90" in by_id["L12"].detail
    assert "is worse than the FantasyPros consensus" in by_id["L14"].detail
    assert "outside [0.75, 0.85]" in by_id["L21"].detail and by_id["L22"].passed


def test_verdict_paths_approved_not_approved_invalid():
    good = [H.Check("commit", True, "ok")]
    assert H.verdict_text(good, _passing_results()) == "Verdict: APPROVED"
    failing = _passing_results({("cov", "QB"): 0.70})
    assert H.verdict_text(good, failing) == "Verdict: NOT APPROVED"
    bad = [*good, H.Check("FROZEN.json", False, "sha256 0000 (pre-registered 1111)")]
    assert H.verdict_text(bad, _passing_results()).startswith("Verdict: INVALID (FROZEN.json: sha256 0000")
    assert H.verdict_text(good, None).startswith("Verdict: INVALID")
    assert H.verdict_text(good, _passing_results()[:21]).startswith("Verdict: INVALID")  # all 22 must be evaluated
    dry = H.verdict_text(good, _passing_results(), dry_run=True)
    assert dry != "Verdict: APPROVED" and dry.startswith("Dry-run verdict (not a release decision): APPROVED")
    assert H.forward_text(good, _passing_results()) == "Forward check: PASS"
    assert H.forward_text(good, failing) == "Forward check: FAIL"
    assert H.forward_text(bad, _passing_results()) == "Forward check: FAIL"


# --------------------------------------------------------------------------- #
# Integrity checks and the end-to-end build
# --------------------------------------------------------------------------- #


SNAPSHOTS = {"processed": {"fact_player_game": "S1"}, "features": {"player_week": "F1"}}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(root, name, *, season, weeks, git, chain_noise=1.0, mode="frozen"):
    base = truth(seasons=(season,), weeks=tuple(weeks))
    frames = []
    for model, noise, s in [(FOCUS, chain_noise, 5), ("direct_lgbm", 2.0, 4), ("rolling_avg_4", 4.0, 1),
                            ("prior_season", 4.5, 2), ("rolling_median_8", 3.5, 3)]:
        frames.append(model_rows(base, model, noise, s, probs=model == FOCUS, width=6.5))
        frames.append(model_rows(base, model + RB.CAL, noise, s, probs=model == FOCUS, width=6.5))
    run = root / "backtests" / name
    run.mkdir(parents=True)
    pl.concat(frames, how="diagonal_relaxed").write_parquet(run / "predictions.parquet")
    manifest = {"run_id": name, "git": git, "git_sessions": [git], "report_seasons": [season],
                "weeks": list(weeks), "input_snapshots": SNAPSHOTS,
                "calibration": {"mode": mode, "calibrators": {FOCUS: {"path": "artifacts/cal.json",
                                                                      "sha256": _sha(b'{"c": 1}')}}}}
    (run / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _prereg(root, commit, **over):
    _write(root / "artifacts" / "FROZEN.json", '{"frozen": true}')
    _write(root / "artifacts" / "cal.json", '{"c": 1}')
    data = {"candidate": "chain_v1-test", "focus_model": FOCUS, "commit": commit, "holdout_season": 2022,
            "forward": {"season": 2023, "weeks": [1, 2, 3]},
            "frozen": {"path": "artifacts/FROZEN.json", "sha256": _sha(b'{"frozen": true}')},
            "calibrators": {FOCUS: {"path": "artifacts/cal.json", "sha256": _sha(b'{"c": 1}')}},
            "calibration_mode": "frozen", "snapshots": SNAPSHOTS,
            "n_boot": H.SPEC_N_BOOT, "seed": 0, "metric_seed": 0, "lines_sha256": H.lines_spec_sha256()}
    data.update(over)
    md = "# Holdout pre-registration\n\nText.\n\n```json\n" + json.dumps(data, indent=2) + "\n```\n"
    return _write(root / "docs" / "prereg.md", md)


def _log(root, git, sessions=1, purpose="holdout_eval"):
    t = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    lines = []
    for s in range(sessions):
        for i in range(3):
            ts = t + timedelta(hours=5 * s, minutes=i)
            lines.append(f"{ts.isoformat()}\tpurpose={purpose}\tseasons=[2022]\tgit={git}\tuser=tester")
    return _write(root / "artifacts" / "holdout_access.log", "\n".join(lines) + "\n")


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(H, "SPEC_N_BOOT", 8)
    monkeypatch.setattr(H, "_guard", lambda seasons, purpose: None)
    monkeypatch.setattr(H, "_cons_frame", lambda seasons, purpose: _synthetic_consensus(tuple(seasons)))
    monkeypatch.setattr(H, "_cons_exclusions", lambda keys, seasons, purpose: keys.select(list(M.KEY_COLUMNS))
                        .with_columns(pl.lit("early_game").alias("reason")))
    monkeypatch.setattr(RB, "_git", lambda *a: "abc1234")


def _build(root, prereg, hold, fwd=None, dry_run=True, log=None):
    return H.build_holdout_report(hold, fwd, prereg, dry_run=dry_run, access_log=log, n_jobs=1, root=root,
                                  data_dir=root / "out", reports_dir=root / "reports")


def test_commit_relation_x_doc_only_other_path_and_non_descendant(repo):
    root, r, x = repo["root"], repo["R"], repo["X"]
    at_x = H.commit_relation(x, x[:7], root)
    assert at_x.ok and at_x.files == ()
    y = _commit(root, {H.PREREG_DOC: "# pre-registration\n"}, "add pre-registration")
    doc_only = H.commit_relation(x[:7], y, root)
    assert doc_only.ok and doc_only.files == (H.PREREG_DOC,) and doc_only.x == x and doc_only.y == y
    other = _commit(root, {"ffml/code.py": "x = 2\n"}, "code change after the pre-registration")
    touched = H.commit_relation(x, other, root)
    assert not touched.ok and touched.files == (H.PREREG_DOC, "ffml/code.py") and "not exactly" in touched.detail
    _g(root, "checkout", "-q", "-b", "side", r)
    z = _commit(root, {H.PREREG_DOC: "# on a side branch\n"}, "not a descendant of X")
    side = H.commit_relation(x, z, root)
    assert not side.ok and "is not a descendant" in side.detail
    assert not H.commit_relation(x, "deadbeefdeadbeef", root).ok  # unknown commit
    # an empty commit on Y keeps the diff from X at exactly the pre-registration file: still valid
    _g(root, "checkout", "-q", y)
    _g(root, "commit", "-q", "--allow-empty", "-m", "empty on Y")
    assert H.commit_relation(x, _g(root, "rev-parse", "HEAD"), root).ok
    # an empty commit directly on X has an empty diff, which is not exactly the pre-registration file
    _g(root, "checkout", "-q", x)
    _g(root, "commit", "-q", "--allow-empty", "-m", "empty on X")
    assert not H.commit_relation(x, _g(root, "rev-parse", "HEAD"), root).ok


def _integrity(root, prereg_commit, run_git, log_git):
    hold = _run(root, f"hold_{run_git[:7]}", season=2022, weeks=(1, 2), git=run_git[:7])
    checks = H.integrity_checks(H.read_manifest(hold), H.load_prereg(_prereg(root, prereg_commit)), root=root,
                                season=2022, access_log=_log(root, log_git[:7]))
    return {c.name: c for c in checks}


def test_integrity_commit_rule_end_to_end(repo):
    root, x = repo["root"], repo["X"]
    at_x = _integrity(root, x, x, x)
    assert at_x["commit"].ok and at_x["report generation commit"].ok and at_x["holdout access log"].ok
    # the pre-registration is committed after X; the run, the session and the generator run at Y
    y = _commit(root, {H.PREREG_DOC: "# pre-registration\n"}, "add pre-registration")
    at_y = _integrity(root, x, y, y)
    assert all(at_y[k].ok for k in ("commit", "report generation commit", "holdout access log"))
    assert f"X {x[:12]}" in at_y["commit"].detail and H.PREREG_DOC in at_y["commit"].detail
    # anything else changed after X: every commit check fails
    bad = _commit(root, {"ffml/code.py": "x = 3\n"}, "a code change")
    after = _integrity(root, x, bad, bad)
    assert not after["commit"].ok and not after["report generation commit"].ok and not after["holdout access log"].ok
    assert "ffml/code.py" in after["commit"].detail
    # a run made at a non-descendant commit is invalid even when the generator itself is fine
    _g(root, "checkout", "-q", y)
    _g(root, "checkout", "-q", "-b", "side", repo["R"])
    z = _commit(root, {"README.md": "side\n"}, "side")
    _g(root, "checkout", "-q", y)
    side = _integrity(root, x, z, y)
    assert not side["commit"].ok and "not a descendant" in side["commit"].detail
    assert side["report generation commit"].ok and side["holdout access log"].ok


def test_integrity_failures_give_invalid_and_compute_nothing(repo, small, monkeypatch):
    tmp_path, commit = repo["root"], repo["X"]
    called = []
    monkeypatch.setattr(H, "compute_run", lambda *a, **k: called.append(1) or H.RunResult())
    hold = _run(tmp_path, "hold", season=2022, weeks=(1, 2, 3, 4), git=commit[:7])
    log = _log(tmp_path, commit)
    cases = {
        "FROZEN.json": dict(frozen={"path": "artifacts/FROZEN.json", "sha256": "0" * 64}),
        "commit": dict(commit=repo["R"]),  # the run's commit X is not X' = R or a doc-only descendant of it
        "snapshot ids": dict(snapshots={"processed": {"fact_player_game": "OTHER"}}),
        "approval lines": dict(lines_sha256="1" * 64),
        "bootstrap and seeds": dict(seed=1),
        "calibration mode": dict(calibration_mode="online"),
        # the run used artifacts/cal.json, which this pre-registration does not list
        "calibrators used by the run": dict(calibrators={FOCUS: {"path": "artifacts/other.json",
                                                                 "sha256": _sha(b'{"o": 1}')}}),
    }
    _write(tmp_path / "artifacts" / "other.json", '{"o": 1}')
    for name, over in cases.items():
        over = dict(over)
        pre = _prereg(tmp_path, over.pop("commit", commit), **over)
        text = _build(tmp_path, pre, hold, log=log).read_text(encoding="utf-8")
        assert f"Dry-run verdict (not a release decision): INVALID ({name}" in text or f"; {name}:" in text, name
    two = _log(tmp_path, commit, sessions=2)
    text = _build(tmp_path, _prereg(tmp_path, commit), hold, log=two).read_text(encoding="utf-8")
    assert "INVALID" in text and "2 access sessions" in text
    wrong = _log(tmp_path, commit, purpose="backtest")
    assert "session purposes ['backtest']" in _build(tmp_path, _prereg(tmp_path, commit), hold,
                                                     log=wrong).read_text(encoding="utf-8")
    assert called == []  # no metric was computed on any invalid session
    with pytest.raises(ValueError, match="dry run"):
        H.build_holdout_report(hold, None, _prereg(tmp_path, commit), access_log=log, root=tmp_path)


def test_forward_fail_never_changes_the_holdout_verdict(repo, small):
    tmp_path, commit = repo["root"], repo["X"]
    hold = _run(tmp_path, "hold", season=2022, weeks=tuple(range(1, 9)), git=commit[:7])
    fwd_bad = _run(tmp_path, "fwd", season=2023, weeks=(1, 2, 3), chain_noise=9.0, git=commit[:7])  # chain loses
    prereg = _prereg(tmp_path, commit)
    log = _log(tmp_path, commit)
    alone = _build(tmp_path, prereg, hold, log=log).read_text(encoding="utf-8")
    both = _build(tmp_path, prereg, hold, fwd_bad, log=log).read_text(encoding="utf-8")
    verdict = [line for line in alone.splitlines() if line.startswith("Dry-run verdict")]
    assert verdict and verdict == [line for line in both.splitlines() if line.startswith("Dry-run verdict")]
    assert "INVALID" not in verdict[0]
    assert "Forward check: FAIL" in both and "Forward check" not in alone.split("## Pre-registration")[0]
    assert "### The 22 pre-registered lines" in both and both.count("### Integrity checks") == 2
    assert "DRY RUN" in both.splitlines()[0]
    # the report shows the information sections outside the 22 lines
    for heading in ("#### Verdict grid, calibrated", "#### Closed coverage and tails",
                    "#### Boom and bust calibration", "#### Thursday split"):
        assert heading in both
    # a dry run never writes under reports/ and its verdict line cannot approve a model
    assert not (tmp_path / "reports").exists()
    assert all(line.strip() != "Verdict: APPROVED" for line in both.splitlines())


def test_real_release_report_path_and_exact_verdict_line(repo, small, monkeypatch):
    """A non-dry run writes reports/evaluator_release_<candidate>.md with one exact verdict line."""
    tmp_path, commit = repo["root"], repo["X"]
    hold = _run(tmp_path, "hold", season=2022, weeks=tuple(range(1, 9)), git=commit[:7])
    prereg = _prereg(tmp_path, commit)
    log = _log(tmp_path, commit)
    monkeypatch.setattr(H, "get_settings", lambda: type("S", (), {"artifacts_dir": log.parent})())
    path = _build(tmp_path, prereg, hold, dry_run=False)
    assert path == tmp_path / "reports" / "evaluator_release_chain_v1-test.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    verdicts = [line for line in lines if line.startswith("Verdict:")]
    assert len(verdicts) == 1 and verdicts[0] in ("Verdict: APPROVED", "Verdict: NOT APPROVED")
    if verdicts[0] == "Verdict: NOT APPROVED":
        assert "Failing lines in plain words:" in path.read_text(encoding="utf-8")


def test_positions_constant_matches_fixture():
    assert set(POS_COUNTS) == set(H.COVERAGE_POSITIONS)
