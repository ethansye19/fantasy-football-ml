"""Driver for the live-test rehearsal, run as ``python _rehearsal_driver.py <command> <json> <out.json>``.

It always runs with ``cwd`` and ``PYTHONPATH`` set to the world's clone (or the
pinned worktree for ``repro-pinned-dirty``) and refuses to start unless
``ffml`` and the runner scripts resolve inside that directory. Everything it
writes goes to the world's synthetic directories.

Injection points used here (documented in reports/livetest_final_rehearsal.md):

* runner side: ``Deps`` with a simulated clock, a no-op alert, no live schedule,
  and a simulated GitHub view (there is no GitHub in the rehearsal; the local
  bare remote is named a GitHub repository through ``core.parse_github_remote``);
* scorer side, weekly: ``score_week(..., n_boot=..., sensitivity_n_boot=...)``
  with every other argument wired exactly as ``livetest_score.main`` wires it
  (the weekly sealed metrics are never read by the final; the sealed parquet
  part does not depend on the draw count);
* scorer side, final: ``final_evaluation(sensitivity_fn=...)`` only for the
  forced-disagreement check of the report-only sensitivity rule.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

CLONE = Path(os.environ["FFML_REHEARSAL_CLONE"]).resolve()
sys.path.insert(0, str(CLONE / "scripts"))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))

import ffml  # noqa: E402

_FFML = Path(ffml.__file__).resolve()
if CLONE not in _FFML.parents:
    raise SystemExit(f"refusing to run: ffml imported from {_FFML}, not from {CLONE}")

import livetest_core as core  # noqa: E402
import livetest_week as runner  # noqa: E402

if CLONE not in Path(runner.__file__).resolve().parents:
    raise SystemExit(f"refusing to run: runner imported from {runner.__file__}, not from {CLONE}")

import _rehearsal_world as W  # noqa: E402

from ffml.config import get_settings  # noqa: E402
from ffml.eval import livetest_score as LS  # noqa: E402

SPEC = W.WorldSpec.from_json(os.environ["FFML_REHEARSAL_SPEC"])
WORLD = W.World(SPEC, CLONE.parent)


class SimClock:
    """Simulated UTC time: a fixed start plus the real time elapsed since it was set."""

    def __init__(self) -> None:
        self.start = datetime.now(UTC)
        self.t0 = time.monotonic()

    def set(self, when: datetime) -> None:
        self.start, self.t0 = when, time.monotonic()
        stamp = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")
        os.environ["GIT_AUTHOR_DATE"] = stamp
        os.environ["GIT_COMMITTER_DATE"] = stamp

    def __call__(self) -> datetime:
        return self.start + timedelta(seconds=time.monotonic() - self.t0)


CLOCK = SimClock()
_ACTIVITY = {"n": 0, "before": None}


def fake_github_view(owner, repo, branch, sha, *, sleep=None, **_kw):
    """What GitHub's activity API would return for the push just made (server time = the simulated clock)."""
    _ACTIVITY["n"] += 1
    stamp = CLOCK().astimezone(UTC).replace(microsecond=0)
    view = {"owner": owner, "repo": repo, "branch": branch, "sha": sha, "errors": [],
            "commit": {"sha": sha, "html_url": f"https://github.com/{owner}/{repo}/commit/{sha}",
                       "committer_date_client": stamp.isoformat()},
            "ref_sha": sha,
            "push_activity": {"id": 9_000_000 + _ACTIVITY["n"], "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "activity_type": "push", "actor": "synthetic-runner", "before": _ACTIVITY["before"],
                              "after": sha},
            "simulated": "QA rehearsal: no GitHub; this is the shape github_view returns"}
    _ACTIVITY["before"] = sha
    return view


core.parse_github_remote = lambda url: ("synthetic-owner", "ffml-rehearsal") if url else None


def deps() -> runner.Deps:
    return runner.Deps(clock=CLOCK, alert=lambda *a, **k: None, github_view=fake_github_view,
                       live_schedule=None, sleep=lambda s: None)


def runner_main(argv: list[str]) -> int:
    return runner.main(argv, deps())


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_probe(payload):
    from ffml.models import chain_v2

    s = get_settings()
    return {
        "ffml_file": str(_FFML), "runner_file": str(Path(runner.__file__).resolve()),
        "project_root": str(LS.PROJECT_ROOT), "default_worktree": str(LS.DEFAULT_WORKTREE),
        "runner_repo_root": str(core.REPO_ROOT), "runner_commitments_worktree": str(core.DEFAULT_COMMITMENTS_WORKTREE),
        "data_dir": str(s.data_dir), "artifacts_dir": str(s.artifacts_dir), "reports_dir": str(s.reports_dir),
        "holdout_unlock": bool(s.holdout_unlock), "first_season": s.first_season, "holdout_season": s.holdout_season,
        "pins": chain_v2.check_pins(), "quantile_repr": chain_v2.QUANTILE_CALIBRATOR_REPR,
        "isotonic_repr": chain_v2.ISOTONIC_CALIBRATOR_REPR, "sensitivity_n_boot": LS.SENSITIVITY_N_BOOT,
        "spec_n_boot": LS.H.SPEC_N_BOOT, "python": sys.executable,
    }


def cmd_build(payload):
    return W.build_world(WORLD)


def weekly_hook(season: int, week: int, n_boot: int, sens_n_boot: int, weekly_sensitivity: bool,
                root: Path | None = None, reports_dir: Path | None = None):
    """``livetest_score.main`` for ``--week``, line for line, with the draw counts as parameters."""
    source = LS.BranchSource(Path(LS.DEFAULT_WORKTREE))
    prereg = LS.load_prereg(None)
    rec_path = LS.default_push_record(season, week)
    push_record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else None
    dim_game = LS.load_dim_game([season])
    baselines = LS.ensure_comparators(season, week, dim_game, run=LS.COMPARATORS_RUN)
    consensus = LS.consensus_rows(season, week)
    kw = {} if root is None else {"root": root}
    if reports_dir is not None:
        kw["reports_dir"] = reports_dir
    rec, out = LS.score_week(season, week, source=source, dim_game=dim_game, push_record=push_record,
                             roots=[LS.PROJECT_ROOT, get_settings().data_dir.parent], baselines=baselines,
                             consensus=consensus, prereg=prereg, allow_rehearsal=False,
                             dim_game_id=LS.dim_game_snapshot_id(), snapshot_ids=LS.processed_snapshot_ids(),
                             weekly_sensitivity=weekly_sensitivity, n_boot=n_boot, sensitivity_n_boot=sens_n_boot,
                             **kw)
    failed = [c.name for c in rec.checks if not c.ok]
    return {"checks": len(rec.checks), "failed": failed, "report": str(out),
            "message": f"sealed {W.week_key(season, week)}: {len(rec.checks) - len(failed)} of {len(rec.checks)} "
                       f"checks pass" + (f"; failed: {failed}" if failed else "")}


def cmd_weekly(payload):
    """Run the week-by-week plan: runner init, build+push, misses, weekly scoring, seal."""
    x = payload["x"]
    results = []
    for step in payload["steps"]:
        op = step["op"]
        t0 = time.perf_counter()
        res: dict = {"op": op, **{k: v for k, v in step.items() if k != "op"}}
        try:
            if op == "init":
                CLOCK.set(datetime.fromisoformat(step["at"]))
                argv = ["init", "--season", str(step["season"]), "--weeks", step["weeks"],
                        "--note", "QA rehearsal (synthetic)"] + (["--force"] if step.get("force") else [])
                res["exit"] = runner_main(argv)
            elif op == "push":
                s, w = step["season"], step["week"]
                CLOCK.set(W.created_time(s, w))
                path = W.build_commitment(WORLD, s, w, x)
                res["commitment_sha256"] = W.sha256_file(path)
                CLOCK.set(W.push_time(s, w))
                res["exit"] = runner_main(["run", "--season", str(s), "--week", str(w), "--skip-refresh",
                                           "--no-live-schedule"])
                rec = read_json(get_settings().data_dir / "livetest" / "runner" / W.week_key(s, w) / "push_record.json")
                res["push_record"] = {k: (rec or {}).get(k) for k in ("commit_sha", "external_timestamp",
                                                                        "external_timestamp_source", "on_time")}
                verify = read_json(get_settings().data_dir / "livetest" / "runner" / W.week_key(s, w) / "verify.json")
                res["post_push_verify_ok"] = (verify or {}).get("ok")
                res["ffml_livetest_verify_rc"] = ((verify or {}).get("ffml_livetest_verify") or {}).get("returncode")
            elif op == "misses":
                CLOCK.set(datetime.fromisoformat(step["at"]))
                res["exit"] = runner_main(["run", "--skip-refresh", "--no-live-schedule"])
            elif op == "score-hook":
                CLOCK.set(W.tuesday_after(step["season"], step["week"]))
                res.update(weekly_hook(step["season"], step["week"], step.get("n_boot", 5),
                                       step.get("sens_n_boot", 5), step.get("weekly_sensitivity", False)))
                res["exit"] = 0
            elif op == "score-cli":
                CLOCK.set(W.tuesday_after(step["season"], step["week"]))
                before = W.tree_listing(get_settings().data_dir)
                rep_before = W.tree_listing(CLONE / "reports")
                art_before = W.tree_listing(get_settings().artifacts_dir)
                p = subprocess.run([sys.executable, "-c", W.SCORER_WRAPPER, "--week", W.week_key(step["season"],
                                    step["week"]), *step.get("args", [])], cwd=CLONE, env=dict(os.environ),
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
                after = W.tree_listing(get_settings().data_dir)
                rep_after = W.tree_listing(CLONE / "reports")
                art_after = W.tree_listing(get_settings().artifacts_dir)
                res.update(exit=p.returncode, stdout=p.stdout[-3000:], stderr=p.stderr[-3000:],
                           data_written=sorted(k for k in after if before.get(k) != after[k]),
                           reports_written=sorted(k for k in rep_after if rep_before.get(k) != rep_after[k]),
                           artifacts_written=sorted(k for k in art_after if art_before.get(k) != art_after[k]),
                           settings_reports=sorted(W.tree_listing(WORLD.settings_reports_dir)))
            elif op == "seal":
                CLOCK.set(W.tuesday_after(step["season"], step["week"]) + timedelta(minutes=30))
                res["exit"] = runner_main(["seal", "--season", str(step["season"]), "--week", str(step["week"]),
                                           "--no-live-schedule"])
            else:
                raise ValueError(f"unknown step {op!r}")
        except SystemExit as exc:  # argparse errors
            res["exit"] = exc.code
        except Exception as exc:  # noqa: BLE001 - recorded, the test asserts
            res["exit"] = "error"
            res["error"] = f"{type(exc).__name__}: {exc}"
            res["traceback"] = traceback.format_exc()[-3000:]
        res["seconds"] = round(time.perf_counter() - t0, 1)
        results.append(res)
        if res.get("exit") not in (0, None) and step.get("required", True):
            break
    return {"steps": results}


def cmd_final_forced(payload):
    """The final with a sensitivity check forced to disagree on every line (report-only rule)."""
    mode = payload["mode"]
    prereg = LS.load_prereg(None)

    def forced(frame, consensus):
        sc = LS.score_frames(frame, consensus, n_boot=payload.get("n_boot", 5))
        sc.results = [LS.LineResult(r.line, mode == "all-pass", f"forced {mode} by the rehearsal") for r in sc.results]
        return sc

    verdict, out = LS.final_evaluation(source=LS.BranchSource(Path(LS.DEFAULT_WORKTREE)), dim_game=LS.load_dim_game(),
                                       prereg=prereg, consensus=LS._all_consensus(prereg), sensitivity=True,
                                       sensitivity_fn=forced)
    sealed = read_json(LS.scoring_root() / LS.SEALED_DIR / "final_lines.json")
    return {"verdict": verdict, "final_md": out.read_text(encoding="utf-8"), "final_lines": sealed}


def cmd_hook_part(payload):
    """Score one week with the hook into a scratch root; the part's sha256 (equivalence with the CLI part)."""
    scratch = Path(payload["root"])
    res = weekly_hook(payload["season"], payload["week"], 5, 5, False, root=scratch, reports_dir=scratch / "reports")
    part = LS.part_path(scratch, payload["season"], payload["week"])
    return {**res, "part_sha256": W.sha256_file(part)}


def cmd_repro_worktree_trust(payload):
    """Consistent local tampering of a sealed part plus the commitments worktree's uncommitted copy of the record."""
    import polars as pl

    s, w = payload["season"], payload["week"]
    wk = W.week_key(s, w)
    root = LS.scoring_root()
    part, rec_json, local_seal = LS.part_path(root, s, w), LS.record_path(root, s, w), LS.seal_path(root, s, w)
    branch_file = Path(LS.DEFAULT_WORKTREE) / LS.SEALED_BRANCH_DIR / f"{wk}.sha256"
    backup = {p: p.read_bytes() for p in (part, rec_json, local_seal, branch_file)}
    window = LS.prereg_window(LS.load_prereg(None).data)
    out = {}
    try:
        frame = pl.read_parquet(part)
        frame.with_columns((pl.col("actual") + 5.0).alias("actual")).write_parquet(part)  # every outcome changed
        rec = json.loads(rec_json.read_text(encoding="utf-8"))
        rec["part_sha256"] = W.sha256_file(part)
        rec_json.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
        text = f"{W.sha256_file(part)}  {part.name}\n{W.sha256_file(rec_json)}  {rec_json.name}\n"
        local_seal.write_text(text, encoding="utf-8")
        branch_file.write_text(text, encoding="utf-8")  # the worktree file only; nothing is committed
        status = subprocess.run(["git", "-C", str(LS.DEFAULT_WORKTREE), "status", "--porcelain"], capture_output=True,
                                text=True).stdout.strip()
        wt = LS.verify_seals(LS.BranchSource(Path(LS.DEFAULT_WORKTREE)), root, [(s, w)])
        ref = LS.verify_seals(LS.BranchSource(None), root, [(s, w)])
        integ = LS.livetest_integrity_checks(LS.load_prereg(None), {(s, w): rec}, window, {}, root=root)
        sealed_part = [c for c in integ if c.name == f"{wk} sealed part"]
        out = {"worktree_status": status,
               "worktree_mode": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in wt],
               "no_worktree_mode": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in ref],
               "sealed_part_check": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in sealed_part]}
    finally:
        for p, data in backup.items():
            p.write_bytes(data)
    out["restored_status"] = subprocess.run(["git", "-C", str(LS.DEFAULT_WORKTREE), "status", "--porcelain"],
                                            capture_output=True, text=True).stdout.strip()
    return out


def cmd_repro_pinned_dirty(payload):
    """The weekly scorer run from a worktree pinned at X, then the runner's own pin check."""
    pinned = CLONE  # this driver runs with FFML_REHEARSAL_CLONE = the pinned worktree
    x = payload["x"]
    scratch = Path(payload["scratch"])

    def status():
        return subprocess.run(["git", "-C", str(pinned), "status", "--porcelain=v1", "--untracked-files=all"],
                              capture_output=True, text=True).stdout.strip()

    def pin():
        try:
            r = core.verify_code_pin(pinned, x, Path(sys.executable), main_repo=Path(payload["main_repo"]),
                                     check_env=False, require_editable=False)
            return {"ok": True, "ffml_file": r.ffml_file}
        except core.PinError as exc:
            return {"ok": False, "error": str(exc)}

    out = {"status_before": status(), "pin_before": pin(), "project_root": str(LS.PROJECT_ROOT)}
    res = weekly_hook(payload["season"], payload["week"], 5, 5, False, root=scratch)  # reports_dir: the default
    out.update(weekly=res, status_after=status(), pin_after=pin())
    return out


def cmd_integrity(payload):
    """The final's integrity checks for the current state, without metrics (for scenario tables)."""
    prereg = LS.load_prereg(None)
    window = LS.prereg_window(prereg.data)
    root = LS.scoring_root()
    records = LS.load_records(root, window)
    source = LS.BranchSource(Path(LS.DEFAULT_WORKTREE))
    misses = {w: m for w in window if w not in records and (m := LS.missed_marker(source, *w)) is not None}
    checks = LS.livetest_integrity_checks(prereg, records, window, misses, root=root)
    return {"checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks]}


def cmd_seals(payload):
    """``verify_seals`` for every sealed week, reading the branch from the worktree and from git only."""
    prereg = LS.load_prereg(None)
    root = LS.scoring_root()
    weeks = sorted(LS.load_records(root, LS.prereg_window(prereg.data)))
    out = {}
    for mode, source in (("worktree", LS.BranchSource(Path(LS.DEFAULT_WORKTREE))), ("git_only", LS.BranchSource(None))):
        out[mode] = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in LS.verify_seals(source, root, weeks)]
    return out


def cmd_dim_game_unfinished(payload):
    """A new dim_game snapshot in which one game of the given week is not final (LATEST moves to it)."""
    import polars as pl

    from ffml.data.store import SnapshotStore

    store = SnapshotStore(get_settings().processed_dir)
    old = store.latest_id("dim_game")
    games = store.read("dim_game")
    s, w = payload["season"], payload["week"]
    target = games.filter((pl.col("season") == s) & (pl.col("week") == w))["game_id"].sort().to_list()[-1]
    games = games.with_columns(pl.when(pl.col("game_id") == target).then(pl.lit("scheduled"))
                               .otherwise(pl.col("status")).alias("status"))
    new = store.write_snapshot("dim_game", {int(x): games.filter(pl.col("season") == x)
                                            for x in sorted(games["season"].unique().to_list())},
                               meta={"synthetic": True, "note": f"{target} not final"})
    return {"old": old, "new": new, "game": target}


COMMANDS = {"probe": cmd_probe, "build": cmd_build, "weekly": cmd_weekly, "final-forced": cmd_final_forced,
            "hook-part": cmd_hook_part, "repro-worktree-trust": cmd_repro_worktree_trust,
            "repro-pinned-dirty": cmd_repro_pinned_dirty, "integrity": cmd_integrity,
            "dim-game-unfinished": cmd_dim_game_unfinished, "seals": cmd_seals}


def main() -> int:
    command, payload, out = sys.argv[1], json.loads(sys.argv[2]), Path(sys.argv[3])
    result = COMMANDS[command](payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
