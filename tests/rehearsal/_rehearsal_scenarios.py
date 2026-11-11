"""Scenario pipelines of the live-test final rehearsal (run by test_livetest_final_rehearsal.py).

Each pipeline builds one synthetic world from scratch (clone at X, fake remote,
synthetic stores on a simulated calendar), runs the season week by week through
the runner and the scorer, then the final evaluation through the scorer's CLI,
and returns a JSON evidence record. Pipelines are independent and run in a
thread pool (at most ``FFML_REHEARSAL_WORKERS`` at a time); every heavy step is
a subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import _rehearsal_world as W

#: the real 5,000-draw final (about 1.5 h of one core more) runs only with FFML_REHEARSAL_5000=1
QUICK = os.environ.get("FFML_REHEARSAL_5000") != "1"
FINAL_TIMEOUT = 4 * 3600
H = timedelta(hours=1)
M = timedelta(minutes=1)
D = timedelta(days=1)


def _compact(value: Any) -> Any:
    """A final without the report text (the world's evidence directory keeps the report)."""
    if isinstance(value, dict) and "proc" in value:
        out = {k: v for k, v in value.items() if k not in ("final_md", "proc", "final_lines")}
        out["returncode"] = value["proc"]["returncode"]
        out["stderr_tail"] = value["proc"]["stderr"][-1200:]
        fl = value.get("final_lines") or {}
        if fl.get("binding"):
            out["binding_lines"] = {ln["id"]: ln["passed"] for ln in fl["binding"]["lines"]}
        if fl.get("excluded_games"):
            out["excluded_games"] = fl["excluded_games"]
        return out
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items()}
    return value


def record(world: str, key: str, value: Any) -> Any:
    """Stream one scenario result as soon as it exists (see ``W.stream``); returns it unchanged."""
    W.stream(world, key, _compact(value))
    return value


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text)


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


# --------------------------------------------------------------------------- #
# The season as events in simulated time
# --------------------------------------------------------------------------- #

#: the pre-build scenario's weeks (world P)
PREBUILD_FIXED, PREBUILD_MISSED, PREBUILD_FROZEN = (2026, 11), (2026, 13), (2026, 16)


def prebuild_events() -> tuple[list[tuple[datetime, int, dict[str, Any]]], set[tuple[int, int]]]:
    """World P: an unlogged change fixed before the deadline, one left unfixed, and a frozen-path change."""
    ev: list[tuple[datetime, int, dict[str, Any]]] = []
    s, w = PREBUILD_FIXED
    a = W.attempt_time(s, w)
    ev += [(a - 2 * H, 0, {"op": "git-commit", "label": "U", "path": "ffml/data/rehearsal_u.py",
                           "message": "Data: synthetic fix on an allowed path, not logged yet", "at": iso(a - 2 * H)}),
           (a, 1, {"op": "push", "season": s, "week": w, "attempts": [iso(a), iso(a + 30 * M)], "required": False,
                   "tag": "blocked_then_fixed"}),
           (a + 50 * M, 0, {"op": "git-log", "label": "U", "paths": "ffml/data/rehearsal_u.py",
                            "reason": "synthetic data fix", "first_week": W.week_key(s, w), "at": iso(a + 50 * M)}),
           (a + 60 * M, 1, {"op": "push", "season": s, "week": w, "attempts": [iso(a + 60 * M)], "tag": "after_fix"})]
    s, w = PREBUILD_MISSED
    a, k = W.attempt_time(s, w), W.first_kickoff(s, w)
    ev += [(a - 2 * H, 0, {"op": "git-commit", "label": "V", "path": "scripts/rehearsal_v.py",
                           "message": "Runner: synthetic change, never logged before the deadline", "at": iso(a - 2 * H)}),
           (a, 1, {"op": "push", "season": s, "week": w, "attempts": [iso(a), iso(a + 12 * H), iso(k - 35 * M)],
                   "required": False, "tag": "blocked_past_deadline"}),
           (k + H, 0, {"op": "git-log", "label": "V", "paths": "scripts/rehearsal_v.py", "reason": "runner change",
                       "first_week": W.week_key(2026, 14), "at": iso(k + H)})]
    s, w = PREBUILD_FROZEN
    a = W.attempt_time(s, w)
    ev += [(a - 2 * H, 0, {"op": "git-commit", "label": "Z", "path": "ffml/models/rehearsal_z.py",
                           "message": "Models: synthetic change on a frozen path", "at": iso(a - 2 * H)}),
           (a - 110 * M, 0, {"op": "git-log", "label": "Z", "paths": "ffml/models/rehearsal_z.py",
                             "reason": "frozen-path change (logged)", "at": iso(a - 110 * M)}),
           (a, 1, {"op": "push", "season": s, "week": w, "attempts": [iso(a)], "required": False,
                   "tag": "blocked_frozen"}),
           (a + 20 * M, 0, {"op": "git-reset", "to": "Z:parent", "at": iso(a + 20 * M)}),
           (a + 30 * M, 1, {"op": "push", "season": s, "week": w, "attempts": [iso(a + 30 * M)],
                            "tag": "after_reset"})]
    return ev, {PREBUILD_FIXED, PREBUILD_MISSED, PREBUILD_FROZEN}


def correction_events() -> list[tuple[datetime, int, dict[str, Any]]]:
    """World E: ``--week 2027w01`` before its cutoff, and before any snapshot at or after it (both exit 4);
    ``--data-cutoff`` for a 2027 week before the 2027 schedule is in dim_game (exit 4) and for a scheduled week."""
    c = W.data_cutoff(2027, 1)
    early = _t("2027-02-01T12:00:00+00:00")
    return [(early, 0, {"op": "cli", "args": ["--data-cutoff", "2027w01"], "at": iso(early), "required": False,
                        "tag": "data_cutoff_unscheduled", "expect_exit": 4}),
            (early, 1, {"op": "cli", "args": ["--data-cutoff", "2026w18"], "at": iso(early), "required": False,
                        "tag": "data_cutoff_scheduled", "expect_exit": 0}),
            (c - H, 0, {"op": "cli", "args": ["--week", "2027w01"], "at": iso(c - H), "required": False,
                        "tag": "before_cutoff", "expect_exit": 4}),
            (c + 10 * M, 0, {"op": "cli", "args": ["--week", "2027w01"], "at": iso(c + 10 * M), "required": False,
                             "tag": "no_snapshot_yet", "expect_exit": 4})]


#: world G (D-121): a game that is never final, and a game that becomes final 4 days after its week's cutoff
UNFINAL_NEVER, UNFINAL_LATE = (2026, 14, 3), (2027, 2, 2)


def unfinal_spec_games() -> dict[str, str]:
    s, w, g = UNFINAL_LATE
    return {W.game_id(*UNFINAL_NEVER): "never", W.game_id(s, w, g): iso(W.data_cutoff(s, w) + 4 * D)}


def unfinal_events() -> tuple[list[tuple[datetime, int, dict[str, Any]]], set[tuple[int, int]]]:
    """World G: 2026w14 waits, is retried and is scored without its game after 14 days, while the runner
    scores and seals 2026w15 and 2026w16 (later weeks are not held); 2027w02 waits until its late game is final."""
    ev: list[tuple[datetime, int, dict[str, Any]]] = []
    s, w, _ = UNFINAL_NEVER
    c = W.data_cutoff(s, w)
    c15 = W.data_cutoff(s, w + 1)
    ev += [(c + H, 0, {"op": "score-hook", "season": s, "week": w, "at": iso(c + H), "required": False,
                       "tag": "never_wait_friday"}),
           (c + 7 * D + H, 2, {"op": "cli", "args": ["--week", W.week_key(s, w)], "at": iso(c + 7 * D + H),
                               "required": False, "tag": "never_wait_cli", "expect_exit": 4}),
           (c15 + H, 2, {"op": "score-week", "at": iso(c15 + H), "required": False, "tag": "runner_while_waiting"}),
           (c + 14 * D + 45 * M, 2, {"op": "score-week", "at": iso(c + 14 * D + 45 * M), "required": False,
                                     "tag": "runner_after_14_days"})]
    s, w, _ = UNFINAL_LATE
    c = W.data_cutoff(s, w)
    ev += [(c + H, 0, {"op": "score-hook", "season": s, "week": w, "at": iso(c + H), "required": False,
                       "tag": "late_wait_friday"}),
           (c + 7 * D + H, 2, {"op": "score-hook", "season": s, "week": w, "at": iso(c + 7 * D + H),
                               "tag": "late_scored"}),
           (c + 7 * D + 90 * M, 2, {"op": "seal", "season": s, "week": w, "at": iso(c + 7 * D + 90 * M)})]
    return ev, {(2026, 14), (2026, 15), (2026, 16), (2027, 2)}


NAMED_WEEKS: tuple[tuple[int, int], ...] = (
    (2026, 11), (2026, 12), (2026, 13), (2026, 14), (2026, 15), (2026, 16), (2026, 17), (2027, 1), (2027, 2),
    (2027, 3), (2026, 18))
assert set(NAMED_WEEKS) <= set(W.WINDOW), sorted(set(NAMED_WEEKS) - set(W.WINDOW))


def _span(season: int) -> str:
    weeks = W.SEASON_WINDOW[season]
    assert list(weeks) == list(range(weeks[0], weeks[-1] + 1)), weeks
    return f"{weeks[0]}-{weeks[-1]}"


def weekly_plan(spec: W.WorldSpec) -> list[dict[str, Any]]:
    """Every step of the window in simulated-time order (a pre-build refresh, the runner's build and push,
    the Friday refresh after the data cutoff, weekly scoring, the seal)."""
    ev: list[tuple[datetime, int, dict[str, Any]]] = [
        (_t("2026-11-11T00:00:00+00:00"), 0, {"op": "init", "season": 2026, "weeks": _span(2026),
                                              "at": "2026-11-11T00:00:00+00:00"}),
        (_t("2027-09-01T00:00:00+00:00"), 0, {"op": "init", "season": 2027, "weeks": _span(2027), "force": True,
                                              "at": "2027-09-01T00:00:00+00:00"}),
    ]
    missed = set(spec.missed)
    custom: set[tuple[int, int]] = set()
    if spec.prebuild:
        extra, custom = prebuild_events()
        ev += extra
    if spec.corrections:
        ev += correction_events()
    custom_scoring: set[tuple[int, int]] = set()
    if spec.unfinal_games:
        extra_u, custom_scoring = unfinal_events()
        ev += extra_u
    no_commitment = missed | ({PREBUILD_MISSED} if spec.prebuild else set())
    for s, w in W.WINDOW:
        a = W.attempt_time(s, w)
        if (s, w) not in missed:
            ev.append((a - H, 0, {"op": "refresh", "at": iso(a - H), "kind": "prebuild", "week": W.week_key(s, w)}))
            if (s, w) not in custom:
                ev.append((a, 1, {"op": "push", "season": s, "week": w}))
        c = W.data_cutoff(s, w)
        ev.append((c + 30 * M, 0, {"op": "refresh", "at": iso(c + 30 * M), "kind": "friday",
                                   "week": W.week_key(s, w)}))
        if (s, w) not in no_commitment and (s, w) not in custom_scoring:
            if (s, w) in set(spec.cli_weeks):
                ev.append((c + H, 0, {"op": "score-cli", "season": s, "week": w, "at": iso(c + H)}))
            else:
                ev.append((c + H, 0, {"op": "score-hook", "season": s, "week": w, "at": iso(c + H), "n_boot": 5,
                                      "sens_n_boot": 5, "weekly_sensitivity": spec.weekly_sensitivity}))
            ev.append((c + 90 * M, 0, {"op": "seal", "season": s, "week": w, "at": iso(c + 90 * M)}))
    for season, weeks in W.SEASON_WINDOW.items():  # a season's last week has no later run to record its miss
        if (season, weeks[-1]) in missed:
            k = W.first_kickoff(season, weeks[-1]) + H
            ev.append((k, 0, {"op": "misses", "at": iso(k)}))
    ev.sort(key=lambda e: (e[0], e[1]))
    return [step for _, _, step in ev]


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def prepare(spec: W.WorldSpec, root: Path) -> dict[str, Any]:
    """Clone, probe, synthetic stores, the freeze commits, the whole season, then a logged post-freeze change."""
    world = W.World(spec, root)
    ev: dict[str, Any] = {"world": spec.name, "spec": json.loads(spec.to_json()), "root": str(root)}
    t0 = time.perf_counter()
    x = W.make_clone(world)
    ev["x"] = x
    p, built = W.driver(world, "build")
    assert p.returncode == 0, p.stderr[-3000:]
    ev["build"] = built
    p, probe = W.driver(world, "probe")
    assert p.returncode == 0, p.stderr[-3000:]
    ev["probe"] = probe
    # the freeze as D-120 does it, from Z: (rehearsal only: the synthetic stored-parts digest), the fill commit,
    # one append-only change-log commit Y; the builds then run at Y
    ev["base"] = W.git(world.clone, "rev-parse", "HEAD")
    ev["base_commits"] = W.git(world.clone, "rev-list", "--count", f"{x}..HEAD")
    W.write_rehearsal_digest(world.clone, built["stored_digest"])
    sd = W.commit_paths(world.clone, [W.PREREG_DOC], "Rehearsal only: synthetic stored-parts digest",
                        _t("2026-11-11T23:30:00+00:00"))
    W.fill_commit_field(world.clone, x)
    f = W.commit_paths(world.clone, [W.PREREG_DOC], "Freeze: pre-registration commit field (synthetic rehearsal)",
                       _t("2026-11-12T00:00:00+00:00"))
    ev["freeze"] = freeze_changelog(world, x, f)
    ev["commits"] = {"X": x, "S_rehearsal_digest": sd, "F_fill": f, "Y_log": ev["freeze"]["y"]}
    # Z may change allowed paths after X (ffml/data, scripts, docs); no frozen path may differ from X
    ev["diff_x_head"] = W.git(world.clone, "diff", "--name-only", x, "HEAD").splitlines()
    prereg = W.prereg_data((world.clone / W.PREREG_DOC).read_text(encoding="utf-8"))
    ev["diff_x_head_frozen_code"] = "\n".join(f for f in ev["diff_x_head"] if W.path_frozen(f, prereg))
    ev["window"] = [W.week_key(*k) for k in W.WINDOW]
    ev["prereg_window"] = [W.week_key(int(a), int(b)) for a, ws in prereg["window"]["weeks"].items() for b in ws]
    p, weekly = W.driver(world, "weekly", {"steps": weekly_plan(spec)}, timeout=8 * 3600)
    assert p.returncode == 0, p.stderr[-4000:]
    ev["weekly"] = weekly["steps"]
    ev["labels"] = weekly.get("labels", {})
    bad = [s for s in weekly["steps"] if s.get("exit") != 0 and s.get("required", True)]
    assert not bad, json.dumps(bad, indent=1, default=str)[-6000:]
    # a logged post-freeze change on an allowed path (the change log names it in a later, append-only commit)
    (world.clone / "scripts" / "rehearsal_logged_change.py").write_text(
        '"""Synthetic post-freeze change on an allowed path (QA rehearsal)."""\n', encoding="utf-8")
    a = W.commit_paths(world.clone, ["scripts/rehearsal_logged_change.py"], "Runner: synthetic logged change",
                       _t("2027-11-25T12:00:00+00:00"))
    W.append_changelog(world.clone, [W.changelog_row(a, "scripts/rehearsal_logged_change.py", "runner change",
                                                     first_week="none (after the window)", date="2027-11-25")])
    l2 = W.commit_paths(world.clone, [W.CHANGELOG_DOC], "Change log: entry for the runner change",
                        _t("2027-11-25T12:05:00+00:00"))
    ev["commits"].update(A_logged_change=a, L2_log_A=l2)
    ev["branch_files"] = sorted(W.git(world.clone, "ls-tree", "-r", "--name-only", "origin/livetest-2026").splitlines())
    ev["prepare_seconds"] = round(time.perf_counter() - t0, 1)
    return {"world": world, "ev": ev}


#: a simulated time after the window for finals that need one (world G: its excluded game is never final, so
#: the final must run after that week's cutoff plus 14 days, as the real final will)
FINAL_NOW = datetime(2027, 12, 2, 12, 0, tzinfo=UTC)


def freeze_changelog(world: W.World, x: str, fill: str) -> dict[str, Any]:
    """D-120 steps b to d: rows for every commit in X..fill in one append-only commit Y (the helper for the
    commits outside the pinned build, hand-written rows for the ones it refuses), then the real
    ``--prebuild-check`` at Y (exit 0) and at a Y without one row (exit 5)."""
    import sys

    clone = world.clone
    helper = [sys.executable, str(clone / "scripts" / "livetest_changelog.py"), "--from", x, "--repo", str(clone),
              "--json"]
    out: dict[str, Any] = {}
    p = W.run(world, [*helper, "--to", fill])
    assert p.returncode in (0, 1), p.stderr[-2000:]
    listed = json.loads(p.stdout)["missing"]
    out["listed"] = [{k: m[k] for k in ("sha", "status", "subject", "paths", "why")} for m in listed]
    p = W.run(world, [*helper, "--to", fill, "--append"])
    assert p.returncode in (0, 1), p.stderr[-2000:]
    out["appended_by_helper"] = json.loads(p.stdout)["appended"]
    hand = [m for m in listed if m["status"] == "needs_hand_row"]
    out["hand_rows"] = [m["sha"] for m in hand]
    out["needs_owner"] = [m for m in listed if m["status"] not in ("auto", "needs_hand_row")]
    if hand:
        W.append_changelog(clone, [W.hand_row(m) for m in hand])
    y = W.commit_paths(clone, [W.CHANGELOG_DOC], "Live-test change log: rows for every commit in X..fill (D-120)",
                       _t("2026-11-12T00:05:00+00:00"))
    out["y"] = y
    p = W.run(world, [*helper, "--to", y])
    out["helper_after_y"] = {"returncode": p.returncode, "missing": json.loads(p.stdout)["missing"] if p.stdout else None}
    p = W.scorer_cli(world, "--prebuild-check", "--from", "", "--to", y)
    out["prebuild_y"] = {"exit": p.returncode, "json": _last_json(p.stdout), "stderr": p.stderr[-800:]}
    # the same commit Y without one row, on a side commit from the fill
    drop = (hand or listed)[0]["sha"] if (hand or listed) else None
    out["dropped"] = drop
    if drop:
        text, eol = W.read_text_lf(clone / W.CHANGELOG_DOC)
        W.git(clone, "checkout", "-q", "--detach", fill)
        lines = [ln for ln in text.split("\n") if not (ln.startswith("|") and drop[:12] in ln)]
        assert len(lines) == len(text.split("\n")) - 1, "the dropped commit is not on exactly one row"
        W.write_text_eol(clone / W.CHANGELOG_DOC, "\n".join(lines), eol)
        y_drop = W.commit_paths(clone, [W.CHANGELOG_DOC], "Live-test change log: one row left out (rehearsal check)",
                                _t("2026-11-12T00:06:00+00:00"))
        W.git(clone, "checkout", "-q", "main")
        p = W.scorer_cli(world, "--prebuild-check", "--from", "", "--to", y_drop)
        out["prebuild_dropped"] = {"exit": p.returncode, "y_drop": y_drop, "json": _last_json(p.stdout),
                                   "stderr": p.stderr[-800:]}
    assert W.git(clone, "rev-parse", "HEAD") == y
    return record(world.spec.name, "freeze", out)


def _last_json(text: str) -> Any:
    for line in reversed(text.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def final(world: W.World, *flags: str, label: str, env: dict[str, str] | None = None,
          cwd: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The scorer's ``--final`` through its CLI, plus what it wrote (report, sealed directory)."""
    md = world.weekly_reports_dir / "final.md"
    lines_json = world.sealed_dir / "final_lines.json"
    for f in (md, lines_json):
        if f.exists():
            f.unlink()
    sealed_before = W.tree_listing(world.sealed_dir)
    p = W.scorer_cli(world, "--final", *flags, timeout=FINAL_TIMEOUT, env=env, cwd=cwd, now=now)
    sealed_after = W.tree_listing(world.sealed_dir)
    out: dict[str, Any] = {"label": label, "flags": list(flags), "proc": p.as_dict(),
                           "verdict_stdout": W.verdict_lines(p.stdout),
                           "sealed_dir_changes": sorted(k for k in set(sealed_before) | set(sealed_after)
                                                        if sealed_before.get(k) != sealed_after.get(k))}
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        out.update(final_md=text, verdict_md=W.verdict_lines(text), lines=W.line_status(text),
                   checks=W.checks_table(text), flagged=W.flagged_table(text))
        shutil.copy2(md, world.evidence / f"final_{label}.md")
    if lines_json.is_file():
        out["final_lines"] = json.loads(lines_json.read_text(encoding="utf-8"))
        shutil.copy2(lines_json, world.evidence / f"final_lines_{label}.json")
    return record(world.spec.name, f"final:{label}", out)


def integrity(world: W.World) -> dict[str, Any]:
    p, res = W.driver(world, "integrity")
    assert p.returncode == 0, p.stderr[-3000:]
    return res


def sealed_inventory(world: W.World) -> dict[str, Any]:
    """Local sealed files, their branch records, the week status files and the weekly reports."""
    out: dict[str, Any] = {"weeks": {}}
    for s, w in W.WINDOW:
        wk = W.week_key(s, w)
        local = world.sealed_dir / f"{wk}.sha256"
        rec = world.sealed_dir / f"{wk}.json"
        status = world.weeks_dir / f"{wk}.json"
        record = json.loads(rec.read_text(encoding="utf-8")) if rec.is_file() else {}
        out["weeks"][wk] = {
            "local_sha256_file": local.read_text(encoding="utf-8") if local.is_file() else None,
            "branch_record": W.git(world.clone, "show", f"origin/livetest-2026:scoring/{wk}.sha256", check=False) or None,
            "part_sha256": W.sha256_file(world.sealed_dir / f"{wk}.parquet") if (world.sealed_dir / f"{wk}.parquet").is_file() else None,
            "consensus_part_sha256": W.sha256_file(world.sealed_dir / f"{wk}_consensus.parquet")
            if (world.sealed_dir / f"{wk}_consensus.parquet").is_file() else None,
            "report": (world.weekly_reports_dir / f"{wk}_verification.md").is_file(),
            "status_file": status.is_file(),
            "missed_marker": W.git(world.clone, "show", f"origin/livetest-2026:{wk}/MISSED.json", check=False) or None,
            "data_cutoff": record.get("data_cutoff"), "data_snapshots": record.get("data_snapshots"),
            "outcome_rows_sha256": record.get("outcome_rows_sha256"), "code_commit": record.get("code_commit"),
            "bootstrap_frames": record.get("bootstrap_frames"),
        }
    return out


def snapshot_ids(world: W.World) -> dict[str, list[str]]:
    out = {}
    for tier, root in (("processed", world.data_dir / "processed"), ("restricted", world.data_dir / "processed" / "restricted")):
        for ds in (["dim_game", "fact_player_game", "fact_dst_game"] if tier == "processed" else ["fact_ecr_weekly"]):
            d = root / ds
            out[f"{tier}/{ds}"] = sorted(p.name.split("=", 1)[-1] for p in d.iterdir()
                                         if p.is_dir() and p.name.startswith("snapshot=")) if d.is_dir() else []
    return out


def weekly_leaks(world: W.World, ev: dict[str, Any]) -> dict[str, list[str]]:
    """Metric values in anything weekly scoring or sealing writes outside the sealed parts."""
    leaks: dict[str, list[str]] = {}
    for f in sorted(world.weekly_reports_dir.glob("*_verification.md")):
        leaks[f"reports/livetest/{f.name}"] = W.metric_leaks(f.read_text(encoding="utf-8"))
    for f in sorted(world.sealed_dir.glob("*.sha256")):
        leaks[f"data/livetest/scoring/sealed/{f.name}"] = W.metric_leaks(f.read_text(encoding="utf-8"))
    for f in sorted(world.weeks_dir.glob("*.json")):
        leaks[f"data/livetest/scoring/weeks/{f.name}"] = W.metric_leaks(f.read_text(encoding="utf-8"))
    for rel in ev["branch_files"]:
        if rel.startswith("scoring/") or rel.endswith(("sealed_hashes.json", "STATUS.md", "README.md")):
            leaks[f"livetest-2026:{rel}"] = W.metric_leaks(W.git(world.clone, "show", f"origin/livetest-2026:{rel}"))
    return leaks


# --------------------------------------------------------------------------- #
# World A: PASS, sealed results, fetch, markers, change log, sensitivity, reproductions
# --------------------------------------------------------------------------- #


def pipeline_pass(root: Path, spawn: Callable[[str, Callable[[], dict[str, Any]]], None]) -> dict[str, Any]:
    spec = W.WorldSpec("A_pass", weekly_sensitivity=True)
    st = prepare(spec, root / "A")
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    ev["snapshot_ids"] = snapshot_ids(world)
    ev["metric_leaks"] = weekly_leaks(world, ev)
    if not QUICK:  # the real 5,000-draw run (the CLI default) on a copy, beside the rest
        copy_root = root / "A5000"
        shutil.copytree(world.root, copy_root, symlinks=True)
        copy = W.World(W.WorldSpec("A5000_sensitivity"), copy_root)
        spawn("A5000_sensitivity", lambda: {"world": "A5000_sensitivity",
                                            "final_default": final(copy, label="default_with_5000")})
    import polars as pl

    sealed: dict[str, Any] = {}
    # --- the final refuses tampered sealed files before reading anything ---------------- #
    part = world.sealed_dir / "2026w14.parquet"
    keep = part.read_bytes()
    pl.read_parquet(part).head(-1).write_parquet(part)
    sealed["tampered_part"] = final(world, label="tampered_part")
    part.write_bytes(keep)
    rec = world.sealed_dir / "2026w11.json"
    keep = rec.read_bytes()
    data = json.loads(keep)
    data["files_verified"] = 999
    rec.write_text(json.dumps(data, indent=1), encoding="utf-8")
    sealed["tampered_record"] = final(world, label="tampered_record")
    rec.write_bytes(keep)
    # --- a branch without the last seal record (a second bare repository as origin) ----- #
    alt = world.root / "remote-missing-record.git"
    W.git(world.root, "clone", "-q", "--bare", "--no-local", world.remote.as_uri(), str(alt))
    head = W.git(alt, "rev-parse", "refs/heads/livetest-2026")
    W.git(alt, "update-ref", "refs/heads/livetest-2026", f"{head}~1")
    sealed["missing_branch_record_ref"] = {"remote_head": head, "alt_head": W.git(alt, "rev-parse", "refs/heads/livetest-2026"),
                                           "files_removed": W.git(alt, "diff", "--name-only", f"{head}~1", head)}
    W.git(world.clone, "remote", "set-url", "origin", alt.as_uri())
    sealed["missing_branch_record"] = final(world, label="missing_branch_record")
    # --- the GitHub fetch fails: exit 75, no verdict, nothing written ------------------- #
    W.git(world.clone, "remote", "set-url", "origin", (world.root / "no-such-remote.git").as_uri())
    sealed["fetch_failed"] = final(world, label="fetch_failed")
    sealed["fetch_failed"]["final_md_exists"] = (world.weekly_reports_dir / "final.md").is_file()
    W.git(world.clone, "remote", "set-url", "origin", world.remote.as_uri())
    W.git(world.clone, "fetch", "-q", "origin", "+refs/heads/livetest-2026:refs/remotes/origin/livetest-2026")
    # --- QA-F29: tampering with the worktree's uncommitted copy no longer helps ---------- #
    sealed["worktree_tamper"] = record(spec.name, "worktree_tamper", worktree_tamper(world, *W.FIRST_WEEK))
    ev["sealed_checks"] = sealed

    # --- the PASS final (the retry after the failed fetch); binding 500 draws ------------- #
    ev["final_pass"] = final(world, "--no-sensitivity", label="pass_no_sensitivity")
    # the same final again with the consensus archive and the fact tables moved away: identical results show
    # that the final reads only sealed parts (D-116 A3), and that it is deterministic
    hidden = world.root / "hidden-stores"
    hidden.mkdir(exist_ok=True)
    moved = []
    for rel in ("restricted", "fact_player_game", "fact_dst_game"):
        src = world.data_dir / "processed" / rel
        if src.exists():
            shutil.move(str(src), str(hidden / rel))
            moved.append(rel)
    try:
        ev["final_pass_repeat"] = final(world, "--no-sensitivity", label="pass_no_sensitivity_repeat")
    finally:
        for rel in moved:
            shutil.move(str(hidden / rel), str(world.data_dir / "processed" / rel))
    ev["final_pass_repeat"]["stores_moved_away"] = moved
    ev["integrity_pass"] = integrity(world)
    p, forced = W.driver(world, "final-forced", {"mode": "all-fail"}, timeout=FINAL_TIMEOUT)
    assert p.returncode == 0, p.stderr[-3000:]
    ev["final_forced_all_fail"] = {"verdict": forced["verdict"], "lines": W.line_status(forced["final_md"]),
                                   "differing": (forced.get("final_lines") or {}).get("sensitivity", {}).get("differing"),
                                   "final_md": forced["final_md"]}
    ev["git_scenarios"] = git_scenarios(world, ev["commits"])
    ev["repro_pinned"] = record(spec.name, "repro_pinned", repro_pinned(world, ev["commits"]["Y_log"]))
    # last (they push to the branch): a stray MISSED marker on a committed week, then the QA-F28 attack
    ev["stray_marker"] = record(spec.name, "stray_marker", stray_marker(world, 2026, 13))
    return ev


def worktree_tamper(world: W.World, season: int, week: int) -> dict[str, Any]:
    """QA-F29: a consistent local tampering of the sealed part, record and the worktree's copy of the branch record."""
    import polars as pl

    wk = W.week_key(season, week)
    part, rec, local = (world.sealed_dir / f"{wk}.parquet", world.sealed_dir / f"{wk}.json",
                        world.sealed_dir / f"{wk}.sha256")
    wt_file = world.commitments / "scoring" / f"{wk}.sha256"
    backup = {p: p.read_bytes() for p in (part, rec, local, wt_file)}
    try:
        pl.read_parquet(part).with_columns((pl.col("actual") + 5.0).alias("actual")).write_parquet(part)
        data = json.loads(rec.read_text(encoding="utf-8"))
        data["part_sha256"] = W.sha256_file(part)
        rec.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        text = "".join(f"{W.sha256_file(p)}  {p.name}\n" for p in (part, world.sealed_dir / f"{wk}_consensus.parquet", rec)
                       if p.is_file())
        local.write_text(text, encoding="utf-8")
        wt_file.write_text(text, encoding="utf-8")
        status = W.git(world.commitments, "status", "--porcelain")
        p, seals = W.driver(world, "seals")
        res = final(world, label="worktree_tamper")
        res["worktree_status"] = status
        res["seals_by_mode"] = {m: [c for c in v if c["name"].startswith(wk)] for m, v in seals.items()}
    finally:
        for p, data in backup.items():
            p.write_bytes(data)
    res["restored_status"] = W.git(world.commitments, "status", "--porcelain")
    return res


def stray_marker(world: W.World, season: int, week: int) -> dict[str, Any]:
    """A MISSED.json pushed by hand for a committed, sealed week: flagged, not used; then the QA-F28 attack."""
    wk = W.week_key(season, week)
    wt = world.commitments
    created = "2027-11-26T09:00:00+00:00"
    marker = {"format": "ffml.livetest.missed/2", "season": season, "week": week, "created_at": created,
              "status": "MISSED: no commitment was pushed to this branch before the first kickoff",
              "reason": {"category": "technical_failure", "detail": "synthetic: written by hand (QA rehearsal)"}}
    (wt / wk / "MISSED.json").write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    when = _t("2027-11-26T09:05:00+00:00")
    W.git(wt, "add", f"{wk}/MISSED.json")
    W.git(wt, "commit", "-q", "-m", f"livetest {wk}: MISSED (written by hand)", env=W.git_dates(when))
    W.git(wt, "push", "-q", "origin", "refs/heads/livetest-2026:refs/heads/livetest-2026")
    out: dict[str, Any] = {"week": wk, "marker_created_at": created, "marker_commit_time": when.isoformat(),
                           "marker_commit": W.git(wt, "rev-parse", "HEAD")}
    out["final"] = final(world, "--no-sensitivity", label="stray_marker")
    # the QA-F28 attack: the week's local sealed files removed
    moved = world.root / "moved-sealed"
    moved.mkdir(exist_ok=True)
    names = [p.name for p in world.sealed_dir.glob(f"{wk}*")]
    for n in names:
        shutil.move(str(world.sealed_dir / n), str(moved / n))
    out["attack_local_files_removed"] = final(world, "--no-sensitivity", label="marker_attack")
    for n in names:
        shutil.move(str(moved / n), str(world.sealed_dir / n))
    return out


def git_scenarios(world: W.World, commits: dict[str, str]) -> dict[str, Any]:
    clone = world.clone
    out: dict[str, Any] = {}
    base = W.git(clone, "rev-parse", "HEAD")
    when = _t("2027-11-26T12:00:00+00:00")

    def run_scenario(name: str, start: str, actions: Callable[[], dict[str, str]]) -> None:
        W.git(clone, "checkout", "-q", "-b", f"scn-{name}", start)
        made = actions()
        res = final(world, label=f"git_{name}")
        res["commits"] = made
        res["log"] = W.git(clone, "log", "--format=%h %p %s", f"{commits['X']}..HEAD")
        out[name] = res
        W.git(clone, "checkout", "-q", "main")
        W.git(clone, "reset", "-q", "--hard", base)

    def missing_entry():
        (clone / "scripts" / "x.py").write_text('"""Unlogged synthetic change."""\n', encoding="utf-8")
        return {"scripts_x": W.commit_paths(clone, ["scripts/x.py"], "Runner: unlogged change", when)}

    def edit_entry():
        W.edit_changelog(clone, "| runner change |", "| runner change (reworded) |")
        return {"edit": W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: reword an entry", when)}

    def delete_entry():
        text, eol = W.read_text_lf(clone / W.CHANGELOG_DOC)
        lines = [ln for ln in text.split("\n") if "| runner change |" not in ln]
        W.write_text_eol(clone / W.CHANGELOG_DOC, "\n".join(lines), eol)
        return {"delete": W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: delete an entry", when)}

    def fill_placeholder():
        W.edit_changelog(clone, "| | | | | | | |", W.changelog_row(commits["F_fill"], W.PREREG_DOC,
                                                                     "freeze: commit field filled"))
        return {"fill_placeholder": W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: first entry in the template row",
                                                   when)}

    def log_plus_other_path():
        (clone / "docs" / "status" / "rehearsal-note.md").write_text("Synthetic note.\n", encoding="utf-8")
        W.append_changelog(clone, [W.changelog_row(commits["A_logged_change"], "scripts/", "duplicate entry",
                                                   date="2027-11-26")])
        return {"log_plus_doc": W.commit_paths(clone, [W.CHANGELOG_DOC, "docs/status/rehearsal-note.md"],
                                               "Change log entry plus a note in one commit", when)}

    def merge():
        W.git(clone, "checkout", "-q", "-b", "scn-merge-side")
        W.append_changelog(clone, [W.changelog_row(commits["A_logged_change"], "scripts/", "duplicate entry",
                                                   date="2027-11-26")])
        side = W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: append on a side branch", when)
        W.git(clone, "checkout", "-q", "scn-merge")
        W.git(clone, "merge", "-q", "--no-ff", "-m", "Merge the side branch", "scn-merge-side",
              env=W.git_dates(when + timedelta(minutes=5)))
        return {"side_append": side, "merge": W.git(clone, "rev-parse", "HEAD")}

    def frozen_path():
        (clone / "ffml" / "models" / "x.py").write_text('"""Synthetic change on a frozen path."""\n', encoding="utf-8")
        z = W.commit_paths(clone, ["ffml/models/x.py"], "Models: synthetic frozen-path change", when)
        W.append_changelog(clone, [W.changelog_row(z, "ffml/models/x.py", "frozen-path change", date="2027-11-26")])
        lz = W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: entry for the frozen-path change",
                            when + timedelta(minutes=5))
        return {"frozen": z, "log_frozen": lz}

    run_scenario("fill_unlogged", commits["F_fill"], lambda: {"fill": commits["F_fill"]})
    run_scenario("missing_entry", base, missing_entry)
    run_scenario("edit_entry", base, edit_entry)
    run_scenario("delete_entry", base, delete_entry)
    run_scenario("fill_placeholder", commits["F_fill"], fill_placeholder)
    run_scenario("log_plus_other_path", base, log_plus_other_path)
    run_scenario("merge", base, merge)
    run_scenario("frozen_path", base, frozen_path)
    out["valid_base"] = {"head": base, "log": W.git(clone, "log", "--format=%h %p %s", f"{commits['X']}..HEAD")}
    return out


def repro_pinned(world: W.World, x: str) -> dict[str, Any]:
    """QA-F27 recheck: weekly scoring from the worktree pinned at the build commit (Y, D-120 step c), then the
    runner's pin check; then the final run from that worktree."""
    pinned = world.root / "ffml-livetest-code"
    W.git(world.clone, "worktree", "add", "-q", "--detach", str(pinned), x)
    env = world.env(PYTHONPATH=str(pinned), FFML_REHEARSAL_CLONE=str(pinned))
    scratch = world.root / "pinned-scratch-root"
    p, res = W.driver(world, "repro-pinned-dirty", {"x": x, "main_repo": str(world.clone), "scratch": str(scratch),
                                                    "sealed_dir": str(world.sealed_dir), "season": W.FIRST_WEEK[0],
                                                    "week": W.FIRST_WEEK[1]},
                      cwd=pinned, env=env)
    res["returncode"] = p.returncode
    res["stderr_tail"] = p.stderr[-1500:]
    res["pinned_commit"] = x
    res["final_pinned"] = final(world, "--no-sensitivity", label="final_from_pinned_worktree", env=env, cwd=pinned)
    return res


# --------------------------------------------------------------------------- #
# Other worlds
# --------------------------------------------------------------------------- #


def pipeline_fail(root: Path, name: str, spec: W.WorldSpec, *, forced: str | None = None) -> dict[str, Any]:
    st = prepare(spec, root / name)
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    ev["final"] = final(world, "--no-sensitivity", label="no_sensitivity")
    if forced:
        p, res = W.driver(world, "final-forced", {"mode": forced}, timeout=FINAL_TIMEOUT)
        assert p.returncode == 0, p.stderr[-3000:]
        ev["final_forced"] = {"mode": forced, "verdict": res["verdict"], "lines": W.line_status(res["final_md"]),
                              "differing": (res.get("final_lines") or {}).get("sensitivity", {}).get("differing")}
    return ev


def pipeline_misses(root: Path, name: str, spec: W.WorldSpec, *, check_last_week: bool = False,
                    equivalence: bool = False) -> dict[str, Any]:
    st = prepare(spec, root / name)
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    ev["status_md"] = W.git(world.clone, "show", "origin/livetest-2026:STATUS.md", check=False)
    if check_last_week:
        p, res = W.driver(world, "dim-game-unfinished", {"season": W.LAST_WEEK[0], "week": W.LAST_WEEK[1],
                                                           "at": "2027-12-01T00:00:00+00:00"})
        assert p.returncode == 0, p.stderr[-3000:]
        ev["last_week_not_final"] = {"snapshot": res, **final(world, label="last_week_not_final")}
        (world.data_dir / "processed" / "dim_game" / "LATEST").write_text(res["old"] + "\n", encoding="utf-8")
    if equivalence:
        eq = {}
        for s, w in spec.cli_weeks:
            p, res = W.driver(world, "hook-part", {"season": s, "week": w, "at": iso(W.friday_scoring(s, w)),
                                                   "root": str(world.root / f"hook-scratch-{s}w{w:02d}")})
            assert p.returncode == 0, p.stderr[-3000:]
            wk = W.week_key(s, w)
            eq[wk] = {"cli_part_sha256": W.sha256_file(world.sealed_dir / f"{wk}.parquet"),
                      "hook_part_sha256": res["part_sha256"],
                      "cli_consensus_sha256": W.sha256_file(world.sealed_dir / f"{wk}_consensus.parquet"),
                      "hook_consensus_sha256": res["consensus_part_sha256"],
                      "cli_outcome_rows_sha256": ev["sealed"]["weeks"][wk]["outcome_rows_sha256"],
                      "hook_outcome_rows_sha256": res["outcome_rows_sha256"]}
        ev["hook_cli_equivalence"] = record(spec.name, "hook_cli_equivalence", eq)
    ev["final"] = final(world, "--no-sensitivity", label="no_sensitivity")
    ev["integrity"] = integrity(world)
    return ev


def pipeline_correction(root: Path) -> dict[str, Any]:
    """World E: world A's truth, with preliminary values before every cutoff and 2027 corrections after it."""
    spec = W.WorldSpec("E_stat_correction", corrections=True, cli_weeks=[(2027, 1)], weekly_sensitivity=False)
    st = prepare(spec, root / "E")
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    ev["snapshot_ids"] = snapshot_ids(world)
    p, latest = W.driver(world, "latest-pins")
    assert p.returncode == 0, p.stderr[-3000:]
    ev["latest_pins"] = latest
    # what the corrected LATEST data would give for 2027w01 (scored into a scratch root, not sealed)
    p, unpinned = W.driver(world, "hook-part", {"season": 2027, "week": 1, "at": "2027-12-01T00:00:00+00:00",
                                                "root": str(world.root / "latest-scratch"), "pins": latest})
    assert p.returncode == 0, p.stderr[-3000:]
    ev["unpinned_2027w01"] = record(spec.name, "unpinned_2027w01", unpinned)
    # the real CLI rescore of 2027w01 after its correction: it must reuse the recorded pins
    keep = {q: q.read_bytes() for q in world.sealed_dir.glob("2027w01*")}
    keep.update({q: q.read_bytes() for q in world.weeks_dir.glob("2027w01*")})
    before = {"part": W.sha256_file(world.sealed_dir / "2027w01.parquet"),
              "record_sha": W.sha256_file(world.sealed_dir / "2027w01.json"),
              "record": json.loads((world.sealed_dir / "2027w01.json").read_text(encoding="utf-8"))}
    rescore = W.scorer_cli(world, "--week", "2027w01", now=_t("2027-12-01T13:00:00+00:00"))
    after_record = json.loads((world.sealed_dir / "2027w01.json").read_text(encoding="utf-8"))
    ev["rescore"] = {"proc": rescore.as_dict(), "part_before": before["part"],
                     "part_after": W.sha256_file(world.sealed_dir / "2027w01.parquet"),
                     "pins_before": before["record"].get("data_snapshots"), "pins_after": after_record.get("data_snapshots"),
                     "outcome_before": before["record"].get("outcome_rows_sha256"),
                     "outcome_after": after_record.get("outcome_rows_sha256"),
                     "record_sha_changed": W.sha256_file(world.sealed_dir / "2027w01.json") != before["record_sha"]}
    record(spec.name, "rescore", ev["rescore"])
    ev["rescore"]["final_after_rescore"] = final(world, "--no-sensitivity", label="after_rescore")
    for q, data in keep.items():  # put the original sealed files back
        q.write_bytes(data)
    ev["final"] = final(world, "--no-sensitivity", label="no_sensitivity")
    return ev


def pipeline_unfinal(root: Path) -> dict[str, Any]:
    """World G (D-121): one game never final, one final 4 days after its cutoff; then the comparators gap."""
    spec = W.WorldSpec("G_unfinal_game", unfinal_games=unfinal_spec_games(), weekly_sensitivity=False)
    st = prepare(spec, root / "G")
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    ev["unfinal_games"] = unfinal_spec_games()
    for wk in ("2026w14", "2026w15", "2026w16", "2027w02"):
        rec = json.loads((world.sealed_dir / f"{wk}.json").read_text(encoding="utf-8"))
        ev["sealed"]["weeks"][wk].update({k: rec.get(k) for k in ("excluded_games", "pin_from_utc",
                                                                   "exclusion_after_utc", "scored_at")})
    record(spec.name, "sealed_d121_weeks", {wk: {k: ev["sealed"]["weeks"][wk].get(k) for k in (
        "excluded_games", "pin_from_utc", "exclusion_after_utc", "data_cutoff", "data_snapshots", "outcome_rows_sha256")}
        for wk in ("2026w14", "2026w15", "2026w16", "2027w02")})
    ev["metric_leaks"] = weekly_leaks(world, ev)
    ev["final"] = final(world, "--no-sensitivity", label="no_sensitivity", now=FINAL_NOW)
    ev["comparators_gap"] = comparators_gap(world)
    return ev


def comparators_gap(world: W.World) -> dict[str, Any]:
    """QA-F31 recheck: ``ensure_comparators`` for 2026w14 (its game excluded) and 2026w17 (a later week) when the
    comparators run must be extended, in-process with a stub runner (see the driver's ``comparators-probe``)."""
    s, w, g = UNFINAL_NEVER
    gid = W.game_id(s, w, g)
    pred = world.data_dir / "backtests" / "livetest_comparators" / "predictions.parquet"
    keep = pred.read_bytes()
    dirs = (world.sealed_dir, world.weeks_dir, world.weekly_reports_dir)
    before = [W.tree_listing(d) for d in dirs]
    cases = [{"label": "unfinal_week", "season": s, "week": w, "at": iso(W.data_cutoff(s, w) + 14 * D + 2 * H)},
             {"label": "later_week", "season": s, "week": 17, "at": iso(W.data_cutoff(s, 17) + 2 * H)}]
    try:
        p, out = W.driver(world, "comparators-probe", {"cases": cases, "excluded_game": {
            "season": s, "week": w, "game_id": gid, "teams": sorted(gid.split("_")[2:])}})
    finally:
        restored = pred.read_bytes() == keep
        pred.write_bytes(keep)
    out.update(returncode=p.returncode, stderr_tail=p.stderr[-2000:],
               restored=restored and [W.tree_listing(d) for d in dirs] == before)
    return record(world.spec.name, "comparators_gap", out)


def pipeline_prebuild(root: Path) -> dict[str, Any]:
    """World P: blocked builds (unlogged change fixed in time; unlogged change left; frozen-path change)."""
    spec = W.WorldSpec("P_prebuild", prebuild=True, weekly_sensitivity=False)
    st = prepare(spec, root / "P")
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    ev["prebuild_steps"] = [s for s in ev["weekly"] if s.get("tag")]
    ev["final"] = final(world, "--no-sensitivity", label="no_sensitivity")
    return ev


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def run_all(root: Path, workers: int = 3, only: set[str] | None = None) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    results: dict[str, Any] = {}
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=workers)
    futures: list = []

    def guarded(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        t0 = time.perf_counter()
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - every world reports its own failure
            res = {"world": name, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        res["seconds"] = round(time.perf_counter() - t0, 1)
        W.stream(name, "world_done", {"seconds": res["seconds"], "error": res.get("error")})
        with lock:
            results[name] = res

    def spawn(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        with lock:
            futures.append(pool.submit(guarded, name, fn))  # counts against the same worker limit

    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "A_pass": lambda: pipeline_pass(root, spawn),
        "G_unfinal_game": lambda: pipeline_unfinal(root),
        "E_stat_correction": lambda: pipeline_correction(root),
        "P_prebuild": lambda: pipeline_prebuild(root),
        "D1_one_miss": lambda: pipeline_misses(root, "D1", W.WorldSpec(
            "D1_one_miss", missed=[(2026, 12)], cli_weeks=[W.FIRST_WEEK], weekly_sensitivity=False), equivalence=True),
        "B_qb_narrow": lambda: pipeline_fail(root, "B", W.WorldSpec("B_qb_narrow", qb_sigma_factor=0.8,
                                                                     weekly_sensitivity=False), forced="all-pass"),
        "C_accuracy_tie": lambda: pipeline_fail(root, "C", W.WorldSpec("C_accuracy_tie",
                                                                       tie_baseline=("prior_season", "WR"),
                                                                       weekly_sensitivity=False)),
        "D2_two_misses": lambda: pipeline_misses(root, "D2", W.WorldSpec(
            "D2_two_misses", missed=[(2026, 12), W.LAST_WEEK], weekly_sensitivity=False), check_last_week=True),
        "D3_three_misses": lambda: pipeline_misses(root, "D3", W.WorldSpec(
            "D3_three_misses", missed=[(2026, 12), (2026, 16), (2027, 3)], weekly_sensitivity=False)),
        "B2_qb_narrow_repeat": lambda: pipeline_fail(root, "B2", W.WorldSpec("B_qb_narrow", qb_sigma_factor=0.8,
                                                                             weekly_sensitivity=False)),
    }
    for n, fn in jobs.items():
        if only is None or n in only:
            spawn(n, fn)
    done = 0
    while True:
        with lock:
            pending = futures[done:]
        if not pending:
            break
        for f in pending:
            f.result()
        done += len(pending)
    pool.shutdown(wait=True)
    return results


def evidence_json(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=1, default=str)


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
