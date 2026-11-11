"""Driver for the live-test rehearsal, run as ``python _rehearsal_driver.py <command> <json> <out.json>``.

It always runs with ``cwd`` and ``PYTHONPATH`` set to the world's clone (or the
pinned worktree for ``repro-pinned-dirty``) and refuses to start unless
``ffml`` and the runner scripts resolve inside that directory. Everything it
writes goes to the world's synthetic directories.

Injection points used here (documented in reports/livetest_final_rehearsal.md):

* runner side: ``Deps`` with a simulated clock, a recording alert (nothing is
  sent), no live schedule, a simulated GitHub view (there is no GitHub in the
  rehearsal; the local bare remote is named a GitHub repository through
  ``core.parse_github_remote``), and ``Deps.run`` wrapped so that (a) the
  build command ``ffml livetest build-week`` writes a synthetic commitment with
  the builder's helpers, and (b) every scorer command
  (``-m ffml.eval.livetest_score``: ``--prebuild-check``, ``--data-cutoff``)
  goes through the clone-asserting wrapper with the simulated clock; every
  other command runs unchanged;
* scorer side, weekly: ``score_week(..., n_boot=..., sensitivity_n_boot=...)``
  with every other argument wired exactly as ``livetest_score.main`` wires
  ``--week`` (``plan_week``: data cutoff, pins, pinned stores, D-121 exclusions); the weekly sealed
  metrics are never read by the final and the sealed parquet parts do not
  depend on the draw count;
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
ALERTS: list[dict] = []
BUILDS: list[dict] = []
SCORER_CALLS: list[dict] = []


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


def record_alert(kind, message, **kw):
    ALERTS.append({"kind": kind, "message": message[:600], "severity": kw.get("severity"),
                   "at": CLOCK().isoformat(timespec="seconds")})
    return None


def run_hook(cmd, *args, **kw):
    """``Deps.run``: the build is synthetic; scorer commands go through the clone-asserting wrapper."""
    cmd = [str(c) for c in cmd]
    if len(cmd) >= 3 and cmd[1:3] == ["-m", "ffml.cli"] and "build-week" in cmd:
        season, week = int(cmd[cmd.index("--season") + 1]), int(cmd[cmd.index("--week") + 1])
        created = CLOCK.start + timedelta(minutes=5)  # a build takes five simulated minutes
        path = W.build_commitment(WORLD, season, week, created)
        CLOCK.set(created)
        BUILDS.append({"week": W.week_key(season, week), "created_at": created.isoformat(),
                       "commitment_sha256": W.sha256_file(path)})
        return subprocess.CompletedProcess(cmd, 0, f"committed {path}\n", "")
    if len(cmd) >= 3 and cmd[1:3] == ["-m", "ffml.eval.livetest_score"]:
        env = dict(kw.get("env") or os.environ)
        env["FFML_REHEARSAL_CLONE"] = str(CLONE)
        env["FFML_REHEARSAL_NOW"] = CLOCK().isoformat()
        kw["env"] = env
        # the runner asks for the weekly 5,000-draw sensitivity; the rehearsal drops it to save hours (world A
        # runs the weekly sensitivity through the hook with 5 draws)
        dropped = [a for a in cmd[3:] if a == "--weekly-sensitivity"]
        cmd = [cmd[0], "-c", W.SCORER_WRAPPER, *[a for a in cmd[3:] if a != "--weekly-sensitivity"]]
        proc = subprocess.run(cmd, *args, **kw)
        SCORER_CALLS.append({"args": cmd[3:], "dropped": dropped, "returncode": proc.returncode,
                             "at": env["FFML_REHEARSAL_NOW"], "stdout": (proc.stdout or "")[-600:],
                             "stderr": (proc.stderr or "")[-600:]})
        return proc
    return subprocess.run(cmd, *args, **kw)


def deps() -> runner.Deps:
    return runner.Deps(clock=CLOCK, alert=record_alert, github_view=fake_github_view, live_schedule=None,
                       sleep=lambda s: None, run=run_hook)


def runner_main(argv: list[str]) -> int:
    return runner.main(argv, deps())


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


_TRUTH = {}


def truth():
    import polars as pl

    if "t" not in _TRUTH:
        _TRUTH["t"] = pl.read_parquet(get_settings().data_dir / "rehearsal_world" / "truth.parquet")
    return _TRUTH["t"]


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --------------------------------------------------------------------------- #
# The weekly scorer wired as ``livetest_score.main --week``
# --------------------------------------------------------------------------- #


def weekly_hook(season: int, week: int, n_boot: int, sens_n_boot: int, weekly_sensitivity: bool, now: datetime,
                root: Path | None = None, reports_dir: Path | None = None, record=None):
    """``livetest_score.main`` for ``--week``, step for step (``plan_week``, D-116 A3 and D-121), with the draw
    counts as parameters. ``record`` defaults to the week's sealed JSON, as in ``main`` (a rescore)."""
    wk = W.week_key(season, week)
    if record is None:
        record = LS._week_record(season, week)
    try:
        plan = LS.plan_week(season, week, LS.season_dim_game(season), now=now, record=record)
    except LS.RetryLater as exc:
        return {"exit": 4, "message": f"retry later: {exc}"}
    except LS.ScoreRefused as exc:
        return {"exit": 3, "message": f"refused: {exc}"}
    pins, stores, dim_game, cutoff = plan.pins, plan.stores, plan.dim_game, plan.cutoff
    rec_path = LS.default_push_record(season, week)
    push_record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else None
    source = LS.BranchSource(Path(LS.DEFAULT_WORKTREE))
    try:
        baselines = LS.ensure_comparators(season, week, dim_game, run=LS.COMPARATORS_RUN,
                                          actuals_fn=lambda s_: LS.load_actuals(s_, stores["processed"]),
                                          excluded_games=plan.excluded, now=now, root=root)
    except LS.ScoreRefused as exc:
        return {"exit": 3, "message": f"refused: {exc}"}
    consensus = None
    if stores.get("restricted") is not None:
        consensus = LS.consensus_rows(season, week, store=stores["processed"], restricted_store=stores["restricted"])
    kw = {} if root is None else {"root": root}
    if reports_dir is not None:
        kw["reports_dir"] = reports_dir
    saved = LS.utcnow
    LS.utcnow = lambda: now
    try:
        rec, out = LS.score_week(
            season, week, source=source, dim_game=dim_game, push_record=push_record,
            roots=[LS.PROJECT_ROOT, get_settings().data_dir.parent], baselines=baselines, consensus=consensus,
            prereg=LS.load_prereg(None), allow_rehearsal=False,
            outcomes=lambda raw, s_: LS.harness_outcomes(raw, s_, stores["processed"]),
            dim_game_id=pins.get("processed/dim_game"), snapshot_ids=LS.processed_snapshot_ids(),
            weekly_sensitivity=weekly_sensitivity, data_cutoff=cutoff, data_snapshots=pins, n_boot=n_boot,
            sensitivity_n_boot=sens_n_boot, excluded_games=plan.excluded, pin_from=plan.pin_from, **kw)
    except LS.ScoreRefused as exc:
        return {"exit": 3, "message": f"refused: {exc}"}
    finally:
        LS.utcnow = saved
    failed = [c.name for c in rec.checks if not c.ok]
    return {"exit": 0, "checks": len(rec.checks), "failed": failed, "report": str(out), "pins": pins,
            "data_cutoff": LS.iso(cutoff), "pin_from": LS.iso(plan.pin_from), "excluded": list(plan.excluded),
            "message": f"sealed {wk}: {len(rec.checks) - len(failed)} of {len(rec.checks)} checks pass"
                       + (f"; failed: {failed}" if failed else "")}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def sealed_on_branch() -> list[str]:
    """The weeks with a sealed-hash record on the fake remote branch."""
    names = W.git(WORLD.remote, "ls-tree", "-r", "--name-only", "refs/heads/livetest-2026", check=False).splitlines()
    return sorted(n.split("/", 1)[0] for n in names if n.endswith("/" + core.SEALED_NAME))


def runner_log_events(sizes: dict[Path, int], step: str) -> list[dict]:
    """The runner log events of one step written since ``sizes`` was taken."""
    logs = get_settings().data_dir / "livetest" / "logs"
    keep = ("ts", "status", "week", "results", "not_final", "exclusion_after", "pin_point", "returncode", "message")
    out = []
    for f in sorted(logs.glob("*.jsonl")) if logs.is_dir() else []:
        with f.open("rb") as fh:
            fh.seek(sizes.get(f, 0))
            for line in fh.read().decode("utf-8").splitlines():
                e = json.loads(line)
                if e.get("step") == step:
                    out.append({k: v for k, v in e.items() if k in keep})
    return out


def log_sizes() -> dict[Path, int]:
    logs = get_settings().data_dir / "livetest" / "logs"
    return {f: f.stat().st_size for f in logs.glob("*.jsonl")} if logs.is_dir() else {}


def cmd_probe(payload):
    from ffml.models import chain_v2

    s = get_settings()
    dg = LS.load_dim_game([2026]) if (s.processed_dir / "dim_game" / "LATEST").exists() else None
    cut = {W.week_key(2026, w): LS.iso(LS.data_cutoff(dg, 2026, w)) for w in W.SEASON_WINDOW[2026]} \
        if dg is not None else {}
    return {
        "ffml_file": str(_FFML), "runner_file": str(Path(runner.__file__).resolve()),
        "project_root": str(LS.PROJECT_ROOT), "default_worktree": str(LS.DEFAULT_WORKTREE),
        "runner_repo_root": str(core.REPO_ROOT), "runner_commitments_worktree": str(core.DEFAULT_COMMITMENTS_WORKTREE),
        "data_dir": str(s.data_dir), "artifacts_dir": str(s.artifacts_dir), "reports_dir": str(s.reports_dir),
        "default_reports_dir": str(LS.default_reports_dir()),
        "holdout_unlock": bool(s.holdout_unlock), "first_season": s.first_season, "holdout_season": s.holdout_season,
        "pins": chain_v2.check_pins(), "sensitivity_n_boot": LS.SENSITIVITY_N_BOOT, "spec_n_boot": LS.H.SPEC_N_BOOT,
        "bootstrap_line_frames_only": LS.BOOTSTRAP_LINE_FRAMES_ONLY, "python": sys.executable,
        "scorer_cutoffs_2026": cut, "world_cutoffs_2026": {W.week_key(2026, w): W.data_cutoff(2026, w).isoformat()
                                                          for w in W.SEASON_WINDOW[2026]},
    }


def cmd_build(payload):
    return W.build_world(WORLD)


def cmd_weekly(payload):
    """The season in simulated time: refreshes, runner builds and pushes, misses, weekly scoring, seals, git steps."""
    labels: dict[str, str] = dict(payload.get("labels") or {})
    results = []
    for step in payload["steps"]:
        op = step["op"]
        t0 = time.perf_counter()
        a0 = len(ALERTS)
        s0 = len(SCORER_CALLS)
        res: dict = {"op": op, **{k: v for k, v in step.items() if k != "op"}}
        try:
            if op == "init":
                CLOCK.set(_t(step["at"]))
                argv = ["init", "--season", str(step["season"]), "--weeks", step["weeks"],
                        "--note", "QA rehearsal (synthetic)"] + (["--force"] if step.get("force") else [])
                res["exit"] = runner_main(argv)
            elif op == "refresh":
                res["snapshots"] = W.write_snapshot_as_of(truth(), _t(step["at"]), SPEC)
                res["exit"] = 0
            elif op == "push":
                s, w = step["season"], step["week"]
                attempts = []
                for at in step.get("attempts") or [W.attempt_time(s, w).isoformat()]:
                    CLOCK.set(_t(at))
                    b0, al0 = len(BUILDS), len(ALERTS)
                    code = runner_main(["run", "--season", str(s), "--week", str(w), "--skip-refresh",
                                        "--no-live-schedule"])
                    attempts.append({"at": at, "exit": code, "built": BUILDS[b0:], "alerts": ALERTS[al0:]})
                    rec = read_json(get_settings().data_dir / "livetest" / "runner" / W.week_key(s, w) / "push_record.json")
                    if code == 0 and rec:
                        break
                res["attempts"] = attempts
                res["exit"] = attempts[-1]["exit"]
                rec = read_json(get_settings().data_dir / "livetest" / "runner" / W.week_key(s, w) / "push_record.json")
                res["push_record"] = {k: (rec or {}).get(k) for k in ("commit_sha", "external_timestamp",
                                                                        "external_timestamp_source", "on_time")}
                verify = read_json(get_settings().data_dir / "livetest" / "runner" / W.week_key(s, w) / "verify.json")
                res["post_push_verify_ok"] = (verify or {}).get("ok")
                res["ffml_livetest_verify_rc"] = ((verify or {}).get("ffml_livetest_verify") or {}).get("returncode")
                built = [b for a in attempts for b in a["built"]]
                res["commitment_sha256"] = built[-1]["commitment_sha256"] if built else None
            elif op == "misses":
                CLOCK.set(_t(step["at"]))
                res["exit"] = runner_main(["run", "--skip-refresh", "--no-live-schedule"])
            elif op == "score-hook":
                now = _t(step["at"])
                CLOCK.set(now)
                res.update(weekly_hook(step["season"], step["week"], step.get("n_boot", 5), step.get("sens_n_boot", 5),
                                       step.get("weekly_sensitivity", False), now))
            elif op in ("score-cli", "cli"):
                now = _t(step["at"])
                CLOCK.set(now)
                args = step.get("args") or ["--week", W.week_key(step["season"], step["week"])]
                before = W.tree_listing(get_settings().data_dir)
                rep_before = W.tree_listing(WORLD.settings_reports_dir)
                art_before = W.tree_listing(get_settings().artifacts_dir)
                clone_before = W.tree_listing(CLONE / "reports")
                env = dict(os.environ)
                env["FFML_REHEARSAL_NOW"] = now.isoformat()
                p = subprocess.run([sys.executable, "-c", W.SCORER_WRAPPER, *args], cwd=CLONE, env=env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
                after = W.tree_listing(get_settings().data_dir)
                rep_after = W.tree_listing(WORLD.settings_reports_dir)
                art_after = W.tree_listing(get_settings().artifacts_dir)
                clone_after = W.tree_listing(CLONE / "reports")
                res.update(exit=p.returncode, stdout=p.stdout[-3000:], stderr=p.stderr[-3000:],
                           data_written=sorted(k for k in after if before.get(k) != after[k]),
                           reports_written=sorted(k for k in rep_after if rep_before.get(k) != rep_after[k]),
                           artifacts_written=sorted(k for k in art_after if art_before.get(k) != art_after[k]),
                           clone_reports_written=sorted(k for k in clone_after if clone_before.get(k) != clone_after[k]))
                if step.get("expect_exit") is not None and p.returncode == step["expect_exit"]:
                    res["exit_as_expected"] = True
            elif op == "score-week":  # the runner's Friday job: every due, unsealed week (A3, D-121)
                CLOCK.set(_t(step["at"]))
                sizes = log_sizes()
                res["exit"] = runner_main(["score-week", "--skip-refresh", "--no-live-schedule"])
                res["score_events"] = runner_log_events(sizes, "score")
                res["sealed_on_branch"] = sealed_on_branch()
            elif op == "seal":
                CLOCK.set(_t(step["at"]))
                res["exit"] = runner_main(["seal", "--season", str(step["season"]), "--week", str(step["week"]),
                                           "--no-live-schedule"])
            elif op == "git-commit":
                at = _t(step["at"])
                p = CLONE / step["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(step.get("content", '"""Synthetic post-freeze change (QA rehearsal)."""\n'), encoding="utf-8")
                labels[step["label"] + ":parent"] = W.git(CLONE, "rev-parse", "HEAD")
                labels[step["label"]] = W.commit_paths(CLONE, [step["path"]], step.get("message", step["label"]), at)
                res.update(exit=0, sha=labels[step["label"]])
            elif op == "git-log":
                at = _t(step["at"])
                sha = labels[step["label"]]
                W.append_changelog(CLONE, [W.changelog_row(sha, step.get("paths", "see commit"),
                                                           step.get("reason", "rehearsal change"),
                                                           first_week=step.get("first_week", ""),
                                                           date=at.date().isoformat())])
                labels["log:" + step["label"]] = W.commit_paths(CLONE, [W.CHANGELOG_DOC],
                                                                f"Change log: entry for {step['label']}", at)
                res.update(exit=0, sha=labels["log:" + step["label"]])
            elif op == "git-reset":
                W.git(CLONE, "reset", "-q", "--hard", labels[step["to"]])
                res.update(exit=0, head=W.git(CLONE, "rev-parse", "HEAD"))
            else:
                raise ValueError(f"unknown step {op!r}")
        except SystemExit as exc:  # argparse errors
            res["exit"] = exc.code
        except Exception as exc:  # noqa: BLE001 - recorded, the test asserts
            res["exit"] = "error"
            res["error"] = f"{type(exc).__name__}: {exc}"
            res["traceback"] = traceback.format_exc()[-3000:]
        res["alerts"] = ALERTS[a0:]
        res["scorer_calls"] = SCORER_CALLS[s0:]
        res["seconds"] = round(time.perf_counter() - t0, 1)
        results.append(res)
        if step.get("tag"):  # streamed at once, so an interrupted run keeps it
            W.stream(SPEC.name, f"step:{step['tag']}", {k: v for k, v in res.items() if k != "snapshots"})
        if res.get("exit") not in (0, None) and step.get("required", True):
            break
    return {"steps": results, "labels": labels}


def cmd_final_forced(payload):
    """The final (as ``main --final`` wires it) with a sensitivity check forced to disagree on every line."""
    mode = payload["mode"]
    prereg = LS.load_prereg(None)

    def forced(frame, consensus):
        sc = LS.score_frames(frame, consensus, n_boot=payload.get("n_boot", 5))
        sc.results = [LS.LineResult(r.line, mode == "all-pass", f"forced {mode} by the rehearsal") for r in sc.results]
        return sc

    branch_ref = LS.fetch_branch_ref()
    verdict, out = LS.final_evaluation(source=LS.BranchSource(None, ref=branch_ref["ref"]), dim_game=LS.load_dim_game(),
                                       prereg=prereg, branch_ref=branch_ref, sensitivity=True, sensitivity_fn=forced)
    sealed = read_json(LS.scoring_root() / LS.SEALED_DIR / "final_lines.json")
    return {"verdict": verdict, "final_md": out.read_text(encoding="utf-8"), "final_lines": sealed}


def cmd_hook_part(payload):
    """Score one week with the hook into a scratch root, reusing the week's recorded pins; part hashes."""
    scratch = Path(payload["root"])
    s, w = payload["season"], payload["week"]
    record = LS._week_record(s, w) or {}
    if payload.get("pins"):  # score against these snapshots instead of the recorded ones (the LATEST contrast)
        record = {**record, "data_snapshots": payload["pins"]}
    res = weekly_hook(s, w, 5, 5, False, _t(payload["at"]), root=scratch, reports_dir=scratch / "reports",
                      record=record)
    part = LS.part_path(scratch, s, w)
    cons = LS.consensus_part_path(scratch, s, w)
    rec = read_json(LS.record_path(scratch, s, w)) or {}
    return {**res, "part_sha256": W.sha256_file(part) if part.is_file() else None,
            "consensus_part_sha256": W.sha256_file(cons) if cons.is_file() else None,
            "outcome_rows_sha256": rec.get("outcome_rows_sha256"), "data_snapshots": rec.get("data_snapshots")}


def cmd_latest_pins(payload):
    """The LATEST snapshot id of every pinned dataset (the snapshot a rescore would read if it did not pin)."""
    roots = LS.store_roots()
    out = {}
    for tier, ds in LS.PINNED_DATASETS:
        pointer = roots[tier] / ds / "LATEST"
        if pointer.exists():
            out[f"{tier}/{ds}"] = pointer.read_text(encoding="utf-8").strip()
    return out


def cmd_repro_pinned_dirty(payload):
    """The weekly scorer run from a worktree pinned at X, then the runner's own pin check (QA-F27)."""
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

    out = {"status_before": status(), "pin_before": pin(), "project_root": str(LS.PROJECT_ROOT),
           "default_reports_dir": str(LS.default_reports_dir())}
    s, w = payload["season"], payload["week"]
    res = weekly_hook(s, w, 5, 5, False, W.friday_scoring(s, w), root=scratch,
                      record=json.loads((Path(payload["sealed_dir"]) / f"{W.week_key(s, w)}.json")
                                        .read_text(encoding="utf-8")))
    out.update(weekly=res, status_after=status(), pin_after=pin())
    return out


def cmd_integrity(payload):
    """The final's pre-metric checks for the current state (as ``final_evaluation`` builds them), no metrics."""
    prereg = LS.load_prereg(None)
    window = LS.prereg_window(prereg.data)
    root = LS.scoring_root()
    records = LS.load_records(root, window)
    source = LS.BranchSource(None)
    on_branch = {w for w in window if source.read(f"{LS.week_key(*w)}/{LS.COMMITMENT_NAME}") is not None
                 or source.read(f"{LS.SEALED_BRANCH_DIR}/{LS.week_key(*w)}.sha256") is not None}
    markers = {w: m for w in window if (m := LS.missed_marker(source, *w)) is not None}
    misses = {w: m for w, m in markers.items() if w not in on_branch and w not in records}
    checks = LS.livetest_integrity_checks(prereg, records, window, misses, root=root)
    checks += LS.branch_record_checks(source, records, [w for w in window if w in records], None)
    return {"checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
            "unsealed_on_branch": [LS.week_key(*w) for w in window if w in on_branch and w not in records]}


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
    """A new dim_game snapshot (simulated id ``at``) in which one game of the given week is not final."""
    import polars as pl

    from ffml.data import store as ST

    store = ST.SnapshotStore(get_settings().processed_dir)
    old = store.latest_id("dim_game")
    games = store.read("dim_game")
    s, w = payload["season"], payload["week"]
    target = games.filter((pl.col("season") == s) & (pl.col("week") == w))["game_id"].sort().to_list()[-1]
    games = games.with_columns(pl.when(pl.col("game_id") == target).then(pl.lit("scheduled"))
                               .otherwise(pl.col("status")).alias("status"))
    saved = ST._utc_now
    ST._utc_now = lambda: _t(payload["at"])
    try:
        new = store.write_snapshot("dim_game", {int(x): games.filter(pl.col("season") == x)
                                                for x in sorted(games["season"].unique().to_list())},
                                   meta={"synthetic": True, "note": f"{target} not final"})
    finally:
        ST._utc_now = saved
    return {"old": old, "new": new, "game": target}


def cmd_comparators_probe(payload):
    """QA-F31: ``ensure_comparators`` when the comparators run must be extended to the week, with a stub runner.

    The predictions file is cut before each week; the stub records the actuals the run would be scheduled
    from and puts the full run back (the "extension"). The real ``run_comparators`` (the full walk-forward)
    runs on real data at the comparators pre-warm after the freeze and in the Week 10 rehearsal (D-127)."""
    import inspect

    import polars as pl

    from ffml.eval.harness import PREDICTIONS

    pred = LS.comparators_run_dir(LS.COMPARATORS_RUN) / PREDICTIONS
    full = pl.read_parquet(pred)
    eg = payload["excluded_game"]
    params = inspect.signature(LS.ensure_comparators).parameters
    latest = getattr(LS, "season_dim_game", None) or (lambda s_: LS.load_dim_game([s_]))

    def game_rows(a: pl.DataFrame) -> dict:
        wk = a.filter((pl.col("season") == eg["season"]) & (pl.col("week") == eg["week"]))
        hits = []
        if "game_id" in wk.columns:
            hits.append(pl.col("game_id").cast(pl.String) == eg["game_id"])
        if "team" in wk.columns:
            hits.append(pl.col("team").cast(pl.String).is_in(eg["teams"]))
        hit = hits[0]
        for h in hits[1:]:
            hit = hit | h
        return {"week_rows": wk.height, "game_rows": wk.filter(hit).height,
                "columns": [c for c in ("game_id", "team") if c in wk.columns]}

    out = {"signature": sorted(params)}
    for case in payload["cases"]:
        s, w, now = case["season"], case["week"], _t(case["at"])
        res: dict = {"week": W.week_key(s, w), "at": case["at"]}
        seen: dict = {}

        def stub(actuals, run_id, _seen=seen):
            _seen.update(rows=actuals.height, last_week=max(int(a) * 100 + int(b) for a, b in
                                                            actuals.select("season", "week").unique().rows()),
                         **game_rows(actuals))
            full.write_parquet(pred)  # the extension: the run now holds the week
            return LS.comparators_run_dir(run_id)

        full.filter(pl.col("season") * 100 + pl.col("week") < s * 100 + w).write_parquet(pred)
        try:
            plan = LS.plan_week(s, w, latest(s), now=now, record={})  # a first scoring of the week
            res.update(excluded=list(plan.excluded), pin_from=LS.iso(plan.pin_from))
            kw = {"run": LS.COMPARATORS_RUN, "runner": stub,
                  "actuals_fn": lambda s_, st=plan.stores["processed"]: LS.load_actuals(s_, st)}
            if "excluded_games" in params:
                kw["excluded_games"] = plan.excluded
            if "now" in params:
                kw["now"] = now
            rows = LS.ensure_comparators(s, w, plan.dim_game, **kw)
            res.update(ok=True, rows=rows.height, runner_called=bool(seen), actuals=dict(seen))
        except (LS.ScoreRefused, LS.RetryLater) as exc:
            res.update(ok=False, refused=f"{type(exc).__name__}: {exc}", runner_called=bool(seen))
        finally:
            full.write_parquet(pred)
        out[case["label"]] = res
    return out


COMMANDS = {"probe": cmd_probe, "comparators-probe": cmd_comparators_probe, "build": cmd_build, "weekly": cmd_weekly, "final-forced": cmd_final_forced,
            "hook-part": cmd_hook_part, "latest-pins": cmd_latest_pins, "repro-pinned-dirty": cmd_repro_pinned_dirty,
            "integrity": cmd_integrity, "dim-game-unfinished": cmd_dim_game_unfinished, "seals": cmd_seals}


def main() -> int:
    command, payload, out = sys.argv[1], json.loads(sys.argv[2]), Path(sys.argv[3])
    result = COMMANDS[command](payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
