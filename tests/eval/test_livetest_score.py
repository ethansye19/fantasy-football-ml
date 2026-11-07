"""Live-test scorer (ffml/eval/livetest_score.py) on fixtures: a fake branch directory, fake commitments,
a push record, a dim_game with one game not final, synthetic outcomes. No real or restricted rows."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.eval import livetest_score as LS
from ffml.eval import report_backtest as RB
from ffml.eval import report_holdout as H

KICKOFF = datetime(2026, 11, 13, 1, 15, tzinfo=UTC)
TEAMS = [f"T{i:02d}" for i in range(8)]
Z = {0.05: -1.645, 0.10: -1.2816, 0.25: -0.6745, 0.50: 0.0, 0.75: 0.6745, 0.90: 1.2816, 0.95: 1.645}


def _rows(season: int, week: int, model: str, seed: int, noise: float = 1.5) -> pl.DataFrame:
    rng = np.random.default_rng(seed + week)
    rows = []
    for gi in range(4):
        gid = f"{season}_{week:02d}_{TEAMS[2 * gi]}_{TEAMS[2 * gi + 1]}"
        for team in (TEAMS[2 * gi], TEAMS[2 * gi + 1]):
            for pos, n in (("QB", 3), ("RB", 3), ("WR", 4), ("TE", 3), ("K", 1), ("DST", 1)):
                for i in range(n):
                    eid = team if pos == "DST" else f"{team}-{pos}{i}"
                    mu = 4.0 + 12.0 * (n - i) / n
                    q50 = mu + rng.normal(0, noise)
                    for scoring in ("ppr", "half"):
                        rows.append({"entity_id": eid, "position": pos, "team": team, "game_id": gid, "season": season,
                                     "week": week, "scoring": scoring, "model": model, "pred_mean": q50 + 0.2,
                                     **{f"pred_q{int(q * 100):02d}": max(0.0, q50 + z * 5.0) for q, z in Z.items()},
                                     "boom_prob": 0.3, "bust_prob": 0.3, "active_prob": 0.9,
                                     "pred_mean_uncond": q50 * 0.9, "pred_q50_uncond": q50 * 0.9,
                                     "approval_status": "not approved", "_mu": mu})
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def fake_outcomes(frame: pl.DataFrame, season: int) -> pl.DataFrame:
    """Outcome columns for any frame carrying ``_mu`` (deterministic per entity-week)."""
    key = (pl.col("entity_id").hash() + pl.col("week").cast(pl.UInt64) * 7919) % 1000
    actual = (pl.col("_mu") + (key.cast(pl.Float64) / 1000.0 - 0.5) * 12.0).clip(0.0).round(1)
    return frame.with_columns(
        actual.alias("actual"), (key % 10 != 0).alias("active"),
        (actual > 15).cast(pl.Float64).alias("boom_actual"), (actual < 6).cast(pl.Float64).alias("bust_actual"),
        pl.lit(-4.0).alias("pos_min"), pl.lit(60.0).alias("pos_max"), pl.lit(KICKOFF).alias("week_cutoff"),
    ).drop("_mu")


def dim_game(season: int, weeks, unfinished: dict | None = None) -> pl.DataFrame:
    rows = []
    for w in weeks:
        for gi in range(4):
            gid = f"{season}_{w:02d}_{TEAMS[2 * gi]}_{TEAMS[2 * gi + 1]}"
            status = "scheduled" if (unfinished or {}).get(w) == gi else "final"
            rows.append({"season": season, "week": w, "game_type": "REG", "game_id": gid,
                         "kickoff_utc": KICKOFF + timedelta(days=7 * (w - 10), hours=gi), "status": status})
    return pl.DataFrame(rows)


def make_week(tmp_path, season: int, week: int, *, kickoff: datetime = KICKOFF, pushed: datetime | None = None,
              rehearsal: bool = False, tamper: str | None = None, code_commit: str = "0" * 40):
    """Local outputs, a branch directory entry and a push record for one week."""
    local = tmp_path / "data" / "livetest" / f"{season}w{week:02d}" / "pred"
    local.mkdir(parents=True, exist_ok=True)
    cal = _rows(season, week, "chain_v2", 1).with_columns(pl.lit("walk_forward:abc").alias("calibration_version"))
    raw = _rows(season, week, "chain_v1", 1)
    cal.write_parquet(local / LS.SUMMARY_NAME)
    raw.write_parquet(local / LS.RAW_SUMMARY_NAME)
    if tamper == "raw":
        raw.with_columns(pl.col("pred_q50") + 1.0).write_parquet(local / LS.RAW_SUMMARY_NAME)
    outputs = [{"path": str((local / n).relative_to(tmp_path)).replace("\\", "/"), "role": "predictions",
                "sha256": LS.sha256_file(local / n), "bytes": (local / n).stat().st_size}
               for n in (LS.SUMMARY_NAME, LS.RAW_SUMMARY_NAME)]
    if tamper == "raw":
        outputs[1]["sha256"] = "0" * 64
    payload = {
        "format": "ffml.livetest.commitment/1", "model": "chain_v2", "season": season, "week": week,
        "rehearsal": rehearsal, "environment": "private_test", "approval_status": "not approved", "label": "not approved",
        "created_at": (kickoff - timedelta(hours=20)).isoformat(), "first_kickoff": kickoff.isoformat(),
        "model_version": f"chain_v2-primary-c{season}w{week:02d}-20261112T051500Z",
        "calibrator_state": {"sha256": "f" * 64, "calibration_version": "v3-tie-aware-quantile",
                             "quantile_calibrator": "OnlinePITQuantile(x)", "isotonic": "OnlineIsotonic(y)",
                             "history_last": [season, week - 1] if week > 1 else [season - 1, 18]},
        "calibration_history": {"sha256": "e" * 64, "weeks": 1, "sources": []},
        "outputs": outputs, "pins": {"selection_sha256": "sel-crlf", "feature_version": "0.2.0",
                                     "feature_manifest_sha256": "fm"},
        "snapshots": {"processed_live_rows": {"dim_game": "20261112T000000Z"}},
        "code": {"commit": code_commit, "clean": True},
    }
    payload["digest"] = {"algorithm": "sha256", "of": "canonical JSON of every other top-level field",
                         "value": LS.canonical_digest(payload)}
    if tamper == "digest":
        payload["created_at"] = (kickoff - timedelta(hours=21)).isoformat()  # digest no longer matches
    raw_json = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    branch = tmp_path / "branch" / f"{season}w{week:02d}"
    branch.mkdir(parents=True, exist_ok=True)
    (branch / LS.COMMITMENT_NAME).write_bytes(raw_json)
    (branch / LS.SUMMARY_NAME).write_bytes((local / LS.SUMMARY_NAME).read_bytes())
    stamp = pushed if pushed is not None else kickoff - timedelta(hours=19)
    wk = f"{season}w{week:02d}"
    record = {"season": season, "week": week, "commit_sha": "b" * 40, "commitment_sha256": LS.sha256_bytes(raw_json),
              "files_committed": {f"{wk}/{LS.COMMITMENT_NAME}": LS.sha256_bytes(raw_json),
                                  f"{wk}/{LS.SUMMARY_NAME}": LS.sha256_file(local / LS.SUMMARY_NAME)},
              "commitment_digest": payload["digest"]["value"], "external_timestamp": stamp.isoformat(),
              "external_timestamp_source": "GitHub repository activity API (server time of the push)",
              "github": {"push_activity": {"timestamp": stamp.isoformat(), "after": "b" * 40}}}
    return record


PREREG = {
    "commit": "<frozen commit>", "season": 2026, "weeks": [10, 11], "window": [[2026, 10], [2026, 11]],
    "rules": {"lines_sha256": H.lines_spec_sha256()}, "n_boot": 500, "seed": 0, "metric_seed": 0,
    "missed_weeks": {"allowed": 2},
    "pins": {"selection": {"path": "ffml/models/chain/selection.json", "sha256": "sel-lf",
                           "sha256_crlf_working_copy": "sel-crlf"},
             "feature_manifest": {"path": "x", "sha256": "fm"}, "feature_version": "0.2.0"},
    "calibration": {"version": "v3-tie-aware-quantile", "quantiles": "OnlinePITQuantile(x)", "isotonic": "OnlineIsotonic(y)"},
    "frozen_paths": ["ffml/models/", "ffml/eval/metrics.py", "seeds"],
    "allowed_paths": {"ffml/data/": "fixes", "scripts/": "runner", "api/, web/": "app", "other docs/, reports/": "docs"},
    "allowed_changes_after_freeze": ["docs/status/livetest-preregistration-2026.md"],
}


def _prereg(tmp_path, **over):
    data = {**PREREG, **over}
    p = tmp_path / "prereg.md"
    p.write_text("# prereg\n\n```json\n" + json.dumps(data, indent=1) + "\n```\n", encoding="utf-8")
    return H.load_prereg(p)


def _baselines(season, week):
    frames = []
    for b, seed in (("rolling_avg_4", 11), ("prior_season", 12), ("rolling_median_8", 13)):
        frames.append(_rows(season, week, b, seed, noise=4.0))
        frames.append(_rows(season, week, b + RB.CAL, seed, noise=4.0))
    return fake_outcomes(pl.concat(frames), season)


def _consensus(season, week):
    c = _rows(season, week, RB.CONSENSUS, 21, noise=2.0).filter(pl.col("scoring") == "ppr").drop("scoring")
    c = fake_outcomes(c, season).select("entity_id", "position", "season", "week", "pred_mean", "pred_q10", "pred_q50",
                                        "pred_q90", "actual", "active", "model", "team", "game_id")
    return c.with_columns(pl.col("pred_q50").rank("ordinal", descending=True).over(["position", "season", "week"])
                          .cast(pl.Float64).alias("rank_pred"))


def _score(tmp_path, season, week, record, dg, **kw):
    return LS.score_week(season, week, source=LS.BranchSource(tmp_path / "branch"), dim_game=dg, push_record=record,
                         roots=[tmp_path], outcomes=fake_outcomes, baselines=_baselines(season, week),
                         consensus=_consensus(season, week), prereg=kw.pop("prereg", _prereg(tmp_path)),
                         root=tmp_path / "scoring", reports_dir=tmp_path / "reports", n_boot=40, **kw)


def test_canonical_digest_equals_the_builder_and_runner_forms():
    from ffml.sim.livetest import commitment_digest

    payload = {"b": [1, 2], "a": {"y": "é", "x": None}, "digest": {"value": "old"}, "t": datetime(2026, 1, 1, tzinfo=UTC)}
    assert LS.canonical_digest(payload) == commitment_digest(dict(payload))


def test_week_status_and_refusal_when_one_game_is_not_final(tmp_path):
    dg = dim_game(2026, [10], unfinished={10: 2})
    st = LS.week_status(dg, 2026, 10)
    assert (st.games, st.final, st.is_final) == (4, 3, False) and len(st.unfinished) == 1
    record = make_week(tmp_path, 2026, 10)
    with pytest.raises(LS.ScoreRefused, match="week final"):
        _score(tmp_path, 2026, 10, record, dg)
    assert not (tmp_path / "scoring" / "parts").exists()


def test_refusals_digest_hash_push_and_rehearsal(tmp_path):
    dg = dim_game(2026, [10])
    with pytest.raises(LS.ScoreRefused, match="digest"):
        _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10, tamper="digest"), dg)
    with pytest.raises(LS.ScoreRefused, match="file hashes"):
        _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10, tamper="raw"), dg)
    late = make_week(tmp_path, 2026, 10, pushed=KICKOFF + timedelta(minutes=1))
    with pytest.raises(LS.ScoreRefused, match="pushed before the deadline"):
        _score(tmp_path, 2026, 10, late, dg)
    # the binding deadline is the earlier kickoff: a game moved two hours earlier makes this push late
    early = dim_game(2026, [10]).with_columns((pl.col("kickoff_utc") - timedelta(hours=2)).alias("kickoff_utc"))
    with pytest.raises(LS.ScoreRefused, match="pushed before the deadline"):
        _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10, pushed=KICKOFF - timedelta(hours=1)), early)
    record = make_week(tmp_path, 2026, 10)
    with pytest.raises(LS.ScoreRefused, match="push record"):
        _score(tmp_path, 2026, 10, None, dg)
    no_gh = {**record, "external_timestamp_source": "none: local clock only", "github": {}}
    with pytest.raises(LS.ScoreRefused, match="GitHub"):
        _score(tmp_path, 2026, 10, no_gh, dg)
    rec_r = make_week(tmp_path, 2026, 10, rehearsal=True)
    with pytest.raises(LS.ScoreRefused, match="rehearsal"):
        _score(tmp_path, 2026, 10, rec_r, dg)
    rec, out = _score(tmp_path, 2026, 10, rec_r, dg, allow_rehearsal=True)
    assert rec.rehearsal and out.is_file()


METRIC_WORDS = ("MAE", "pinball", "coverage", "start/sit", "Spearman", "Verdict", "not passed", "beats", "pass ")


def _assert_no_metrics(text: str) -> None:
    """The weekly outputs carry hashes, timestamps and counts only: no decimal number, no metric word."""
    import re

    body = re.sub(r"\b\d+\.\d+\.\d+\b", " ", text)  # dotted versions such as 0.2.0 are not metrics
    decimals = re.findall(r"\b\d+\.\d+\b", body)
    assert decimals == [], decimals
    for word in METRIC_WORDS:
        assert word not in text, word


def _push_seal(tmp_path, wk: str, tamper: bool = False) -> None:
    """The runner's push of the sealed-file hashes to the branch (fake branch directory)."""
    src = tmp_path / "scoring" / LS.SEALED_DIR / f"{wk}.sha256"
    dst = tmp_path / "branch" / LS.SEALED_BRANCH_DIR / f"{wk}.sha256"
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    dst.write_text(text.replace("a", "b", 1) if tamper else text, encoding="utf-8")


def test_weekly_outputs_are_sealed_and_verification_only(tmp_path):
    dg = dim_game(2026, [10, 11])
    rec, out = _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg)
    text = out.read_text(encoding="utf-8")
    assert out.name == "2026w10_verification.md" and "verification only" in text
    _assert_no_metrics(text)
    assert rec.refusals == [] and rec.files_verified == 2
    names = {c.name: c.ok for c in rec.checks}
    assert names["digest"] and names["pushed before the deadline (GitHub timestamp)"] and names["pin: selection.json"]
    assert names["pushed blobs unchanged"]
    assert rec.deadline == KICKOFF.isoformat(timespec="seconds")
    assert names["code commit"] is False  # the pre-registration has no frozen commit yet
    sealed = tmp_path / "scoring" / LS.SEALED_DIR
    part = LS.part_path(tmp_path / "scoring", 2026, 10)
    record = json.loads(LS.record_path(tmp_path / "scoring", 2026, 10).read_text(encoding="utf-8"))
    seals = LS.read_seal((sealed / "2026w10.sha256").read_text(encoding="utf-8"))
    cpart = LS.consensus_part_path(tmp_path / "scoring", 2026, 10)
    assert part.is_file() and cpart.is_file() and seals == {
        part.name: LS.sha256_file(part), cpart.name: LS.sha256_file(cpart),
        "2026w10.json": LS.sha256_file(sealed / "2026w10.json")}
    assert record["outcome_rows_sha256"] and record["consensus_part_sha256"] == LS.sha256_file(cpart)
    status = json.loads(LS.week_status_path(tmp_path / "scoring", 2026, 10).read_text(encoding="utf-8"))
    assert status["outcome_rows_sha256"] == record["outcome_rows_sha256"] and status["sealed"] == seals
    assert set(status) >= {"dim_game_snapshot", "processed_snapshots", "outcome_rows_sha256"}
    _assert_no_metrics(json.dumps(status))
    _assert_no_metrics((sealed / "2026w10.sha256").read_text(encoding="utf-8"))
    # the metrics exist, sealed in the JSON only
    m = record["sealed_metrics"]
    assert len(m["week_lines"]) == 22 and len(m["cumulative_lines_500"]) == 22 and m["cumulative_lines_sensitivity"] is None
    assert any(isinstance(v, float) for v in m["week_points"]["QB"]["chain_v2+cal"].values())
    frame = pl.read_parquet(part)
    assert set(frame["model"].unique()) >= {"chain_v2", "chain_v2+cal", "rolling_avg_4+cal"}
    # no report under reports/ carries metrics; the second week accumulates in the sealed JSON only
    _, out2 = _score(tmp_path, 2026, 11, make_week(tmp_path, 2026, 11, kickoff=KICKOFF + timedelta(days=7)), dg,
                     weekly_sensitivity=True, sensitivity_n_boot=60)
    _assert_no_metrics(out2.read_text(encoding="utf-8"))
    rec2 = json.loads(LS.record_path(tmp_path / "scoring", 2026, 11).read_text(encoding="utf-8"))
    assert rec2["sealed_metrics"]["cumulative_weeks"] == [[2026, 10], [2026, 11]]
    assert rec2["sealed_metrics"]["cumulative_lines_sensitivity"]["n_boot"] == 60
    for f in (tmp_path / "reports").glob("*.md"):
        _assert_no_metrics(f.read_text(encoding="utf-8"))


def test_final_refused_until_complete_then_prints_one_verdict(tmp_path):
    prereg = _prereg(tmp_path)
    dg_open = dim_game(2026, [10, 11], unfinished={11: 0})
    src = LS.BranchSource(tmp_path / "branch")
    kw = dict(source=src, prereg=prereg, root=tmp_path / "scoring", reports_dir=tmp_path / "reports", n_boot=40)
    with pytest.raises(LS.ScoreRefused, match="refused until 2026w11 is final"):
        LS.final_evaluation(dim_game=dg_open, **kw)
    dg = dim_game(2026, [10, 11])
    with pytest.raises(LS.ScoreRefused, match="neither sealed nor marked MISSED"):
        LS.final_evaluation(dim_game=dg, **kw)
    _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg, prereg=prereg)
    # week 11 missed: one miss is excluded and the verdict is computed (INVALID here: no frozen commit)
    (tmp_path / "branch" / "2026w11").mkdir(parents=True, exist_ok=True)
    (tmp_path / "branch" / "2026w11" / LS.MISSED_NAME).write_text('{"reason": "offline"}', encoding="utf-8")
    # the sealed hashes must be on the branch before the final reads anything
    with pytest.raises(LS.ScoreRefused, match="sealed files do not match the branch record"):
        LS.final_evaluation(dim_game=dg, **kw)
    _push_seal(tmp_path, "2026w10")
    verdict, out = LS.final_evaluation(dim_game=dg, **kw)
    assert verdict.startswith("Verdict: INVALID") and "frozen commit" in verdict
    text = out.read_text(encoding="utf-8")
    assert text.count("Verdict:") == 1
    # a dry run can never produce the registry's exact line
    v2, _ = LS.final_evaluation(dim_game=dg, dry_run=True, **kw)
    assert v2.startswith("Dry-run verdict") and "Verdict: APPROVED" not in v2
    # a tampered sealed file is refused, as is a tampered branch record
    part = LS.part_path(tmp_path / "scoring", 2026, 10)
    pl.read_parquet(part).head(5).write_parquet(part)
    with pytest.raises(LS.ScoreRefused, match="sha256 differs from the branch record"):
        LS.final_evaluation(dim_game=dg, **kw)


def test_final_verdict_uses_the_binding_draws_only(tmp_path):
    """The 5,000-draw check is report-only: a sensitivity result that fails every line changes nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _g(repo, "init", "-q")
    _g(repo, "config", "user.email", "t@example.com")
    _g(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _g(repo, "add", "a.txt")
    _g(repo, "commit", "-q", "-m", "X")
    x = _g(repo, "rev-parse", "HEAD")
    prereg = _prereg(tmp_path, commit=x)
    dg = dim_game(2026, [10, 11])
    for w, kick in ((10, KICKOFF), (11, KICKOFF + timedelta(days=7))):
        _score(tmp_path, 2026, w, make_week(tmp_path, 2026, w, kickoff=kick, code_commit=x), dg, prereg=prereg,
               repo_root=repo)
        _push_seal(tmp_path, f"2026w{w}")
    kw = dict(source=LS.BranchSource(tmp_path / "branch"), prereg=prereg, root=tmp_path / "scoring",
              reports_dir=tmp_path / "reports", n_boot=40, repo_root=repo)
    base, out = LS.final_evaluation(dim_game=dg, sensitivity=False, **kw)
    assert base in ("Verdict: APPROVED", "Verdict: NOT APPROVED"), base
    frame, _ = LS.load_parts(tmp_path / "scoring", [(2026, 10), (2026, 11)])
    binding = LS.score_frames(frame, LS.load_consensus_parts(tmp_path / "scoring", [(2026, 10), (2026, 11)]),
                              n_boot=40)

    def all_fail(frame_, consensus_):
        sc = LS.score_frames(frame_, consensus_, n_boot=40)
        sc.results = [LS.LineResult(r.line, False, "forced") for r in sc.results]
        return sc

    with_sens, out2 = LS.final_evaluation(dim_game=dg, sensitivity_fn=all_fail, **kw)
    assert with_sens == base
    text = out2.read_text(encoding="utf-8")
    sealed = json.loads((tmp_path / "scoring" / LS.SEALED_DIR / "final_lines.json").read_text(encoding="utf-8"))
    passed_binding = [r.passed for r in binding.results]
    assert [ln["passed"] for ln in sealed["binding"]["lines"]] == passed_binding
    assert sealed["sensitivity"]["n_boot"] == LS.SENSITIVITY_N_BOOT and len(sealed["sensitivity"]["differing"]) == sum(passed_binding)
    assert "Lines whose PASS/FAIL would differ" in text and text.count("Verdict:") == 1
    same, out3 = LS.final_evaluation(dim_game=dg, sensitivity_fn=lambda f, c: LS.score_frames(f, c, n_boot=40), **kw)
    assert same == base and "No line differs" in out3.read_text(encoding="utf-8")


def test_miss_rule_from_the_pre_registration(tmp_path):
    dg = dim_game(2026, [10, 11, 12])
    for w in (11, 12):
        (tmp_path / "branch" / f"2026w{w}").mkdir(parents=True, exist_ok=True)
        (tmp_path / "branch" / f"2026w{w}" / LS.MISSED_NAME).write_text("{}", encoding="utf-8")
    two = _prereg(tmp_path, window=[[2026, 10], [2026, 11], [2026, 12]], missed_weeks={"allowed": 2},
                  miss_rule="two misses excluded, a third is INVALID")
    _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg, prereg=two)
    _push_seal(tmp_path, "2026w10")
    checks = LS.livetest_integrity_checks(two, LS.load_records(tmp_path / "scoring", [(2026, 10)]),
                                          [(2026, 10), (2026, 11), (2026, 12)], {(2026, 11): {}, (2026, 12): {}})
    by = {c.name: c for c in checks}
    assert by["missed weeks (miss rule)"].ok and "2 allowed" in by["missed weeks (miss rule)"].detail
    assert "two misses excluded" in by["missed weeks (miss rule)"].detail
    three = _prereg(tmp_path, window=[[2026, 10], [2026, 11], [2026, 12], [2026, 13]], missed_weeks={"allowed": 2})
    checks = LS.livetest_integrity_checks(three, {}, [(2026, 11), (2026, 12), (2026, 13)],
                                          {(2026, 11): {}, (2026, 12): {}, (2026, 13): {}})
    assert not {c.name: c for c in checks}["missed weeks (miss rule)"].ok


def test_final_second_miss_is_invalid_and_checks_cover_each_week(tmp_path):
    prereg = _prereg(tmp_path, window=[[2026, 10], [2026, 11], [2026, 12]], missed_weeks={"allowed": 1})
    dg = dim_game(2026, [10, 11, 12])
    for w in (11, 12):
        (tmp_path / "branch" / f"2026w{w}").mkdir(parents=True, exist_ok=True)
        (tmp_path / "branch" / f"2026w{w}" / LS.MISSED_NAME).write_text("{}", encoding="utf-8")
    _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg, prereg=prereg)
    _push_seal(tmp_path, "2026w10")
    verdict, _ = LS.final_evaluation(source=LS.BranchSource(tmp_path / "branch"), dim_game=dg, prereg=prereg,
                                     root=tmp_path / "scoring", reports_dir=tmp_path / "reports", n_boot=40)
    assert "INVALID" in verdict and "missed weeks (miss rule)" in verdict
    records = LS.load_records(tmp_path / "scoring", [(2026, 10)])
    checks = LS.livetest_integrity_checks(prereg, records, [(2026, 10), (2026, 11), (2026, 12)],
                                          {(2026, 11): {}, (2026, 12): {}})
    by = {c.name: c for c in checks}
    assert by["approval lines"].ok and by["bootstrap and seeds"].ok and not by["frozen commit"].ok
    rules = {"lines_sha256": H.lines_spec_sha256(), "x": 1}
    good = _prereg(tmp_path, rules={**rules, "rules_sha256": LS.rules_hash(rules)})
    bad = _prereg(tmp_path, rules={**rules, "rules_sha256": "0" * 64})
    assert {c.name: c.ok for c in LS.livetest_integrity_checks(good, {}, [], {})}["rules hash"]
    assert not {c.name: c.ok for c in LS.livetest_integrity_checks(bad, {}, [], {})}["rules hash"]
    assert "2026w10 commitment" in by and by["allowed paths"].ok


def test_allowed_prefixes_and_path_rules():
    allowed, frozen = LS.allowed_prefixes(PREREG)
    assert "scripts/" in allowed and "web/" in allowed and "docs/" in allowed and "reports/" in allowed
    assert "docs/status/livetest-preregistration-2026.md" in allowed and "ffml/models/" in frozen
    assert LS.path_allowed("scripts/livetest_week.py", allowed, frozen)
    assert LS.path_allowed("docs/status/livetest-preregistration-2026.md", allowed, frozen)
    assert not LS.path_allowed("ffml/models/chain/lgbm.py", allowed, frozen)
    assert not LS.path_allowed("ffml/eval/metrics.py", allowed, frozen)
    assert not LS.path_allowed("ffml/eval/power.py", allowed, frozen)  # not listed either way: not allowed


def _g(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def test_commit_relation_allowed_paths(tmp_path):
    _g(tmp_path, "init", "-q")
    _g(tmp_path, "config", "user.email", "t@example.com")
    _g(tmp_path, "config", "user.name", "t")

    def commit(rel, msg):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(msg + "\n", encoding="utf-8")
        _g(tmp_path, "add", rel)
        _g(tmp_path, "commit", "-q", "-m", msg)
        return _g(tmp_path, "rev-parse", "HEAD")

    x = commit("ffml/models/a.py", "X")
    y = commit("scripts/run.py", "allowed")
    z = commit("ffml/models/a.py", "frozen")
    allowed, frozen = LS.allowed_prefixes(PREREG)
    assert LS.commit_relation_allowed(x, x, tmp_path, allowed, frozen).ok
    assert LS.commit_relation_allowed(x, y, tmp_path, allowed, frozen).ok
    rel = LS.commit_relation_allowed(x, z, tmp_path, allowed, frozen)
    assert not rel.ok and "ffml/models/a.py" in rel.detail
    assert not LS.commit_relation_allowed(y, x, tmp_path, allowed, frozen).ok  # not a descendant


CHANGELOG_TEMPLATE = ("# log\n\n| Date (UTC) | Commit | Paths | Reason | Verification | Verified by | First week |\n"
                      "| --- | --- | --- | --- | --- | --- | --- |\n")


def _log_row(sha: str, path: str) -> str:
    return f"| 2026-11-20 | `{sha[:9]}` | {path} | change | no effect on commitments | Evaluator | 2026w11 |\n"


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _g(repo, "init", "-q", "-b", "main")
    _g(repo, "config", "user.email", "t@example.com")
    _g(repo, "config", "user.name", "t")

    def commit(files: dict, msg: str) -> str:
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            _g(repo, "add", rel)
        _g(repo, "commit", "-q", "-m", msg)
        return _g(repo, "rev-parse", "HEAD")

    def append_log(rows: str, msg: str) -> str:
        p = repo / LS.CHANGELOG_DOC
        return commit({LS.CHANGELOG_DOC: p.read_text(encoding="utf-8") + rows}, msg)

    return repo, commit, append_log


def test_changelog_commits_and_descendant_coverage(tmp_path):
    """D-116 A2: each scored week's build commit must already hold the entries of every commit since X."""
    repo, commit, append_log = _repo(tmp_path)
    x = commit({"ffml/models/a.py": "X\n", LS.CHANGELOG_DOC: CHANGELOG_TEMPLATE}, "X")
    y = commit({"scripts/run.py": "y\n"}, "allowed change")
    log_y = append_log(_log_row(y, "scripts/run.py"), "log y")
    z = commit({"scripts/other.py": "z\n"}, "unlogged change")
    logged = LS.changelog_commits((repo / LS.CHANGELOG_DOC).read_text(encoding="utf-8"))
    assert logged == {y[:9]}
    assert LS.uncovered_commits(x, x, repo, logged) == [] and LS.uncovered_commits(x, log_y, repo, logged) == []
    assert LS.uncovered_commits(x, z, repo, logged) == [z]
    assert LS.changelog_at(y, repo) == set() and LS.changelog_at(log_y, repo) == {y[:9]}
    prereg = _prereg(tmp_path, commit=x, frozen_paths=["ffml/models/"], allowed_paths={"scripts/": "runner",
                                                                                        "docs/": "docs"})
    records = {(2026, 10): {"code_commit": log_y, "checks": [], "commitment_sha256": "a" * 64, "files_verified": 2},
               (2026, 11): {"code_commit": y, "checks": [], "commitment_sha256": "b" * 64, "files_verified": 2},
               (2026, 12): {"code_commit": z, "checks": [], "commitment_sha256": "c" * 64, "files_verified": 2}}
    checks = LS.livetest_integrity_checks(prereg, records, [(2026, 10), (2026, 11), (2026, 12)], {}, repo_root=repo)
    by = {c.name: c for c in checks}
    assert by["frozen commit"].ok and by["2026w10 change log"].ok
    # built at y before its entry existed: logged after first use, even though HEAD's log covers y
    assert not by["2026w11 change log"].ok and "not in the change log at the build commit" in by["2026w11 change log"].detail
    assert not by["2026w12 change log"].ok
    assert by["2026w11 commitment"].ok and by["2026w12 commitment"].ok  # the path rule itself is satisfied


def test_prereg_window_and_week_parsing():
    assert LS.prereg_window({"window": [[2026, 10], [2027, 1]]}) == [(2026, 10), (2027, 1)]
    assert LS.prereg_window({"window": {"seasons": [2026, 2027], "weeks": {"2026": [10, 11], "2027": [1]}}}) == [
        (2026, 10), (2026, 11), (2027, 1)]
    assert LS.prereg_window({"season": 2026, "weeks": list(range(10, 19))}) == list(LS.DEFAULT_WINDOW)
    assert LS.prereg_window({"season": 2026, "weeks": list(range(10, 19)),
                             "extension": {"season": 2027, "weeks": list(range(1, 10))}}) == list(LS.DEFAULT_WINDOW)
    assert LS.parse_week("2026w10") == (2026, 10) and LS.week_key(2027, 9) == "2027w09"
    with pytest.raises(ValueError):
        LS.parse_week("week10")


def test_changelog_only_exemption_is_narrow(tmp_path):
    """Only a non-merge commit that appends to the change log alone is exempt from needing its own entry."""
    _g(tmp_path, "init", "-q", "-b", "main")
    _g(tmp_path, "config", "user.email", "t@example.com")
    _g(tmp_path, "config", "user.name", "t")
    log = tmp_path / LS.CHANGELOG_DOC
    log.parent.mkdir(parents=True, exist_ok=True)
    header = ("# log\n\n| Date (UTC) | Commit | Paths | Reason | Verification | Verified by | First week |\n"
              "| --- | --- | --- | --- | --- | --- | --- |\n")

    def write(rel, text, msg):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        _g(tmp_path, "add", rel)
        _g(tmp_path, "commit", "-q", "-m", msg)
        return _g(tmp_path, "rev-parse", "HEAD")

    def row(sha, note="entry"):
        return f"| 2026-11-20 | `{sha[:9]}` | scripts/ | {note} | none | Evaluator | 2026w11 |\n"

    log.write_text(header, encoding="utf-8", newline="\n")
    x = write(LS.CHANGELOG_DOC, header, "X: frozen commit with the empty log")
    a = write("scripts/a.py", "a\n", "allowed change a")
    app = write(LS.CHANGELOG_DOC, header + row(a), "append the entry for a")       # append only: exempt
    assert LS._changelog_append_only(app, tmp_path)
    logged = LS.changelog_commits(log.read_text(encoding="utf-8"))
    assert LS.uncovered_commits(x, app, tmp_path, logged) == []

    edit = write(LS.CHANGELOG_DOC, header + row(a, "entry, reworded"), "edit an existing entry")
    assert not LS._changelog_append_only(edit, tmp_path)                         # an edit deletes a line
    assert LS.uncovered_commits(x, edit, tmp_path, logged) == [edit]

    delete = write(LS.CHANGELOG_DOC, header, "delete an entry")
    assert not LS._changelog_append_only(delete, tmp_path)
    b = write("scripts/b.py", "b\n", "allowed change b")
    both_path = tmp_path / "docs" / "n.md"
    both_path.parent.mkdir(parents=True, exist_ok=True)
    both_path.write_text("n\n", encoding="utf-8")
    log.write_text(header + row(b), encoding="utf-8", newline="\n")
    _g(tmp_path, "add", "docs/n.md", LS.CHANGELOG_DOC)
    _g(tmp_path, "commit", "-q", "-m", "log plus another path")
    both = _g(tmp_path, "rev-parse", "HEAD")
    assert not LS._changelog_append_only(both, tmp_path)                         # another path in the same commit

    # a merge commit whose branch only appended to the log is still not exempt
    _g(tmp_path, "checkout", "-q", "-b", "side")
    side = write(LS.CHANGELOG_DOC, header + row(b) + row(both), "append on a side branch")
    assert LS._changelog_append_only(side, tmp_path)
    _g(tmp_path, "checkout", "-q", "main")
    c = write("scripts/c.py", "c\n", "allowed change c")
    _g(tmp_path, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = _g(tmp_path, "rev-parse", "HEAD")
    assert not LS._changelog_append_only(merge, tmp_path)
    logged = LS.changelog_commits(log.read_text(encoding="utf-8")) | {edit[:9], delete[:9], c[:9]}
    # the merge needs its own entry, and a whose entry was deleted is uncovered again
    assert set(LS.uncovered_commits(x, merge, tmp_path, logged)) == {merge, a}
    # the root commit has no parent and is never exempt
    assert not LS._changelog_append_only(x, tmp_path)


# --------------------------------------------------------------------------- #
# Pre-freeze fixes (EV-4.6): QA-F27, QA-F28, QA-F29, the allowed read
# --------------------------------------------------------------------------- #


def test_reports_go_to_settings_reports_dir_not_the_checkout(tmp_path, monkeypatch):
    """QA-F27: a weekly run with the default reports dir writes under FFML_REPORTS_DIR and leaves the checkout clean."""
    from ffml.config import PROJECT_ROOT, reset_settings_cache

    monkeypatch.setenv("FFML_REPORTS_DIR", str(tmp_path / "settings-reports"))
    monkeypatch.setenv("FFML_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()
    try:
        before = _g(PROJECT_ROOT, "status", "--porcelain", "--untracked-files=all", "--", "reports", "data")
        dg = dim_game(2026, [10])
        rec, out = LS.score_week(2026, 10, source=LS.BranchSource(tmp_path / "branch"), dim_game=dg,
                                 push_record=make_week(tmp_path, 2026, 10), roots=[tmp_path], outcomes=fake_outcomes,
                                 baselines=_baselines(2026, 10), consensus=_consensus(2026, 10),
                                 prereg=_prereg(tmp_path), n_boot=20)
        after = _g(PROJECT_ROOT, "status", "--porcelain", "--untracked-files=all", "--", "reports", "data")
        assert out == tmp_path / "settings-reports" / "livetest" / "2026w10_verification.md" and out.is_file()
        assert LS.part_path(tmp_path / "data" / "livetest" / "scoring", 2026, 10).is_file()
        assert after == before  # nothing new under the checkout's reports/ or data/
    finally:
        reset_settings_cache()


def _two_week_world(tmp_path):
    prereg = _prereg(tmp_path, window=[[2026, 10], [2026, 11]])
    dg = dim_game(2026, [10, 11])
    for w, kick in ((10, KICKOFF), (11, KICKOFF + timedelta(days=7))):
        _score(tmp_path, 2026, w, make_week(tmp_path, 2026, w, kickoff=kick), dg, prereg=prereg)
        _push_seal(tmp_path, f"2026w{w}")
    kw = dict(source=LS.BranchSource(tmp_path / "branch"), prereg=prereg, root=tmp_path / "scoring",
              reports_dir=tmp_path / "reports", n_boot=20, sensitivity=False)
    return dg, kw


def test_missed_marker_cannot_exclude_a_committed_week(tmp_path):
    """QA-F28: a hand-pushed MISSED.json for a committed, sealed week never excludes it."""
    dg, kw = _two_week_world(tmp_path)
    (tmp_path / "branch" / "2026w11" / LS.MISSED_NAME).write_text(
        '{"reason": "hand-pushed", "created_at": "2026-11-21T09:00:00+00:00"}', encoding="utf-8")
    # with the local sealed files in place: the week is scored and the marker is listed, not used
    verdict, out = LS.final_evaluation(dim_game=dg, **kw)
    text = out.read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith("| 2026w11 |"))
    assert "2026-11-21T09:00:00+00:00" in row and "a commitment.json on the branch" in row
    assert "a sealed-hash record on the branch" in row and "n/a (not a git ref)" in row
    flagged = json.loads((tmp_path / "scoring" / LS.SEALED_DIR / "final_lines.json").read_text(encoding="utf-8"))
    assert [f["week"] for f in flagged["flagged_markers"]] == ["2026w11"]
    assert "markers ignored for committed weeks, which are scored: 2026w11" in text
    assert "Integrity findings that do not change the verdict" in text and "0 missed week(s)" in text
    # the attack: the local sealed files moved away -> refused, never a 1-week-shorter verdict
    sealed = tmp_path / "scoring" / LS.SEALED_DIR
    for f in sealed.glob("2026w11.*"):
        f.rename(tmp_path / f.name)
    with pytest.raises(LS.ScoreRefused, match="never excluded as a miss"):
        LS.final_evaluation(dim_game=dg, **kw)
    # a genuine miss (no commitment, no seal on the branch) is still excluded
    import shutil

    shutil.rmtree(tmp_path / "branch" / "2026w11")
    (tmp_path / "branch" / LS.SEALED_BRANCH_DIR / "2026w11.sha256").unlink()
    (tmp_path / "branch" / "2026w11").mkdir()
    (tmp_path / "branch" / "2026w11" / LS.MISSED_NAME).write_text('{"reason": "offline"}', encoding="utf-8")
    verdict, out = LS.final_evaluation(dim_game=dg, **kw)
    assert "1 missed week(s) ['2026w11'], 2 allowed" in out.read_text(encoding="utf-8")


def test_final_checks_the_branch_record_against_what_was_scored(tmp_path):
    """QA-F29: a commitment or summary on the branch record that differs from the scored one fails the final."""
    dg, kw = _two_week_world(tmp_path)
    verdict, out = LS.final_evaluation(dim_game=dg, **kw)
    assert "2026w10 branch commitment | PASS" in out.read_text(encoding="utf-8")
    c = tmp_path / "branch" / "2026w10" / LS.COMMITMENT_NAME
    c.write_bytes(c.read_bytes() + b" ")
    verdict, out = LS.final_evaluation(dim_game=dg, **kw)
    assert verdict.startswith("Verdict: INVALID") and "the branch commitment differs from the scored one" in verdict
    ref = {"ref": "origin/livetest-2026", "sha": "a" * 40, "ok": False, "fetched_at": "t", "detail": "differs"}
    verdict, _ = LS.final_evaluation(dim_game=dg, branch_ref=ref, **kw)
    assert "branch record: differs" in verdict


def test_fetch_branch_ref_refuses_without_a_remote(tmp_path):
    _g(tmp_path, "init", "-q")
    with pytest.raises(LS.ScoreRefused, match="git fetch origin livetest-2026 failed"):
        LS.fetch_branch_ref(tmp_path)


def test_fetch_branch_ref_reads_the_remote_head(tmp_path):
    remote, clone = tmp_path / "remote.git", tmp_path / "clone"
    _g(tmp_path, "init", "-q", "--bare", str(remote))
    _g(tmp_path, "init", "-q", str(clone))
    _g(clone, "config", "user.email", "t@example.com")
    _g(clone, "config", "user.name", "t")
    _g(clone, "checkout", "-q", "-b", "livetest-2026")
    (clone / "f.txt").write_text("x\n", encoding="utf-8")
    _g(clone, "add", "f.txt")
    _g(clone, "commit", "-q", "-m", "c")
    _g(clone, "remote", "add", "origin", str(remote))
    _g(clone, "push", "-q", "origin", "livetest-2026")
    ref = LS.fetch_branch_ref(clone)
    assert ref["ok"] and ref["sha"] == _g(clone, "rev-parse", "HEAD") and ref["ref"] == "origin/livetest-2026"


@pytest.mark.parametrize("allowed", [0, None, "2", True, -1])
def test_missed_weeks_allowed_is_read_exactly(tmp_path, allowed):
    prereg = _prereg(tmp_path, missed_weeks={} if allowed is None else {"allowed": allowed})
    checks = LS.livetest_integrity_checks(prereg, {}, [(2026, 10)], {(2026, 10): {}})
    c = {x.name: x for x in checks}["missed weeks (miss rule)"]
    assert not c.ok  # 0 allowed with one miss fails; missing, a string, a bool or a negative number fail as invalid
    if allowed == 0:
        assert "0 allowed" in c.detail
    else:
        assert "must be a non-negative integer" in c.detail
    ok = LS.livetest_integrity_checks(_prereg(tmp_path, missed_weeks={"allowed": 0}), {}, [(2026, 10)], {})
    assert {x.name: x for x in ok}["missed weeks (miss rule)"].ok


# --------------------------------------------------------------------------- #
# D-116 (EV-4.7): retryable fetch, flagged markers, pre-build check, Friday cutoff, pinned snapshots
# --------------------------------------------------------------------------- #


def _settings(monkeypatch, tmp_path):
    from ffml.config import reset_settings_cache

    monkeypatch.setenv("FFML_REPORTS_DIR", str(tmp_path / "settings-reports"))
    monkeypatch.setenv("FFML_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()


def test_final_fetch_failure_exits_75_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """D-116 A4: a failed fetch or ls-remote check is retryable; no Verdict line, no report, no sealed file."""
    from ffml.config import reset_settings_cache

    _settings(monkeypatch, tmp_path)
    try:
        prereg_path = tmp_path / "prereg.md"
        _prereg(tmp_path)
        assert prereg_path.is_file()

        def boom(*a, **k):
            raise LS.FetchFailed("git fetch origin livetest-2026 failed: offline")

        monkeypatch.setattr(LS, "fetch_branch_ref", boom)
        rc = LS.main(["--final", "--prereg", str(prereg_path)])
        out = capsys.readouterr()
        assert rc == LS.EXIT_FETCH_RETRY == 75
        assert "Verdict:" not in out.out + out.err and "retry later" in out.err
        assert not (tmp_path / "settings-reports").exists() and not (tmp_path / "data").exists()
    finally:
        reset_settings_cache()


def test_fetch_branch_ref_ls_remote_failure_is_retryable(tmp_path, monkeypatch):
    orig = LS._run_git

    def fake(root, *args):
        if args and args[0] == "fetch":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args and args[0] == "ls-remote":
            return subprocess.CompletedProcess(args, 128, "", "fatal: unable to access")
        if args and args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, "a" * 40 + "\n", "")
        return orig(root, *args)

    monkeypatch.setattr(LS, "_run_git", fake)
    with pytest.raises(LS.FetchFailed, match="does not equal the remote head"):
        LS.fetch_branch_ref(tmp_path)


def test_marker_commit_time_from_the_branch(tmp_path):
    """D-116 A5: the GitHub commit time is the committer time of the branch commit that added the marker."""
    repo = tmp_path / "r"
    repo.mkdir()
    _g(repo, "init", "-q", "-b", "livetest-2026")
    _g(repo, "config", "user.email", "t@example.com")
    _g(repo, "config", "user.name", "t")
    (repo / "2026w11").mkdir()
    (repo / "2026w11" / LS.MISSED_NAME).write_text("{}", encoding="utf-8")
    _g(repo, "add", ".")
    env_date = "2026-11-21T09:30:00+00:00"
    subprocess.run(["git", "commit", "-q", "-m", "missed"], cwd=repo, check=True, capture_output=True,
                   env={**__import__("os").environ, "GIT_COMMITTER_DATE": env_date, "GIT_AUTHOR_DATE": env_date})
    src = LS.BranchSource(None, repo_root=repo, ref="livetest-2026")
    sha, when = src.added_by(f"2026w11/{LS.MISSED_NAME}")
    assert sha == _g(repo, "rev-parse", "HEAD") and LS.parse_time(when) == LS.parse_time(env_date)
    assert LS.BranchSource(tmp_path / "plain").added_by("x") is None


def _prereg_text(commit: str) -> str:
    data = {"commit": commit, "frozen_paths": ["ffml/models/"],
            "allowed_paths": {"scripts/": "runner", "docs/": "documentation"}}
    return "# prereg\n\n```json\n" + json.dumps(data, indent=1) + "\n```\n"


def test_prebuild_check_logged_before_first_use_and_frozen_paths(tmp_path, capsys):
    """D-116 B1 / A1: from..to logged at --to (append-only commits exempt), nothing on a frozen path."""
    repo, commit, append_log = _repo(tmp_path)
    x = commit({"ffml/models/a.py": "X\n", LS.CHANGELOG_DOC: CHANGELOG_TEMPLATE, LS.PREREG_DOC: _prereg_text("<frozen>")},
               "X")
    f = commit({LS.PREREG_DOC: _prereg_text(x)}, "F: fill the frozen commit")
    l1 = append_log(_log_row(f, LS.PREREG_DOC), "L1: log F")
    # the pinned worktree at X: its own copy has the placeholder, so X comes from main
    res = LS.prebuild_check("", x, repo)
    assert res["ok"] and res["x"] == x and res["commits"] == 0 and "from main" in res["detail"]
    res = LS.prebuild_check(None, l1, repo)
    assert res["ok"] and res["commits"] == 2  # F logged at L1; L1 only appends to the log
    a = commit({"scripts/x.py": "a\n"}, "A: runner change, not logged")
    res = LS.prebuild_check(l1, a, repo)
    assert not res["ok"] and res["uncovered"] == [a] and res["frozen"] == []
    l2 = append_log(_log_row(a, "scripts/x.py"), "L2: log A")
    assert LS.prebuild_check(l1, l2, repo)["ok"]
    b = commit({"ffml/models/b.py": "b\n"}, "B: frozen path")
    l3 = append_log(_log_row(b, "ffml/models/b.py"), "L3: log B")
    res = LS.prebuild_check(l2, l3, repo)
    assert not res["ok"] and res["uncovered"] == [] and res["frozen"] == [{"commit": b, "paths": ["ffml/models/b.py"]}]
    # an unlisted path is frozen too (path policy)
    c = commit({"pyproject.toml": "c\n"}, "C: unlisted path")
    res = LS.prebuild_check(l3, c, repo)
    assert {h["commit"] for h in res["frozen"]} == {c} and c in res["uncovered"]
    # bad input: blocked with a reason
    assert not LS.prebuild_check(l3, "f" * 40, repo)["ok"]
    assert "not an ancestor" in LS.prebuild_check(c, l3, repo)["detail"]
    # the CLI prints the JSON and exits 5 when blocked, 0 when ok
    assert LS.main(["--prebuild-check", "--from", l1, "--to", a, "--repo", str(repo)]) == LS.EXIT_BLOCKED == 5
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["ok"] is False and printed["uncovered"] == [a] and set(printed) >= {"ok", "uncovered", "frozen", "detail"}
    assert LS.main(["--prebuild-check", "--from", "", "--to", l2, "--repo", str(repo)]) == LS.EXIT_OK


def test_data_cutoff_rule():
    def dg(*kicks):
        return pl.DataFrame({"season": [2026] * len(kicks), "week": [10] * len(kicks), "game_type": ["REG"] * len(kicks),
                             "kickoff_utc": list(kicks)}, schema_overrides={"kickoff_utc": pl.Datetime("us", "UTC")})

    mnf = datetime(2026, 11, 17, 1, 15, tzinfo=UTC)  # Monday night (US) = Tuesday 01:15 UTC
    assert LS.data_cutoff(dg(KICKOFF, mnf), 2026, 10) == datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    # +72 h lands after Friday noon: the next Friday
    assert LS.data_cutoff(dg(KICKOFF, datetime(2026, 11, 18, 0, 30, tzinfo=UTC)), 2026, 10) == \
        datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
    # exactly 72 h before Friday noon still uses that Friday
    assert LS.data_cutoff(dg(datetime(2026, 11, 17, 12, 0, tzinfo=UTC)), 2026, 10) == \
        datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert LS.data_cutoff(dg(mnf), 2026, 11) is None


def _write_processed(tmp_path, season, week, *, unfinished=False):
    """A real processed store (snapshots written now) with one past week, for the CLI's pinned path."""
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(tmp_path / "data" / "processed")
    kick = datetime(season, 10, 5, 17, 0, tzinfo=UTC)
    games = pl.DataFrame({"season": [season] * 2, "week": [week] * 2, "game_type": ["REG"] * 2,
                          "game_id": [f"{season}_{week}_a", f"{season}_{week}_b"],
                          "kickoff_utc": [kick, kick + timedelta(hours=3)],
                          "status": ["final", "scheduled" if unfinished else "final"]},
                         schema_overrides={"season": pl.Int32, "week": pl.Int32, "kickoff_utc": pl.Datetime("us", "UTC")})
    ids = {"processed/dim_game": store.write_snapshot("dim_game", {season: games})}
    for ds in ("fact_player_game", "fact_dst_game"):
        ids[f"processed/{ds}"] = store.write_snapshot(ds, {season: pl.DataFrame({"season": [season], "x": [1]})})
    return ids


def test_week_exit_4_before_cutoff_without_snapshot_or_not_final(tmp_path, monkeypatch, capsys):
    from ffml.config import reset_settings_cache

    _settings(monkeypatch, tmp_path)
    try:
        season, week = 2024, 5
        ids = _write_processed(tmp_path, season, week, unfinished=True)
        cutoff = LS.data_cutoff(LS.load_dim_game([season]), season, week)
        assert cutoff is not None and cutoff < LS.snapshot_time(ids["processed/dim_game"])
        # before the cutoff
        monkeypatch.setattr(LS, "utcnow", lambda: cutoff - timedelta(minutes=1))
        assert LS.main(["--week", f"{season}w{week:02d}", "--prereg", str(tmp_path / "none.md")]) == LS.EXIT_RETRY_LATER
        assert "retry later: the data cutoff" in capsys.readouterr().err
        # after the cutoff, the pinned dim_game has a game not final
        monkeypatch.setattr(LS, "utcnow", lambda: cutoff + timedelta(hours=1))
        assert LS.main(["--week", f"{season}w{week:02d}"]) == LS.EXIT_RETRY_LATER
        assert "not final in the pinned dim_game (1 of 2 games)" in capsys.readouterr().err
        # --data-cutoff prints the runner's JSON; a week not in dim_game yet is "not yet" (4)
        assert LS.main(["--data-cutoff", f"{season}w{week:02d}"]) == LS.EXIT_OK
        printed = json.loads(capsys.readouterr().out.strip())
        assert printed["data_cutoff_utc"] == LS.iso(cutoff) and printed["week"] == f"{season}w{week:02d}"
        assert printed["last_kickoff_utc"] and "Friday" in printed["rule"]
        assert LS.main(["--data-cutoff", f"{season}w{week + 1:02d}"]) == LS.EXIT_RETRY_LATER
    finally:
        reset_settings_cache()


def test_week_exit_4_when_no_snapshot_at_or_after_the_cutoff(tmp_path, monkeypatch, capsys):
    from ffml.config import reset_settings_cache

    _settings(monkeypatch, tmp_path)
    try:
        _write_processed(tmp_path, 2024, 5)
        cutoff = LS.data_cutoff(LS.load_dim_game([2024]), 2024, 5)
        # pretend every snapshot predates the cutoff: no "first snapshot at or after" exists yet
        monkeypatch.setattr(LS, "snapshot_time", lambda sid: cutoff - timedelta(days=1))
        monkeypatch.setattr(LS, "utcnow", lambda: cutoff + timedelta(hours=1))
        assert LS.main(["--week", "2024w05"]) == LS.EXIT_RETRY_LATER
        assert "no snapshot at or after the data cutoff" in capsys.readouterr().err
    finally:
        reset_settings_cache()


def test_choose_pins_first_at_or_after_cutoff_and_rescore_reuses_recorded(tmp_path):
    root = tmp_path / "processed"
    for ds, ids in (("dim_game", ["20261120T110000Z", "20261120T130000Z", "20261121T090000Z"]),
                    ("fact_player_game", ["20261120T120000Z", "20261122T080000Z"]),
                    ("fact_dst_game", ["20261120T130500Z"])):
        for sid in ids:
            (root / ds / f"snapshot={sid}").mkdir(parents=True)
    roots = {"processed": root, "restricted": root / "restricted"}
    cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    pins = LS.choose_pins(cutoff, roots=roots)
    assert pins == {"processed/dim_game": "20261120T130000Z", "processed/fact_player_game": "20261120T120000Z",
                    "processed/fact_dst_game": "20261120T130500Z"}  # the restricted tier is absent: left out
    # a stat correction after the cutoff writes a newer snapshot; a rescore keeps the recorded pins
    (root / "dim_game" / "snapshot=20261121T120000Z").mkdir()
    assert LS.choose_pins(cutoff, roots=roots, recorded=pins) == pins
    (root / "restricted" / "fact_ecr_weekly").mkdir(parents=True)
    with pytest.raises(LS.RetryLater, match="restricted/fact_ecr_weekly"):
        LS.choose_pins(cutoff, roots=roots)


def test_pinned_store_reads_only_pinned_snapshots(tmp_path):
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(tmp_path / "processed")
    first = store.write_snapshot("dim_game", {2024: pl.DataFrame({"season": [2024], "v": [1]})})
    import time as _t

    _t.sleep(1.1)
    store.write_snapshot("dim_game", {2024: pl.DataFrame({"season": [2024], "v": [2]})})  # a later correction
    stores = LS.pinned_stores({"processed/dim_game": first}, roots={"processed": tmp_path / "processed"})
    assert stores["processed"].read("dim_game")["v"].to_list() == [1] and stores["restricted"] is None
    with pytest.raises(LS.ScoreRefused, match="no pinned snapshot"):
        stores["processed"].read("fact_player_game")


def test_final_uses_only_sealed_parts_after_a_correction(tmp_path, monkeypatch):
    """D-116 A3: corrections after the cutoff never enter; the final reads no actuals and no fresh consensus."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _g(repo, "init", "-q")
    _g(repo, "config", "user.email", "t@example.com")
    _g(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _g(repo, "add", "a.txt")
    _g(repo, "commit", "-q", "-m", "X")
    x = _g(repo, "rev-parse", "HEAD")
    prereg = _prereg(tmp_path, commit=x)
    dg = dim_game(2026, [10, 11])
    for w, kick in ((10, KICKOFF), (11, KICKOFF + timedelta(days=7))):
        _score(tmp_path, 2026, w, make_week(tmp_path, 2026, w, kickoff=kick, code_commit=x), dg, prereg=prereg,
               repo_root=repo)
        _push_seal(tmp_path, f"2026w{w}")
    kw = dict(source=LS.BranchSource(tmp_path / "branch"), prereg=prereg, root=tmp_path / "scoring",
              reports_dir=tmp_path / "reports", n_boot=30, repo_root=repo, sensitivity=False)
    first, _ = LS.final_evaluation(dim_game=dg, **kw)
    lines1 = json.loads((tmp_path / "scoring" / LS.SEALED_DIR / "final_lines.json").read_text(encoding="utf-8"))
    rec = json.loads(LS.record_path(tmp_path / "scoring", 2026, 10).read_text(encoding="utf-8"))
    assert first in ("Verdict: APPROVED", "Verdict: NOT APPROVED") and len(rec["outcome_rows_sha256"]) == 64
    # a stat correction after the cutoff: every outcome and consensus source now gives other numbers (or fails)
    def corrected(*a, **k):
        raise AssertionError("the final read fresh data")

    for name in ("harness_outcomes", "load_actuals", "consensus_rows", "_all_consensus", "ensure_comparators"):
        monkeypatch.setattr(LS, name, corrected)
    import ffml.scoring.actuals as actuals_mod

    monkeypatch.setattr(actuals_mod, "actual_statlines", corrected)
    second, _ = LS.final_evaluation(dim_game=dg, **kw)
    lines2 = json.loads((tmp_path / "scoring" / LS.SEALED_DIR / "final_lines.json").read_text(encoding="utf-8"))
    assert second == first and lines2["binding"] == lines1["binding"]
    # and a rescore with corrected outcomes would seal a different outcomes hash, which the record makes visible
    shifted = LS.outcomes_sha256(pl.read_parquet(LS.part_path(tmp_path / "scoring", 2026, 10))
                                 .with_columns(pl.col("actual") + 0.1))
    assert shifted != rec["outcome_rows_sha256"]


def test_comparator_rows_take_the_chain_outcomes(tmp_path):
    week = fake_outcomes(_rows(2026, 10, "chain_v2+cal", 1), 2026)
    base = _baselines(2026, 10).with_columns(pl.col("actual") + 5.0)  # stale comparator outcomes
    out = LS.with_outcomes_of(base, week)
    j = out.join(week.select("entity_id", "season", "week", "scoring", pl.col("actual").alias("a2")),
                 on=["entity_id", "season", "week", "scoring"])
    assert j.height == out.height and (j["actual"] == j["a2"]).all()


# --------------------------------------------------------------------------- #
# D-121: a game that never becomes final
# --------------------------------------------------------------------------- #

GAME2 = "2026_10_T04_T05"  # game index 2 of week 10 in the fixtures (teams T04 and T05)


def _sid(t: datetime) -> str:
    return t.strftime("%Y%m%dT%H%M%SZ")


def _snapshot(root, ds: str, sid: str, frames: dict) -> str:
    """A snapshot with a chosen id (the store names snapshots by their write time); LATEST follows it."""
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(root)
    for key, frame in frames.items():
        p = store.partition_path(ds, sid, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(p)
    (root / ds / "LATEST").write_text(sid, encoding="utf-8")
    return sid


def _refresh(root, t: datetime, dg: pl.DataFrame) -> None:
    """One refresh at ``t``: dim_game first, then the other processed tables a minute later (as run_process does)."""
    _snapshot(root, "dim_game", _sid(t), {2026: dg})
    for ds in ("fact_player_game", "fact_dst_game"):
        _snapshot(root, ds, _sid(t + timedelta(minutes=1)), {2026: pl.DataFrame({"season": [2026], "x": [1]})})


def _with_teams(dg: pl.DataFrame) -> pl.DataFrame:
    parts = pl.col("game_id").str.split("_")
    return dg.with_columns(parts.list.get(2).alias("home_team"), parts.list.get(3).alias("away_team"))


def _d121_world(tmp_path):
    root = tmp_path / "processed"
    roots = {"processed": root, "restricted": root / "restricted"}
    dg_open = _with_teams(dim_game(2026, [10], unfinished={10: 2}))
    dg_done = _with_teams(dim_game(2026, [10]))
    cutoff = LS.data_cutoff(dg_open, 2026, 10)
    return root, roots, dg_open, dg_done, cutoff, LS.exclusion_after(cutoff)


def _score_planned(tmp_path, plan, prereg, consensus=None):
    return LS.score_week(2026, 10, source=LS.BranchSource(tmp_path / "branch"), dim_game=plan.dim_game,
                         push_record=make_week(tmp_path, 2026, 10), roots=[tmp_path], outcomes=fake_outcomes,
                         baselines=_baselines(2026, 10),
                         consensus=_consensus(2026, 10) if consensus is None else consensus, prereg=prereg,
                         root=tmp_path / "scoring", reports_dir=tmp_path / "reports", n_boot=40,
                         data_cutoff=plan.cutoff, data_snapshots=plan.pins, excluded_games=plan.excluded,
                         pin_from=plan.pin_from)


def test_d121_a_game_never_final_waits_14_days_then_is_excluded_and_listed(tmp_path):
    root, roots, dg_open, _, cutoff, after = _d121_world(tmp_path)
    assert after - cutoff == timedelta(days=14) and cutoff == datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    for t in (cutoff + timedelta(hours=1), cutoff + timedelta(days=7), after + timedelta(hours=1)):
        _refresh(root, t, dg_open)

    def plan(now):
        return LS.plan_week(2026, 10, dg_open, now=now, roots=roots)

    with pytest.raises(LS.RetryLater, match="data cutoff"):
        plan(cutoff - timedelta(minutes=1))
    for now in (cutoff + timedelta(hours=2), after - timedelta(minutes=1)):  # exit 4 until the cutoff plus 14 days
        with pytest.raises(LS.RetryLater, match=r"not final in the pinned dim_game \(3 of 4 games\).*D-121"):
            plan(now)
    p = plan(after + timedelta(hours=2))
    assert p.pin_from == after and p.excluded == (GAME2,)
    assert p.pins == {"processed/dim_game": _sid(after + timedelta(hours=1)),
                      "processed/fact_player_game": _sid(after + timedelta(hours=1, minutes=1)),
                      "processed/fact_dst_game": _sid(after + timedelta(hours=1, minutes=1))}

    prereg = _prereg(tmp_path, window=[[2026, 10]])
    rec, out = _score_planned(tmp_path, p, prereg)
    assert next(c for c in rec.checks if c.name == "week final").ok
    scoring = tmp_path / "scoring"
    sealed = pl.read_parquet(LS.part_path(scoring, 2026, 10))
    assert sealed.height and sealed.filter(pl.col("game_id") == GAME2).height == 0
    assert sealed.filter(pl.col("team").is_in(["T04", "T05"])).height == 0
    assert {"chain_v2", "chain_v2+cal", "rolling_avg_4+cal"} <= set(sealed["model"].unique().to_list())
    cons = pl.read_parquet(LS.consensus_part_path(scoring, 2026, 10))
    assert cons.height and cons.filter(pl.col("team").is_in(["T04", "T05"])).height == 0
    js = json.loads(LS.record_path(scoring, 2026, 10).read_text(encoding="utf-8"))
    public = json.loads(LS.week_status_path(scoring, 2026, 10).read_text(encoding="utf-8"))
    for doc in (js, public):
        assert doc["excluded_games"] == [{"game_id": GAME2, "teams": ["T04", "T05"],
                                          "status_in_pinned_dim_game": "scheduled", "reason": LS.EXCLUSION_REASON}]
        assert doc["pin_from_utc"] == LS.iso(after) and doc["exclusion_after_utc"] == LS.iso(after)
        assert doc["data_snapshots"] == p.pins
    text = out.read_text(encoding="utf-8")
    assert GAME2 in text and "D-121" in text
    _assert_no_metrics(text)

    # the final waits for the week until its cutoff plus 14 days, then lists the excluded game
    _push_seal(tmp_path, "2026w10")
    kw = dict(source=LS.BranchSource(tmp_path / "branch"), prereg=prereg, root=scoring,
              reports_dir=tmp_path / "reports", n_boot=20, sensitivity=False)
    with pytest.raises(LS.ScoreRefused, match="refused until 2026w10 is final"):
        LS.final_evaluation(dim_game=dg_open, now=after - timedelta(hours=1), **kw)
    verdict, fout = LS.final_evaluation(dim_game=dg_open, now=after + timedelta(days=1), **kw)
    ftext = fout.read_text(encoding="utf-8")
    assert "## Games excluded (D-121)" in ftext and GAME2 in ftext and ftext.count("Verdict:") == 1
    lines = json.loads((scoring / LS.SEALED_DIR / "final_lines.json").read_text(encoding="utf-8"))
    assert [g["game_id"] for g in lines["excluded_games"]] == [GAME2] and lines["excluded_games"][0]["week"] == "2026w10"
    assert any(c["name"] == "2026w10 games excluded (D-121)" and c["ok"] for c in lines["checks"])


def test_d121_a_game_final_before_the_14_days_is_scored_normally(tmp_path):
    root, roots, dg_open, dg_done, cutoff, after = _d121_world(tmp_path)
    t_final = cutoff + timedelta(days=3)
    _refresh(root, cutoff + timedelta(hours=1), dg_open)
    _refresh(root, t_final, dg_done)
    _refresh(root, after + timedelta(hours=1), dg_done)
    with pytest.raises(LS.RetryLater, match="not final in the pinned dim_game"):
        LS.plan_week(2026, 10, dg_open, now=cutoff + timedelta(hours=2), roots=roots)
    for now in (t_final + timedelta(hours=1), after + timedelta(hours=2)):  # the same pins whenever it is scored
        p = LS.plan_week(2026, 10, dg_done, now=now, roots=roots)
        assert p.pin_from == t_final and p.excluded == ()
        assert p.pins == {"processed/dim_game": _sid(t_final),
                          "processed/fact_player_game": _sid(t_final + timedelta(minutes=1)),
                          "processed/fact_dst_game": _sid(t_final + timedelta(minutes=1))}
    rec, _ = _score_planned(tmp_path, p, _prereg(tmp_path, window=[[2026, 10]]))
    js = json.loads(LS.record_path(tmp_path / "scoring", 2026, 10).read_text(encoding="utf-8"))
    assert js["excluded_games"] == [] and js["pin_from_utc"] == LS.iso(t_final)
    sealed = pl.read_parquet(LS.part_path(tmp_path / "scoring", 2026, 10))
    assert sealed.filter(pl.col("game_id") == GAME2).height > 0


def test_d121_a_game_moved_to_a_later_week_is_not_excluded(tmp_path):
    root, roots, dg_open, _, cutoff, after = _d121_world(tmp_path)
    moved = dg_open.with_columns(
        pl.when(pl.col("game_id") == GAME2).then(pl.lit(12)).otherwise(pl.col("week")).alias("week"),
        pl.when(pl.col("game_id") == GAME2).then(pl.col("kickoff_utc") + pl.duration(days=14))
        .otherwise(pl.col("kickoff_utc")).alias("kickoff_utc"))
    assert LS.data_cutoff(moved, 2026, 10) == cutoff
    t_move = cutoff + timedelta(days=2)
    _refresh(root, cutoff + timedelta(hours=1), dg_open)  # still in week 10, not final
    _refresh(root, t_move, moved)  # rescheduled into week 12: it leaves week 10
    p = LS.plan_week(2026, 10, moved, now=after + timedelta(days=1), roots=roots)
    assert p.excluded == () and p.pin_from == t_move and p.status.games == 3 and p.status.is_final
    # moved before the cutoff: pinned at the cutoff, exactly as before D-121
    root2 = tmp_path / "processed2"
    _refresh(root2, cutoff + timedelta(hours=1), moved)
    p2 = LS.plan_week(2026, 10, moved, now=cutoff + timedelta(hours=2),
                      roots={"processed": root2, "restricted": root2 / "restricted"})
    assert p2.excluded == () and p2.pin_from == cutoff


def test_d121_rescore_reuses_the_pins_and_the_pin_point(tmp_path):
    root, roots, dg_open, dg_done, cutoff, after = _d121_world(tmp_path)
    _refresh(root, cutoff + timedelta(hours=1), dg_open)
    _refresh(root, after + timedelta(hours=1), dg_open)
    p = LS.plan_week(2026, 10, dg_open, now=after + timedelta(hours=2), roots=roots)
    _score_planned(tmp_path, p, _prereg(tmp_path, window=[[2026, 10]]))
    js = json.loads(LS.record_path(tmp_path / "scoring", 2026, 10).read_text(encoding="utf-8"))
    # later the game is declared final and a newer refresh exists; a rescore keeps the recorded pins
    _refresh(root, after + timedelta(days=3), dg_done)
    q = LS.plan_week(2026, 10, dg_done, now=after + timedelta(days=4), roots=roots, record=js)
    assert (q.pins, q.pin_from, q.excluded, q.cutoff) == (p.pins, p.pin_from, p.excluded, p.cutoff)
    wp = LS.choose_week_pins(2026, 10, cutoff, now=after + timedelta(days=4), roots=roots, recorded=js)
    assert wp == LS.WeekPins(p.pins, after)


def test_d121_an_all_final_week_is_pinned_exactly_as_before(tmp_path):
    root, roots, _, dg_done, cutoff, _ = _d121_world(tmp_path)
    for t in (cutoff - timedelta(hours=1), cutoff + timedelta(hours=1), cutoff + timedelta(days=2)):
        _refresh(root, t, dg_done)
    p = LS.plan_week(2026, 10, dg_done, now=cutoff + timedelta(days=3), roots=roots)
    assert p.pin_from == cutoff and p.excluded == () and p.pins == LS.choose_pins(cutoff, roots=roots)
    rows = _rows(2026, 10, "chain_v2", 1)
    assert LS.without_games(rows, p.excluded, (), 2026, 10) is rows


def test_d121_without_games_by_game_id_team_and_dst():
    rows = pl.concat([_rows(2026, 10, "m", 1), _rows(2026, 11, "m", 1)])
    rows = rows.with_columns(pl.when((pl.col("team") == "T04") & (pl.col("position") == "WR"))
                             .then(None).otherwise(pl.col("game_id")).alias("game_id"))  # rows without a game id
    out = LS.without_games(rows, [GAME2], ["T04", "T05"], 2026, 10)
    gone = rows.height - out.height
    assert gone == rows.filter((pl.col("week") == 10) & pl.col("team").is_in(["T04", "T05"])).height
    assert out.filter(pl.col("week") == 11).height == rows.filter(pl.col("week") == 11).height  # other weeks kept
    assert out.filter((pl.col("week") == 10) & (pl.col("entity_id") == "T05")).height == 0  # the DST entity
    bare = rows.drop("game_id", "team")
    with pytest.raises(LS.ScoreRefused, match="neither game_id nor team"):
        LS.without_games(bare, [GAME2], [], 2026, 10)
    # D-121 never removes a whole week
    st = LS.week_status(dim_game(2026, [10], unfinished={10: 0}).with_columns(pl.lit("scheduled").alias("status")),
                        2026, 10)
    with pytest.raises(LS.ScoreRefused, match="never a whole week"):
        LS.games_to_exclude(st, datetime(2026, 12, 5, tzinfo=UTC), datetime(2026, 11, 20, 12, tzinfo=UTC))


def test_d121_data_cutoff_prints_the_exclusion_point(tmp_path, monkeypatch, capsys):
    from ffml.config import reset_settings_cache

    _settings(monkeypatch, tmp_path)
    try:
        _write_processed(tmp_path, 2024, 5)
        cutoff = LS.data_cutoff(LS.load_dim_game([2024]), 2024, 5)
        assert LS.main(["--data-cutoff", "2024w05"]) == LS.EXIT_OK
        printed = json.loads(capsys.readouterr().out.strip())
        assert printed["data_cutoff_utc"] == LS.iso(cutoff)
        assert printed["exclusion_after_utc"] == LS.iso(cutoff + timedelta(days=14)) and "D-121" in printed["exclusion_rule"]
    finally:
        reset_settings_cache()
