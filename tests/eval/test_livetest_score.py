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
              rehearsal: bool = False, tamper: str | None = None):
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
        "code": {"commit": "0" * 40, "clean": True},
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
    record = {"season": season, "week": week, "commit_sha": "b" * 40, "commitment_sha256": LS.sha256_bytes(raw_json),
              "commitment_digest": payload["digest"]["value"], "external_timestamp": stamp.isoformat(),
              "external_timestamp_source": "GitHub repository activity API (server time of the push)",
              "github": {"push_activity": {"timestamp": stamp.isoformat(), "after": "b" * 40}}}
    return record


PREREG = {
    "commit": "<frozen commit>", "season": 2026, "weeks": [10, 11], "window": [[2026, 10], [2026, 11]],
    "rules": {"lines_sha256": H.lines_spec_sha256()}, "n_boot": 500, "seed": 0, "metric_seed": 0,
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
    with pytest.raises(LS.ScoreRefused, match="pushed before kickoff"):
        _score(tmp_path, 2026, 10, late, dg)
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


def test_weekly_report_is_information_only_and_parts_accumulate(tmp_path):
    dg = dim_game(2026, [10, 11])
    rec, out = _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg)
    text = out.read_text(encoding="utf-8")
    assert "Verdict:" not in text and "information only" in text
    assert rec.refusals == [] and rec.files_verified == 2
    names = {c.name: c.ok for c in rec.checks}
    assert names["digest"] and names["pushed before kickoff (GitHub timestamp)"] and names["pin: selection.json"]
    assert names["code commit"] is False  # the pre-registration has no frozen commit yet
    part = LS.part_path(tmp_path / "scoring", 2026, 10)
    assert part.is_file() and json.loads(LS.record_path(tmp_path / "scoring", 2026, 10).read_text())["part_sha256"]
    frame = pl.read_parquet(part)
    assert set(frame["model"].unique()) >= {"chain_v2", "chain_v2+cal", "rolling_avg_4+cal"}
    # week two sees week one: the window-so-far section lists both weeks
    _, out2 = _score(tmp_path, 2026, 11, make_week(tmp_path, 2026, 11, kickoff=KICKOFF + timedelta(days=7)), dg)
    text2 = out2.read_text(encoding="utf-8")
    assert "2026w10, 2026w11" in text2 and "Verdict:" not in text2 and "(2 of 18" in text2


def test_final_refused_until_complete_then_prints_one_verdict(tmp_path):
    prereg = _prereg(tmp_path)
    dg_open = dim_game(2026, [10, 11], unfinished={11: 0})
    src = LS.BranchSource(tmp_path / "branch")
    kw = dict(source=src, prereg=prereg, root=tmp_path / "scoring", reports_dir=tmp_path / "reports", n_boot=40,
              consensus=pl.concat([_consensus(2026, 10), _consensus(2026, 11)]))
    with pytest.raises(LS.ScoreRefused, match="refused until 2026w11 is final"):
        LS.final_evaluation(dim_game=dg_open, **kw)
    dg = dim_game(2026, [10, 11])
    with pytest.raises(LS.ScoreRefused, match="neither scored nor marked MISSED"):
        LS.final_evaluation(dim_game=dg, **kw)
    _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg, prereg=prereg)
    # week 11 missed: one miss is excluded and the verdict is computed (INVALID here: no frozen commit)
    (tmp_path / "branch" / "2026w11").mkdir(parents=True, exist_ok=True)
    (tmp_path / "branch" / "2026w11" / LS.MISSED_NAME).write_text('{"reason": "offline"}', encoding="utf-8")
    verdict, out = LS.final_evaluation(dim_game=dg, **kw)
    assert verdict.startswith("Verdict: INVALID") and "frozen commit" in verdict
    text = out.read_text(encoding="utf-8")
    assert text.count("Verdict:") == 1
    # a dry run can never produce the registry's exact line
    v2, _ = LS.final_evaluation(dim_game=dg, dry_run=True, **kw)
    assert v2.startswith("Dry-run verdict") and "Verdict: APPROVED" not in v2


def test_final_second_miss_is_invalid_and_checks_cover_each_week(tmp_path):
    prereg = _prereg(tmp_path, window=[[2026, 10], [2026, 11], [2026, 12]])
    dg = dim_game(2026, [10, 11, 12])
    for w in (11, 12):
        (tmp_path / "branch" / f"2026w{w}").mkdir(parents=True, exist_ok=True)
        (tmp_path / "branch" / f"2026w{w}" / LS.MISSED_NAME).write_text("{}", encoding="utf-8")
    _score(tmp_path, 2026, 10, make_week(tmp_path, 2026, 10), dg, prereg=prereg)
    verdict, _ = LS.final_evaluation(source=LS.BranchSource(tmp_path / "branch"), dim_game=dg, prereg=prereg,
                                     root=tmp_path / "scoring", reports_dir=tmp_path / "reports", n_boot=40)
    assert "INVALID" in verdict and "missed weeks (D-106)" in verdict
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
