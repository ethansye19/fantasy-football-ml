"""Scenario pipelines of the live-test final rehearsal (run by test_livetest_final_rehearsal.py).

Each pipeline builds one synthetic world from scratch (clone at X, fake remote,
synthetic stores), runs the season week by week through the runner and the
scorer, then the final evaluation through the scorer's CLI, and returns a JSON
evidence record. Pipelines are independent and run in parallel threads; every
heavy step is a subprocess.
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


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def weekly_plan(spec: W.WorldSpec) -> list[dict[str, Any]]:
    missed = set(spec.missed)
    steps: list[dict[str, Any]] = [{"op": "init", "season": 2026, "weeks": "10-18", "at": "2026-11-11T00:00:00+00:00"}]
    for s, w in W.WINDOW:
        if (s, w) == (2027, 1):
            if (2026, 18) in missed:
                steps.append({"op": "misses", "at": (W.first_kickoff(2026, 18) + timedelta(hours=1)).isoformat()})
            steps.append({"op": "init", "season": 2027, "weeks": "1-9", "force": True,
                          "at": "2027-09-01T00:00:00+00:00"})
        if (s, w) in missed:
            continue
        steps.append({"op": "push", "season": s, "week": w})
        if (s, w) in set(spec.cli_weeks):
            steps.append({"op": "score-cli", "season": s, "week": w})
        else:
            steps.append({"op": "score-hook", "season": s, "week": w, "n_boot": 5, "sens_n_boot": 5,
                          "weekly_sensitivity": spec.weekly_sensitivity})
        steps.append({"op": "seal", "season": s, "week": w})
    if (2027, 9) in missed:
        steps.append({"op": "misses", "at": (W.first_kickoff(2027, 9) + timedelta(hours=1)).isoformat()})
    return steps


def prepare(spec: W.WorldSpec, root: Path) -> dict[str, Any]:
    """Clone, probe, synthetic stores, the freeze commits, the whole season, then a logged post-freeze change."""
    world = W.World(spec, root)
    ev: dict[str, Any] = {"world": spec.name, "spec": json.loads(spec.to_json()), "root": str(root)}
    t0 = time.perf_counter()
    x = W.make_clone(world)
    ev["x"] = x
    p, probe = W.driver(world, "probe")
    assert p.returncode == 0, p.stderr[-3000:]
    ev["probe"] = probe
    p, built = W.driver(world, "build")
    assert p.returncode == 0, p.stderr[-3000:]
    ev["build"] = built
    # the freeze: fill the commit field (and, for the rehearsal only, the synthetic stored-parts digest)
    W.fill_preregistration(world.clone, x, built["stored_digest"])
    f = W.commit_paths(world.clone, [W.PREREG_DOC], "Freeze: pre-registration commit field (synthetic rehearsal)",
                       _t("2026-11-12T00:00:00+00:00"))
    W.append_changelog(world.clone, [W.changelog_row(f, W.PREREG_DOC, "freeze: commit field filled")])
    l1 = W.commit_paths(world.clone, [W.CHANGELOG_DOC], "Change log: entry for the freeze commit",
                        _t("2026-11-12T00:05:00+00:00"))
    ev["commits"] = {"X": x, "F_fill": f, "L1_log_F": l1}
    ev["diff_x_head_frozen_code"] = W.git(world.clone, "diff", "--name-only", x, "HEAD", "--", "ffml", "pyproject.toml",
                                          "requirements.lock.txt")
    p, weekly = W.driver(world, "weekly", {"x": x, "steps": weekly_plan(spec)}, timeout=6 * 3600)
    assert p.returncode == 0, p.stderr[-4000:]
    ev["weekly"] = weekly["steps"]
    bad = [s for s in weekly["steps"] if s.get("exit") != 0]
    assert not bad, json.dumps(bad, indent=1, default=str)[-6000:]
    # a logged post-freeze change on an allowed path (the change log names it in a later, append-only commit)
    (world.clone / "scripts" / "rehearsal_logged_change.py").write_text(
        '"""Synthetic post-freeze change on an allowed path (QA rehearsal)."""\n', encoding="utf-8")
    a = W.commit_paths(world.clone, ["scripts/rehearsal_logged_change.py"], "Runner: synthetic logged change",
                       _t("2027-03-01T12:00:00+00:00"))
    W.append_changelog(world.clone, [W.changelog_row(a, "scripts/rehearsal_logged_change.py", "runner change",
                                                     first_week="2027w01", date="2027-03-01")])
    l2 = W.commit_paths(world.clone, [W.CHANGELOG_DOC], "Change log: entry for the runner change",
                        _t("2027-03-01T12:05:00+00:00"))
    ev["commits"].update(A_logged_change=a, L2_log_A=l2)
    ev["branch_files"] = sorted(W.git(world.clone, "ls-tree", "-r", "--name-only", "origin/livetest-2026").splitlines())
    ev["prepare_seconds"] = round(time.perf_counter() - t0, 1)
    return {"world": world, "ev": ev}


def final(world: W.World, *flags: str, label: str) -> dict[str, Any]:
    """The scorer's ``--final`` through its CLI, plus what it wrote."""
    md = world.weekly_reports_dir / "final.md"
    lines_json = world.sealed_dir / "final_lines.json"
    for f in (md, lines_json):
        if f.exists():
            f.unlink()
    p = W.scorer_cli(world, "--final", *flags, timeout=FINAL_TIMEOUT)
    out: dict[str, Any] = {"label": label, "flags": list(flags), "proc": p.as_dict(),
                           "verdict_stdout": W.verdict_lines(p.stdout)}
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        out.update(final_md=text, verdict_md=W.verdict_lines(text), lines=W.line_status(text),
                   checks=W.checks_table(text))
        shutil.copy2(md, world.evidence / f"final_{label}.md")
    if lines_json.is_file():
        out["final_lines"] = json.loads(lines_json.read_text(encoding="utf-8"))
        shutil.copy2(lines_json, world.evidence / f"final_lines_{label}.json")
    return out


def integrity(world: W.World) -> list[dict[str, Any]]:
    p, res = W.driver(world, "integrity")
    assert p.returncode == 0, p.stderr[-3000:]
    return res["checks"]


def sealed_inventory(world: W.World) -> dict[str, Any]:
    """Local sealed files, their branch records, and the weekly reports."""
    out: dict[str, Any] = {"weeks": {}}
    for s, w in W.WINDOW:
        wk = W.week_key(s, w)
        local = world.sealed_dir / f"{wk}.sha256"
        branch = W.git(world.clone, "show", f"origin/livetest-2026:scoring/{wk}.sha256", check=False)
        out["weeks"][wk] = {
            "local_sha256_file": local.read_text(encoding="utf-8") if local.is_file() else None,
            "branch_record": branch or None,
            "part_sha256": W.sha256_file(world.sealed_dir / f"{wk}.parquet") if (world.sealed_dir / f"{wk}.parquet").is_file() else None,
            "report": (world.weekly_reports_dir / f"{wk}_verification.md").is_file(),
            "missed_marker": W.git(world.clone, "show", f"origin/livetest-2026:{wk}/MISSED.json", check=False) or None,
        }
    return out


# --------------------------------------------------------------------------- #
# Pipelines
# --------------------------------------------------------------------------- #


def pipeline_pass(root: Path, spawn: Callable[[str, Callable[[], dict[str, Any]]], None]) -> dict[str, Any]:
    """World A: PASS world, sealed-file checks, change-log scenarios, sensitivity, two reproductions."""
    spec = W.WorldSpec("A_pass", weekly_sensitivity=True)
    st = prepare(spec, root / "A")
    world, ev = st["world"], st["ev"]
    ev["sealed"] = sealed_inventory(world)
    # weekly outputs carry no metric values (reports, sealed hash files, everything on the branch)
    leaks: dict[str, list[str]] = {}
    for f in sorted(world.weekly_reports_dir.glob("*_verification.md")):
        leaks[f"reports/livetest/{f.name}"] = W.metric_leaks(f.read_text(encoding="utf-8"))
    for f in sorted(world.sealed_dir.glob("*.sha256")):
        leaks[f"data/livetest/scoring/sealed/{f.name}"] = W.metric_leaks(f.read_text(encoding="utf-8"))
    for rel in ev["branch_files"]:  # what weekly scoring and sealing publish (commitments are not scoring output)
        if rel.startswith("scoring/") or rel.endswith(("sealed_hashes.json", "STATUS.md", "README.md")):
            leaks[f"livetest-2026:{rel}"] = W.metric_leaks(W.git(world.clone, "show", f"origin/livetest-2026:{rel}"))
    ev["metric_leaks"] = leaks
    for step in ev["weekly"]:
        if step["op"] == "score-hook":
            step["message_leaks"] = W.metric_leaks(step.get("message", ""))

    # copy the world for the 5,000-draw run (the real CLI default) so it runs beside the rest
    if not QUICK:
        copy_root = root / "A5000"
        shutil.copytree(world.root, copy_root, symlinks=True)
        copy = W.World(W.WorldSpec("A5000_sensitivity"), copy_root)
        spawn("A5000_sensitivity", lambda: {"world": "A5000_sensitivity",
                                            "final_default": final(copy, label="default_with_5000")})

    # --- sealed results: refusals before anything is read --------------------------------- #
    sealed: dict[str, Any] = {}
    part = world.sealed_dir / "2026w14.parquet"
    keep = part.read_bytes()
    import polars as pl

    pl.read_parquet(part).head(-1).write_parquet(part)  # one row dropped
    sealed["tampered_part"] = final(world, label="tampered_part")
    sealed["tampered_part"]["final_md_written"] = (world.weekly_reports_dir / "final.md").is_file()
    part.write_bytes(keep)
    rec = world.sealed_dir / "2026w11.json"
    keep = rec.read_bytes()
    data = json.loads(keep)
    data["files_verified"] = 999
    rec.write_text(json.dumps(data, indent=1), encoding="utf-8")
    sealed["tampered_record"] = final(world, label="tampered_record")
    rec.write_bytes(keep)
    # a missing branch record: the branch as it was before the last seal was pushed (read through git only)
    ref = "refs/remotes/origin/livetest-2026"
    head = W.git(world.clone, "rev-parse", ref)
    W.git(world.clone, "update-ref", ref, f"{head}~1")
    sealed["missing_branch_record_ref"] = {"rewound_from": head, "rewound_to": W.git(world.clone, "rev-parse", ref),
                                           "files_removed": W.git(world.clone, "diff", "--name-only", f"{head}~1", head)}
    sealed["missing_branch_record"] = final(world, "--no-worktree", label="missing_branch_record")
    W.git(world.clone, "update-ref", ref, head)
    # the same record deleted only from the commitments worktree's files falls back to the committed copy
    wt_file = world.commitments / "scoring" / "2027w09.sha256"
    keep = wt_file.read_bytes()
    wt_file.unlink()
    p, seals = W.driver(world, "seals")
    assert p.returncode == 0, p.stderr[-3000:]
    sealed["worktree_file_deleted_seals"] = {m: [c for c in v if c["name"].startswith("2027w09")]
                                             for m, v in seals.items()}
    wt_file.write_bytes(keep)
    p, sealed["seals_intact"] = W.driver(world, "seals")
    assert p.returncode == 0, p.stderr[-3000:]
    p, sealed["repro_worktree_trust"] = W.driver(world, "repro-worktree-trust", {"season": 2026, "week": 10})
    assert p.returncode == 0, p.stderr[-3000:]
    ev["sealed_checks"] = sealed

    # --- the PASS final (binding 500 draws; the 5,000-draw run is the copy above) ---------- #
    ev["final_pass"] = final(world, "--no-sensitivity", label="pass_no_sensitivity")
    ev["final_pass_repeat"] = final(world, "--no-sensitivity", label="pass_no_sensitivity_repeat")
    ev["integrity_pass"] = integrity(world)
    p, forced = W.driver(world, "final-forced", {"mode": "all-fail"}, timeout=FINAL_TIMEOUT)
    assert p.returncode == 0, p.stderr[-3000:]
    ev["final_forced_all_fail"] = {"verdict": forced["verdict"], "lines": W.line_status(forced["final_md"]),
                                   "differing": (forced.get("final_lines") or {}).get("sensitivity", {}).get("differing"),
                                   "final_md": forced["final_md"]}

    # --- change-log and commit-relation scenarios (each INVALID before any metric) --------- #
    ev["git_scenarios"] = git_scenarios(world, ev["commits"])

    # --- the weekly scorer run from a worktree pinned at X (as the runner does) ------------- #
    ev["repro_pinned_dirty"] = repro_pinned(world, ev["x"])

    # --- last (it pushes to the branch): a MISSED marker for a committed and sealed week ---- #
    ev["repro_marker_for_scored_week"] = repro_marker_for_scored_week(world, 2026, 13)
    return ev


def repro_marker_for_scored_week(world: W.World, season: int, week: int) -> dict[str, Any]:
    """A MISSED.json pushed by hand for a week that was committed on time and sealed."""
    wk = W.week_key(season, week)
    wt = world.commitments
    marker = {"format": "ffml.livetest.missed/2", "season": season, "week": week,
              "status": "MISSED: no commitment was pushed to this branch before the first kickoff",
              "reason": {"category": "technical_failure", "detail": "synthetic: written by hand (QA rehearsal)"},
              "note": "the branch already holds this week's commitment.json and scoring/<week>.sha256"}
    (wt / wk / "MISSED.json").write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    when = W.tuesday_after(2027, 9) + timedelta(days=1)
    W.git(wt, "add", f"{wk}/MISSED.json")
    W.git(wt, "commit", "-q", "-m", f"livetest {wk}: MISSED (written by hand)", env=W.git_dates(when))
    W.git(wt, "push", "-q", "origin", "refs/heads/livetest-2026:refs/heads/livetest-2026")
    W.git(wt, "fetch", "-q", "origin", "+refs/heads/livetest-2026:refs/remotes/origin/livetest-2026")
    out: dict[str, Any] = {"week": wk, "branch_has": [p for p in W.git(world.clone, "ls-tree", "-r", "--name-only",
                                                                       "origin/livetest-2026").splitlines()
                                                      if p.startswith(f"{wk}/") or p == f"scoring/{wk}.sha256"]}
    # (a) with the week's local sealed files in place: the marker is ignored, the overlap check passes
    out["integrity_with_records"] = [c for c in integrity(world)
                                     if c["name"] in ("window complete", "missed weeks (miss rule)",
                                                      "no week both scored and missed")]
    # (b) the week's local sealed files removed: the committed, sealed week becomes an excluded miss
    moved = world.root / "moved-sealed"
    moved.mkdir(exist_ok=True)
    for ext in ("parquet", "json", "sha256"):
        shutil.move(str(world.sealed_dir / f"{wk}.{ext}"), str(moved / f"{wk}.{ext}"))
    out["final"] = final(world, "--no-sensitivity", label="marker_for_scored_week")
    for ext in ("parquet", "json", "sha256"):
        shutil.move(str(moved / f"{wk}.{ext}"), str(world.sealed_dir / f"{wk}.{ext}"))
    return out


def git_scenarios(world: W.World, commits: dict[str, str]) -> dict[str, Any]:
    clone = world.clone
    out: dict[str, Any] = {}
    base = W.git(clone, "rev-parse", "HEAD")
    when = _t("2027-06-01T12:00:00+00:00")

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
        # the first entry written into the template's empty row instead of a new row (an edit)
        W.edit_changelog(clone, "| | | | | | | |", W.changelog_row(commits["F_fill"], W.PREREG_DOC,
                                                                     "freeze: commit field filled"))
        return {"fill_placeholder": W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: first entry in the template row",
                                                   when)}

    def log_plus_other_path():
        (clone / "docs" / "status" / "rehearsal-note.md").write_text("Synthetic note.\n", encoding="utf-8")
        W.append_changelog(clone, [W.changelog_row(commits["A_logged_change"], "scripts/", "duplicate entry",
                                                   date="2027-06-01")])
        return {"log_plus_doc": W.commit_paths(clone, [W.CHANGELOG_DOC, "docs/status/rehearsal-note.md"],
                                               "Change log entry plus a note in one commit", when)}

    def merge():
        W.git(clone, "checkout", "-q", "-b", "scn-merge-side")
        W.append_changelog(clone, [W.changelog_row(commits["A_logged_change"], "scripts/", "duplicate entry",
                                                   date="2027-06-01")])
        side = W.commit_paths(clone, [W.CHANGELOG_DOC], "Change log: append on a side branch", when)
        W.git(clone, "checkout", "-q", "scn-merge")
        W.git(clone, "merge", "-q", "--no-ff", "-m", "Merge the side branch", "scn-merge-side",
              env=W.git_dates(when + timedelta(minutes=5)))
        return {"side_append": side, "merge": W.git(clone, "rev-parse", "HEAD")}

    def frozen_path():
        (clone / "ffml" / "models" / "x.py").write_text('"""Synthetic change on a frozen path."""\n', encoding="utf-8")
        z = W.commit_paths(clone, ["ffml/models/x.py"], "Models: synthetic frozen-path change", when)
        W.append_changelog(clone, [W.changelog_row(z, "ffml/models/x.py", "frozen-path change", date="2027-06-01")])
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
    """Weekly scoring from a worktree pinned at X (runner score-week: cwd and editable install = the pinned tree)."""
    pinned = world.root / "ffml-livetest-code"
    W.git(world.clone, "worktree", "add", "-q", "--detach", str(pinned), x)
    env = world.env(PYTHONPATH=str(pinned), FFML_REHEARSAL_CLONE=str(pinned))
    scratch = world.root / "pinned-scratch-root"
    p, res = W.driver(world, "repro-pinned-dirty", {"x": x, "main_repo": str(world.clone), "scratch": str(scratch),
                                                    "season": 2026, "week": 10}, cwd=pinned, env=env)
    res["returncode"] = p.returncode
    res["stderr_tail"] = p.stderr[-1500:]
    # the final run from the pinned worktree: its pre-registration still has the placeholder commit field
    md = pinned / "reports" / "livetest" / "final.md"
    q = W.run(world, [W.sys.executable, "-c", W.SCORER_WRAPPER, "--final", "--no-sensitivity"], cwd=pinned, env=env,
              timeout=FINAL_TIMEOUT)
    res["final_at_x"] = {"proc": q.as_dict(), "verdict_stdout": W.verdict_lines(q.stdout)}
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        res["final_at_x"].update(final_md=text, verdict_md=W.verdict_lines(text), lines=W.line_status(text),
                                 checks=W.checks_table(text))
    return res


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
    if check_last_week:
        p, res = W.driver(world, "dim-game-unfinished", {"season": 2027, "week": 9})
        assert p.returncode == 0, p.stderr[-3000:]
        ev["last_week_not_final"] = {"snapshot": res, **final(world, label="last_week_not_final")}
        latest = world.data_dir / "processed" / "dim_game" / "LATEST"
        latest.write_text(res["old"] + "\n", encoding="utf-8")
    if equivalence:
        eq = {}
        for s, w in spec.cli_weeks:
            p, res = W.driver(world, "hook-part", {"season": s, "week": w,
                                                   "root": str(world.root / f"hook-scratch-{s}w{w:02d}")})
            assert p.returncode == 0, p.stderr[-3000:]
            cli_part = world.sealed_dir / f"{W.week_key(s, w)}.parquet"
            eq[W.week_key(s, w)] = {"cli_part_sha256": W.sha256_file(cli_part), "hook_part_sha256": res["part_sha256"]}
        ev["hook_cli_equivalence"] = eq
    ev["final"] = final(world, "--no-sensitivity", label="no_sensitivity")
    ev["integrity"] = integrity(world)
    return ev


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def run_all(root: Path, workers: int = 3, only: set[str] | None = None) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    results: dict[str, Any] = {}
    extra_threads: list[threading.Thread] = []
    lock = threading.Lock()

    def guarded(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        t0 = time.perf_counter()
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - every world reports its own failure
            res = {"world": name, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        res["seconds"] = round(time.perf_counter() - t0, 1)
        with lock:
            results[name] = res

    def spawn(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        t = threading.Thread(target=guarded, args=(name, fn), name=name)
        t.start()
        extra_threads.append(t)

    jobs: dict[str, Callable[[], dict[str, Any]]] = {
        "A_pass": lambda: pipeline_pass(root, spawn),
        "B_qb_narrow": lambda: pipeline_fail(root, "B", W.WorldSpec("B_qb_narrow", qb_sigma_factor=0.8,
                                                                     weekly_sensitivity=False), forced="all-pass"),
        "B2_qb_narrow_repeat": lambda: pipeline_fail(root, "B2", W.WorldSpec("B_qb_narrow", qb_sigma_factor=0.8,
                                                                             weekly_sensitivity=False)),
        "C_accuracy_tie": lambda: pipeline_fail(root, "C", W.WorldSpec("C_accuracy_tie",
                                                                       tie_baseline=("prior_season", "WR"),
                                                                       weekly_sensitivity=False)),
        "D1_one_miss": lambda: pipeline_misses(root, "D1", W.WorldSpec(
            "D1_one_miss", missed=[(2026, 12)], cli_weeks=[(2026, 10), (2027, 1)], weekly_sensitivity=False),
            equivalence=True),
        "D2_two_misses": lambda: pipeline_misses(root, "D2", W.WorldSpec(
            "D2_two_misses", missed=[(2026, 12), (2027, 9)], weekly_sensitivity=False), check_last_week=True),
        "D3_three_misses": lambda: pipeline_misses(root, "D3", W.WorldSpec(
            "D3_three_misses", missed=[(2026, 12), (2026, 16), (2027, 3)], weekly_sensitivity=False)),
    }
    order = ["A_pass", "D1_one_miss", "B_qb_narrow", "C_accuracy_tie", "D2_two_misses", "B2_qb_narrow_repeat",
             "D3_three_misses"]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(guarded, n, jobs[n]) for n in order if only is None or n in only]
        for f in futures:
            f.result()
    for t in extra_threads:
        t.join()
    return results


def evidence_json(results: dict[str, Any]) -> str:
    def strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k not in ("world_obj",)}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj

    return json.dumps(strip(results), indent=1, default=str)


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
