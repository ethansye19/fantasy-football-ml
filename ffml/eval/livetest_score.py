"""Live-test scorer (D-098, D-105 to D-110): weekly information reports and the single final verdict.

``python -m ffml.eval.livetest_score --week 2026w10`` scores one committed week
of the chain_v2 live test after every regular-season game of that week is
final; ``--final`` applies the 22 pre-registered lines once over the whole
window and prints the only ``Verdict:`` line this module ever writes.

Weekly scoring
==============

1. **Read** the week's commitment from the ``livetest-2026`` branch (the
   commitments worktree, else ``git show origin/livetest-2026:<path>``), the
   branch copy of ``player_summary.parquet`` when present, the local files the
   commitment lists (the raw rows ``player_summary_raw.parquet``), the
   runner's ``push_record.json`` (GitHub's push activity timestamp) and
   ``dim_game``.
2. **Refuse** to score when any game of the week is not final in ``dim_game``,
   when the commitment's digest or any listed file hash does not match, when
   no external push timestamp exists or it is not before the committed first
   kickoff, or when the commitment is a rehearsal. Every other check (commit
   relation with the pre-registration's allowed paths, pins, calibrator
   strings, labels, model version) is recorded and re-evaluated in the final.
3. **Outcomes** come from the harness functions in the order of
   ``run_walk_forward``'s combine step (``outcome_table``, ``week_cutoffs``,
   ``join_outcomes``, ``position_bounds``, ``fill_bounds``, ``with_pit``), from
   ``ffml.scoring.actuals.actual_statlines`` on the latest processed data.
4. **Score** with the holdout machinery (:mod:`ffml.eval.report_backtest`):
   calibrated forms, D-077 pair set, consensus on matched rows, tie-aware
   coverage. The raw rows are relabelled ``chain_v2`` and the calibrated rows
   ``chain_v2+cal`` so the ``+cal`` convention applies unchanged. Comparators
   come from a harness run that holds the baselines' calibrated forms for the
   week (``--baselines-run``) and from the consensus archive; when either is
   absent the affected lines are marked unavailable in the weekly report.
5. **Write** ``data/livetest/scoring/parts/<season>_wNN.parquet`` and
   ``.../weeks/<season>wNN.json`` (checks, hashes, timestamps, snapshot ids),
   and ``reports/livetest/<season>wNN.md``: this week's point metrics and the
   22 lines over the weeks scored so far, information only. A single week's
   bootstrap has one cluster, so its intervals are degenerate; the lines are
   decided only in the final.

Final
=====

``--final`` is refused until every week of the pre-registered window is final
in ``dim_game`` and each is either scored or carries ``MISSED.json`` on the
branch. One missed week is excluded (D-106); a second makes the verdict
``INVALID``. The integrity checks mirror :func:`ffml.eval.report_holdout.integrity_checks`
for the live test: the 22 lines' hash, bootstrap and seeds, the frozen commit
and every week's commit relation under the allowed paths, pins (LF and CRLF
forms), calibrator method strings, model version scheme, labels, per-week
commitment hashes against the push record and push timestamps before kickoff,
and the generator's own HEAD. Then the 22 lines are evaluated once over the
scored weeks (clusters are season-weeks, 500 paired draws, seed 0, metric seed
0) and ``Verdict: APPROVED``, ``Verdict: NOT APPROVED`` or
``Verdict: INVALID (<reason>)`` is printed and written to
``reports/livetest/final.md``. ``--dry-run`` relabels that line so it can never
satisfy the registry's exact-line check (D-076).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import PROJECT_ROOT, ensure_utf8, get_settings
from ffml.eval import report_backtest as RB
from ffml.eval import report_holdout as H
from ffml.eval.report_holdout import Check, LineResult

BRANCH = "livetest-2026"
REMOTE = "origin"
DEFAULT_WORKTREE = PROJECT_ROOT.parent / "ffml-livetest-commitments"
PREREG_DOC = "docs/status/livetest-preregistration-2026.md"
COMMITMENT_NAME = "commitment.json"
SUMMARY_NAME = "player_summary.parquet"
RAW_SUMMARY_NAME = "player_summary_raw.parquet"
MISSED_NAME = "MISSED.json"
PUSH_RECORD_NAME = "push_record.json"
FOCUS = "chain_v2"
RAW_LABEL = "chain_v1"  # the raw rows' model label in player_summary_raw
PURPOSE = "livetest_score"
SCORING = "ppr"
POSITIONS: tuple[str, ...] = RB.POSITIONS
VERSION_RE = re.compile(r"^chain_v2-primary-c(\d{4})w(\d{2})-\d{8}T\d{6}Z$")
#: the fallback window (D-110) when the pre-registration JSON carries no explicit ``window``
DEFAULT_WINDOW: tuple[tuple[int, int], ...] = (*((2026, w) for w in range(10, 19)), *((2027, w) for w in range(1, 10)))
WEEK_RE = re.compile(r"^(\d{4})w(\d{1,2})$")


class ScoreRefused(RuntimeError):
    """The scorer refuses to run (week not final, integrity failure, window incomplete)."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def week_key(season: int, week: int) -> str:
    return f"{season}w{week:02d}"


def parse_week(text: str) -> tuple[int, int]:
    m = WEEK_RE.match(text.strip())
    if not m:
        raise ValueError(f"week must look like 2026w10, got {text!r}")
    return int(m.group(1)), int(m.group(2))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_lf(path: Path) -> str:
    """SHA-256 of a tracked text file with CRLF normalised to LF (the pre-registration's hash rule)."""
    return sha256_bytes(Path(path).read_bytes().replace(b"\r\n", b"\n"))


def canonical_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of every top-level field except ``digest``.

    The same form as ``ffml.sim.livetest.commitment_digest`` and
    ``scripts/livetest_core.canonical_digest``: sorted keys, separators ``,``
    and ``:``, UTF-8, non-ASCII kept, ``default=str``.
    """
    body = {k: v for k, v in payload.items() if k != "digest"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return sha256_bytes(text.encode("utf-8"))


def parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat(timespec="seconds") if ts else None


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=root, capture_output=True,
                          text=True, timeout=120, check=False)


# --------------------------------------------------------------------------- #
# The branch: commitments, summaries, MISSED markers
# --------------------------------------------------------------------------- #


@dataclass
class BranchSource:
    """Files of the ``livetest-2026`` branch: a worktree directory first, else ``git show``."""

    worktree: Path | None = None
    repo_root: Path = PROJECT_ROOT
    ref: str = f"{REMOTE}/{BRANCH}"

    def read(self, rel: str) -> bytes | None:
        rel = rel.replace("\\", "/")
        if self.worktree is not None:
            p = Path(self.worktree) / rel
            if p.is_file():
                return p.read_bytes()
            if not (Path(self.worktree) / ".git").exists():
                return None  # a plain directory (tests): no git fallback
        out = subprocess.run(["git", "show", f"{self.ref}:{rel}"], cwd=self.repo_root, capture_output=True,
                             timeout=120, check=False)
        return out.stdout if out.returncode == 0 else None

    def commit_of(self, rel: str) -> str | None:
        """The branch commit that added ``rel`` (None when the branch is not a git ref here)."""
        out = _run_git(self.repo_root, "log", "--format=%H", "--diff-filter=A", self.ref, "--", rel)
        shas = out.stdout.split()
        return shas[-1] if out.returncode == 0 and shas else None


@dataclass
class Commitment:
    rel: str
    raw: bytes
    data: dict[str, Any]

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.raw)

    @property
    def season(self) -> int:
        return int(self.data["season"])

    @property
    def week(self) -> int:
        return int(self.data["week"])

    @property
    def first_kickoff(self) -> datetime | None:
        return parse_time(self.data.get("first_kickoff") or self.data.get("first_kickoff_utc"))

    @property
    def created_at(self) -> datetime | None:
        return parse_time(self.data.get("created_at"))

    @property
    def code_commit(self) -> str | None:
        code = self.data.get("code")
        if isinstance(code, Mapping):
            return code.get("commit")
        return str(code) if code else None

    @property
    def digest(self) -> str | None:
        d = self.data.get("digest")
        return d.get("value") if isinstance(d, Mapping) else d

    def outputs(self) -> list[dict[str, Any]]:
        return [dict(o) for o in self.data.get("outputs", [])]

    def history_sources(self) -> list[dict[str, Any]]:
        return [dict(s) for s in (self.data.get("calibration_history") or {}).get("sources", [])]

    def entry(self, name: str) -> dict[str, Any] | None:
        for o in self.outputs():
            if Path(str(o.get("path", "")).replace("\\", "/")).name == name:
                return o
        return None


def load_commitment(source: BranchSource, season: int, week: int) -> Commitment:
    rel = f"{week_key(season, week)}/{COMMITMENT_NAME}"
    raw = source.read(rel)
    if raw is None:
        raise ScoreRefused(f"{rel} is not on {source.ref}" + (f" or under {source.worktree}" if source.worktree else ""))
    return Commitment(rel, raw, json.loads(raw.decode("utf-8")))


def missed_marker(source: BranchSource, season: int, week: int) -> dict[str, Any] | None:
    raw = source.read(f"{week_key(season, week)}/{MISSED_NAME}")
    return json.loads(raw.decode("utf-8")) if raw else None


# --------------------------------------------------------------------------- #
# Week status from dim_game
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WeekStatus:
    season: int
    week: int
    games: int
    final: int
    first_kickoff: datetime | None
    unfinished: tuple[str, ...]

    @property
    def is_final(self) -> bool:
        return self.games > 0 and self.final == self.games


def week_status(dim_game: pl.DataFrame, season: int, week: int) -> WeekStatus:
    """Regular-season games of the week and how many are final (``status == "final"``)."""
    games = dim_game.filter((pl.col("season") == season) & (pl.col("week") == week))
    if "game_type" in games.columns:
        games = games.filter(pl.col("game_type") == "REG")
    if "status" in games.columns:
        done = pl.col("status").cast(pl.String).str.to_lowercase() == "final"
    elif "result" in games.columns:
        done = pl.col("result").is_not_null()
    else:
        raise ValueError("dim_game has neither a status nor a result column")
    games = games.with_columns(done.fill_null(False).alias("_final"))
    first = None
    if "kickoff_utc" in games.columns and games.height:
        first = parse_time(games["kickoff_utc"].min())
    ids = games.filter(~pl.col("_final"))
    unfinished = tuple(str(g) for g in (ids["game_id"].to_list() if "game_id" in ids.columns else []))
    return WeekStatus(season, week, games.height, int(games["_final"].sum()), first, unfinished)


def load_dim_game(seasons: Iterable[int] | None = None) -> pl.DataFrame:  # pragma: no cover - reads data
    from ffml.data.store import SnapshotStore

    return SnapshotStore(get_settings().processed_dir).read("dim_game", seasons=list(seasons) if seasons else None)


def dim_game_snapshot_id() -> str | None:  # pragma: no cover - reads data
    pointer = get_settings().processed_dir / "dim_game" / "LATEST"
    return pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None


# --------------------------------------------------------------------------- #
# Commit relation under the pre-registration's allowed paths
# --------------------------------------------------------------------------- #


def allowed_prefixes(prereg: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """``(allowed, frozen)`` path prefixes from the live-test pre-registration JSON."""
    allowed: list[str] = []
    for key in list((prereg.get("allowed_paths") or {}).keys()) + list(prereg.get("allowed_changes_after_freeze") or []):
        for part in str(key).split(","):
            part = part.strip()
            if part.startswith("other "):
                part = part[len("other "):].strip()
            if part and "(" not in part and " " not in part:
                allowed.append(part)
    frozen = [str(p).strip() for p in prereg.get("frozen_paths") or [] if "/" in str(p) and " " not in str(p).strip()]
    return sorted(set(allowed)), sorted(set(frozen))


def path_allowed(path: str, allowed: Sequence[str], frozen: Sequence[str]) -> bool:
    p = path.replace("\\", "/")
    if any(p == f or p.startswith(f.rstrip("/") + "/") for f in frozen):
        return False
    return any(p == a or (a.endswith("/") and p.startswith(a)) for a in allowed)


def commit_relation_allowed(x: str | None, y: str | None, root: Path, allowed: Sequence[str],
                            frozen: Sequence[str]) -> H.CommitRelation:
    """Like :func:`ffml.eval.report_holdout.commit_relation`, with a set of allowed paths (frozen paths never)."""
    fx, fy = H._full_sha(str(x or ""), root), H._full_sha(str(y or ""), root)
    if fx is None or fy is None:
        missing = [s for s, f in ((x, fx), (y, fy)) if f is None]
        return H.CommitRelation(False, str(x), str(y), (), f"unknown commit {missing}")
    if fx == fy:
        return H.CommitRelation(True, fx, fy, (), f"at X {fx[:12]}")
    if _run_git(root, "merge-base", "--is-ancestor", fx, fy).returncode != 0:
        return H.CommitRelation(False, fx, fy, (), f"{fy[:12]} is not a descendant of X {fx[:12]}")
    diff = _run_git(root, "diff", "--name-only", fx, fy)
    files = tuple(sorted(f for f in diff.stdout.splitlines() if f.strip()))
    bad = [f for f in files if not path_allowed(f, allowed, frozen)]
    if diff.returncode == 0 and not bad:
        return H.CommitRelation(True, fx, fy, files, f"Y {fy[:12]} descends from X {fx[:12]}; {len(files)} allowed paths")
    return H.CommitRelation(False, fx, fy, files,
                            f"Y {fy[:12]} descends from X {fx[:12]} but touches {bad[:6]}" + (" ..." if len(bad) > 6 else ""))


# --------------------------------------------------------------------------- #
# Verification of one week's commitment
# --------------------------------------------------------------------------- #


@dataclass
class WeekRecord:
    """Everything the scorer established about one week before any metric was read."""

    season: int
    week: int
    checks: list[Check] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    commitment_sha256: str | None = None
    digest: str | None = None
    branch_commit: str | None = None
    code_commit: str | None = None
    model_version: str | None = None
    first_kickoff: str | None = None
    created_at: str | None = None
    external_timestamp: str | None = None
    external_source: str | None = None
    dim_game_first_kickoff: str | None = None
    games: int = 0
    games_final: int = 0
    summary_sha256: str | None = None
    raw_sha256: str | None = None
    files_verified: int = 0
    rehearsal: bool = False
    stub: bool = False
    pins: dict[str, Any] = field(default_factory=dict)
    calibrator_state: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, Any] = field(default_factory=dict)
    snapshots: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "checks"}
        out["checks"] = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks]
        return out


def _resolve_local(path: str, roots: Sequence[Path]) -> Path | None:
    p = Path(path)
    cands = [p] if p.is_absolute() else [Path(r) / p for r in roots]
    return next((c for c in cands if c.is_file()), None)


def verify_week(
    commitment: Commitment,
    *,
    branch_summary: bytes | None,
    push_record: Mapping[str, Any] | None,
    status: WeekStatus,
    roots: Sequence[Path],
    allow_rehearsal: bool = False,
    expected_commit: str | None = None,
    prereg: Mapping[str, Any] | None = None,
    repo_root: Path = PROJECT_ROOT,
    branch_commit: str | None = None,
) -> tuple[WeekRecord, Path | None, bytes | None]:
    """All pre-metric checks for one week. Returns the record, the raw-rows file and the summary bytes.

    ``refusals`` lists the failures that stop scoring (week not final, digest
    or file hash, push timestamp, rehearsal); other failures are recorded.
    """
    c = commitment
    rec = WeekRecord(c.season, c.week, commitment_sha256=c.sha256, digest=c.digest, code_commit=c.code_commit,
                     model_version=str(c.data.get("model_version") or ""), first_kickoff=iso(c.first_kickoff),
                     created_at=iso(c.created_at), games=status.games, games_final=status.final,
                     dim_game_first_kickoff=iso(status.first_kickoff), branch_commit=branch_commit,
                     rehearsal=bool(c.data.get("rehearsal")), stub=bool(c.data.get("stub")),
                     pins=dict(c.data.get("pins") or {}), calibrator_state=dict(c.data.get("calibrator_state") or {}),
                     labels={k: c.data.get(k) for k in ("environment", "approval_status", "label")},
                     snapshots=dict(c.data.get("snapshots") or {}))

    def check(name: str, ok: bool, detail: str, refuse: bool = False) -> None:
        rec.checks.append(Check(name, ok, detail))
        if refuse and not ok:
            rec.refusals.append(f"{name}: {detail}")

    check("week final", status.is_final,
          f"{status.final} of {status.games} regular-season games final in dim_game"
          + (f"; not final: {list(status.unfinished)[:6]}" if status.unfinished else ""), refuse=True)
    check("identity", c.season == status.season and c.week == status.week,
          f"commitment is {week_key(c.season, c.week)}", refuse=True)
    check("not a rehearsal or stub", (not rec.rehearsal and not rec.stub) or allow_rehearsal,
          "real commitment" if not (rec.rehearsal or rec.stub) else
          "rehearsal or stub commitment" + (" (allowed by flag; information only)" if allow_rehearsal else ""),
          refuse=True)
    want = canonical_digest(c.data)
    check("digest", c.digest == want, f"recomputed {want[:16]}..., committed {str(c.digest)[:16]}...", refuse=True)
    # every listed file: outputs and calibration-history sources
    entries = c.outputs() + c.history_sources()
    problems, n_ok = [], 0
    summary_bytes: bytes | None = None
    raw_path: Path | None = None
    for e in entries:
        p = str(e.get("path", ""))
        name = Path(p.replace("\\", "/")).name
        local = _resolve_local(p, roots)
        if name == SUMMARY_NAME and e.get("role") == "predictions" and branch_summary is not None:
            got = sha256_bytes(branch_summary)
            if got != e.get("sha256"):
                problems.append(f"{name} (branch copy) sha256 {got[:12]} != listed {str(e.get('sha256'))[:12]}")
            else:
                n_ok += 1
                summary_bytes = branch_summary
                rec.summary_sha256 = got
            if local is not None and sha256_file(local) != e.get("sha256"):
                problems.append(f"{name} (local copy) differs from the listed hash")
            continue
        if local is None:
            problems.append(f"{p} missing locally")
            continue
        got = sha256_file(local)
        if got != e.get("sha256"):
            problems.append(f"{p}: sha256 {got[:12]} != listed {str(e.get('sha256'))[:12]}")
            continue
        n_ok += 1
        if name == SUMMARY_NAME and e.get("role") == "predictions" and summary_bytes is None:
            summary_bytes = local.read_bytes()
            rec.summary_sha256 = got
        if name == RAW_SUMMARY_NAME and e.get("role") == "predictions":
            raw_path, rec.raw_sha256 = local, got
    rec.files_verified = n_ok
    check("file hashes", not problems, f"{n_ok} of {len(entries)} listed files verified"
          + ("" if not problems else "; " + "; ".join(problems[:5])), refuse=True)
    check("summary present", summary_bytes is not None and raw_path is not None,
          "calibrated and raw summaries found" if summary_bytes is not None and raw_path is not None else
          "player_summary.parquet or player_summary_raw.parquet not available", refuse=True)
    # timing
    k = c.first_kickoff
    check("created before kickoff", k is not None and c.created_at is not None and c.created_at < k,
          f"created_at {rec.created_at}, first_kickoff {rec.first_kickoff}", refuse=True)
    if status.first_kickoff is not None and k is not None and status.first_kickoff != k:
        check("kickoff agrees with dim_game", False,
              f"commitment {iso(k)} against dim_game {iso(status.first_kickoff)} (the committed time is the deadline)")
    pr = push_record or {}
    stamp = parse_time(pr.get("external_timestamp"))
    pa = ((pr.get("github") or {}).get("push_activity") or {})
    src = str(pr.get("external_timestamp_source") or "")
    external = stamp is not None and "github" in src.lower() and pa.get("timestamp") is not None
    rec.external_timestamp, rec.external_source = iso(stamp), src or None
    check("push record matches the commitment",
          bool(pr) and pr.get("commitment_sha256") == c.sha256 and pr.get("commitment_digest") == c.digest,
          "push_record.json names this commitment's sha256 and digest" if pr else "no push_record.json", refuse=True)
    check("pushed before kickoff (GitHub timestamp)", external and k is not None and stamp < k,
          f"GitHub push_activity.timestamp {iso(stamp)} against first kickoff {iso(k)}" if external else
          f"no GitHub push timestamp ({src or 'none'})", refuse=True)
    if branch_commit is not None:
        check("branch commit matches the push record", pr.get("commit_sha") == branch_commit,
              f"branch {branch_commit[:12]}, push record {str(pr.get('commit_sha'))[:12]}")
        if pa.get("after") is not None:
            check("GitHub activity names the branch commit", pa.get("after") == branch_commit,
                  f"activity.after {str(pa.get('after'))[:12]}")
    # recorded for the final (and shown weekly)
    check("labels", rec.labels.get("approval_status") == "not approved" and rec.labels.get("environment") == "private_test",
          f"{rec.labels}")
    m = VERSION_RE.match(rec.model_version or "")
    check("model version", bool(m) and m is not None and (int(m.group(1)), int(m.group(2))) == (c.season, c.week),
          rec.model_version or "missing")
    hl = rec.calibrator_state.get("history_last")
    prev = [c.season, c.week - 1] if c.week > 1 else [c.season - 1, 18]
    check("calibrator history ends the week before", list(hl or []) == prev, f"history_last {hl}, expected {prev}")
    if expected_commit:
        allowed, frozen = allowed_prefixes(prereg or {})
        rel = commit_relation_allowed(expected_commit, c.code_commit, repo_root, allowed, frozen)
        check("code commit", rel.ok, rel.detail)
    else:
        check("code commit", False, "the pre-registration has no frozen commit yet")
    if prereg:
        check_pins(rec, prereg, repo_root)
    return rec, raw_path, summary_bytes


def check_pins(rec: WeekRecord, prereg: Mapping[str, Any], root: Path) -> None:
    """Pins and calibrator strings of the commitment against the pre-registration (LF and CRLF forms)."""
    pins = prereg.get("pins") or {}
    sel = pins.get("selection") or {}
    want = {sel.get("sha256"), sel.get("sha256_crlf_working_copy")} - {None}
    got = rec.pins.get("selection_sha256")
    rec.checks.append(Check("pin: selection.json", got in want, f"commitment {str(got)[:12]}, pre-registered {sorted(want)}"))
    fm = (pins.get("feature_manifest") or {}).get("sha256")
    rec.checks.append(Check("pin: feature manifest", rec.pins.get("feature_manifest_sha256") == fm,
                            f"commitment {str(rec.pins.get('feature_manifest_sha256'))[:12]}, pre-registered {str(fm)[:12]}"))
    rec.checks.append(Check("pin: feature version", rec.pins.get("feature_version") == pins.get("feature_version"),
                            f"{rec.pins.get('feature_version')} against {pins.get('feature_version')}"))
    cal = prereg.get("calibration") or {}
    for key, want_s in (("quantile_calibrator", cal.get("quantiles")), ("isotonic", cal.get("isotonic"))):
        got_s = rec.calibrator_state.get(key) or rec.pins.get(key) or rec.pins.get(f"{key}_calibrator")
        rec.checks.append(Check(f"calibrator: {key}", bool(want_s) and got_s == want_s,
                                "equal to the pre-registration" if got_s == want_s else f"{got_s!r} against {want_s!r}"))
    rec.checks.append(Check("calibration version", rec.calibrator_state.get("calibration_version") == cal.get("version"),
                            f"{rec.calibrator_state.get('calibration_version')} against {cal.get('version')}"))
    for name, rel_path in (("selection.json (LF)", sel.get("path")),
                           ("feature manifest file", (pins.get("feature_manifest") or {}).get("path"))):
        if rel_path and (root / rel_path).is_file():
            got_lf = sha256_lf(root / rel_path)
            want_h = sel.get("sha256") if name.startswith("selection") else fm
            rec.checks.append(Check(f"pinned file now: {name}", got_lf == want_h, f"LF sha256 {got_lf[:12]}"))


# --------------------------------------------------------------------------- #
# Outcomes and frames
# --------------------------------------------------------------------------- #


OutcomesFn = Callable[[pl.DataFrame, int], pl.DataFrame]


def harness_outcomes(raw: pl.DataFrame, season: int) -> pl.DataFrame:  # pragma: no cover - reads data
    """Outcome columns exactly as ``run_walk_forward`` combines them (then the deterministic PIT)."""
    from ffml.eval.harness import (
        fill_bounds,
        join_outcomes,
        outcome_table,
        position_bounds,
        resolve_scorings,
        week_cutoffs,
        with_pit,
    )
    from ffml.scoring.actuals import actual_statlines

    rules = resolve_scorings(("ppr", "half", "std"))
    actuals = actual_statlines(range(int(get_settings().first_season), season + 1), PURPOSE)
    outcomes = outcome_table(actuals, [season], rules)
    cutoffs = week_cutoffs(actuals.filter(pl.col("season") == season))
    joined = fill_bounds(join_outcomes(raw, outcomes, cutoffs), position_bounds(actuals, [season], rules))
    try:
        return with_pit(joined)
    except Exception:  # noqa: BLE001 - the PIT column is optional for the lines
        return joined


def relabel(frame: pl.DataFrame, label: str) -> pl.DataFrame:
    return frame.with_columns(pl.lit(label).alias("model"))


def week_frame(raw_rows: pl.DataFrame, cal_rows: pl.DataFrame, season: int, week: int,
               outcomes: OutcomesFn) -> pl.DataFrame:
    """Raw (``chain_v2``) and calibrated (``chain_v2+cal``) rows of the week with outcomes attached."""
    both = pl.concat([relabel(raw_rows, FOCUS), relabel(cal_rows, FOCUS + RB.CAL)], how="diagonal_relaxed")
    both = both.filter((pl.col("season") == season) & (pl.col("week") == week))
    if both.is_empty():
        raise ScoreRefused(f"the committed summaries hold no rows for {week_key(season, week)}")
    return outcomes(both, season)


COMPARATORS_CONFIG = "livetest_comparators"
COMPARATORS_RUN = "livetest_comparators"  # data/backtests/livetest_comparators
COMPARATOR_LABELS: tuple[str, ...] = tuple(b + RB.CAL for b in H.LINE_BASELINES)


def comparators_run_dir(run: str | Path | None = None) -> Path:
    from ffml.eval.harness import resolve_run_dir

    return resolve_run_dir(run or COMPARATORS_RUN)


def baseline_rows(run: str | Path, season: int, week: int) -> pl.DataFrame:
    """The comparators run's rows for one week: baselines and the challenger, raw and calibrated, PPR."""
    from ffml.eval.harness import PREDICTIONS

    path = comparators_run_dir(run) / PREDICTIONS
    if not path.is_file():
        return pl.DataFrame()
    frame = pl.read_parquet(path)
    if "scoring" in frame.columns:
        frame = frame.filter(pl.col("scoring") == SCORING)
    keep = ~pl.col("model").str.starts_with("chain")
    return frame.filter((pl.col("season") == season) & (pl.col("week") == week) & keep)


def comparators_available(run: str | Path | None, season: int, week: int) -> bool:
    """True when the run holds the three line baselines' calibrated rows for the week."""
    rows = baseline_rows(run or COMPARATORS_RUN, season, week)
    present = set(rows["model"].unique().to_list()) if rows.height else set()
    return all(label in present for label in COMPARATOR_LABELS)


def final_pairs(dim_game: pl.DataFrame) -> tuple[set[tuple[int, int]], set[int]]:
    """``(weeks fully final, seasons covered)`` from ``dim_game`` (regular season)."""
    g = dim_game.filter(pl.col("game_type") == "REG") if "game_type" in dim_game.columns else dim_game
    if "status" in g.columns:
        done = (pl.col("status").cast(pl.String).str.to_lowercase() == "final").fill_null(False)
    else:
        done = pl.col("result").is_not_null()
    agg = g.with_columns(done.alias("_f")).group_by("season", "week").agg(pl.len().alias("n"), pl.col("_f").sum().alias("f"))
    pairs = {(int(s), int(w)) for s, w, n, f in agg.rows() if n > 0 and n == f}
    return pairs, {int(x) for x in g["season"].unique().to_list()}


def final_week_filter(actuals: pl.DataFrame, dim_game: pl.DataFrame, upto: tuple[int, int]) -> pl.DataFrame:
    """Actual rows of weeks that are fully final in ``dim_game`` and not after ``upto``.

    Seasons that ``dim_game`` does not cover are kept whole (history). This is
    what the comparators run is scheduled from, so a partially final week can
    never enter the baselines' history, and the schedule ends at the week
    being scored (later weeks are added when their turn comes).
    """
    pairs, seasons = final_pairs(dim_game)
    s, w = upto
    key = pl.col("season") * 100 + pl.col("week")
    ok = pl.struct("season", "week").map_elements(
        lambda r: int(r["season"]) not in seasons or (int(r["season"]), int(r["week"])) in pairs,
        return_dtype=pl.Boolean)
    return actuals.filter(ok & (key <= s * 100 + w))


def earlier_weeks_not_final(dim_game: pl.DataFrame, season: int, week: int) -> list[str]:
    pairs, _ = final_pairs(dim_game)
    g = dim_game.filter(pl.col("game_type") == "REG") if "game_type" in dim_game.columns else dim_game
    all_pairs = {(int(s), int(w)) for s, w in g.select("season", "week").unique().rows()}
    return [week_key(s, w) for s, w in sorted(all_pairs) if (s, w) < (season, week) and (s, w) not in pairs]


def run_comparators(actuals: pl.DataFrame, run_id: str = COMPARATORS_RUN) -> Path:  # pragma: no cover - heavy
    """Run or extend the ``livetest_comparators`` configuration from the given actuals (its only schedule input)."""
    from ffml.eval.backtest_configs import get_config
    from ffml.eval.harness import run_walk_forward

    cfg = get_config(COMPARATORS_CONFIG)
    return run_walk_forward(cfg.build_models(), cfg.seasons, cfg.weeks, cfg.scorings, cfg.refit_every, run_id,
                            cfg.purpose, report_seasons=cfg.report_seasons, config_name=cfg.name,
                            calibration=cfg.calibration, actuals=actuals)


def load_actuals(season: int) -> pl.DataFrame:  # pragma: no cover - reads data
    from ffml.scoring.actuals import actual_statlines

    return actual_statlines(range(int(get_settings().first_season), season + 1), PURPOSE)


def ensure_comparators(season: int, week: int, dim_game: pl.DataFrame, *, run: str | Path | None = None,
                       runner: Callable[[pl.DataFrame, str], Path] = run_comparators,
                       actuals_fn: Callable[[int], pl.DataFrame] = load_actuals) -> pl.DataFrame:
    """The comparators' rows for the week; runs or extends ``livetest_comparators`` when they are missing.

    Refuses when an earlier regular-season week is not fully final (its
    outcomes would enter the baselines' history incomplete and the written
    parts are never recomputed) or when the week itself is not final.
    """
    run_id = str(run or COMPARATORS_RUN)
    if comparators_available(run_id, season, week):
        return baseline_rows(run_id, season, week)
    pending = earlier_weeks_not_final(dim_game, season, week)
    if pending:
        raise ScoreRefused(f"comparators cannot be extended to {week_key(season, week)}: earlier weeks not final "
                           f"in dim_game: {pending[:6]}")
    if not week_status(dim_game, season, week).is_final:
        raise ScoreRefused(f"comparators cannot be extended: {week_key(season, week)} is not final in dim_game")
    actuals = final_week_filter(actuals_fn(season), dim_game, (season, week))
    runner(actuals, run_id)
    if not comparators_available(run_id, season, week):
        raise ScoreRefused(f"the comparators run holds no calibrated rows for {week_key(season, week)} after extension")
    return baseline_rows(run_id, season, week)


def consensus_rows(season: int, week: int) -> pl.DataFrame:  # pragma: no cover - restricted data
    from ffml.eval.ecr_baseline import consensus_predictions

    return consensus_predictions([season], SCORING, purpose=PURPOSE).filter(pl.col("week") == week)


# --------------------------------------------------------------------------- #
# Scoring with the holdout machinery
# --------------------------------------------------------------------------- #


@dataclass
class Scored:
    main: RB.BootResult
    cons: RB.BootResult | None
    models: RB.ModelSet
    results: list[LineResult]
    rows: int
    pairs: dict[str, dict[str, int]]
    weeks: list[tuple[int, int]]
    comparators_present: list[str]
    consensus_present: bool


def score_frames(ppr: pl.DataFrame, consensus: pl.DataFrame | None, *, n_boot: int = H.SPEC_N_BOOT,
                 seed: int = H.SPEC_SEED, metric_seed: int = H.SPEC_METRIC_SEED,
                 positions: Sequence[str] = POSITIONS) -> Scored:
    """Exactly :func:`ffml.eval.report_holdout.compute_run`'s metric path on in-memory frames."""
    ppr = ppr.filter(pl.col("scoring") == SCORING) if "scoring" in ppr.columns else ppr
    present = set(ppr["model"].unique().to_list())
    models = RB.resolve_models(present, FOCUS)
    raw = [m for m in models.raw if m in present]
    ref = RB.reference_models(models, present)
    aligned, _, _ = RB.headline_frames(ppr, raw, present, ref)
    bounds = H.bounds_from_run(ppr)
    aligned = {m: RB.with_randomized_pit(f, bounds, metric_seed) for m, f in aligned.items()}
    main = RB.run_bootstrap(aligned, positions, lineup=False, n_boot=n_boot, seed=seed)
    cons_boot = None
    if consensus is not None and not consensus.is_empty():
        cons_frames = RB.consensus_population(consensus, aligned, raw, ref)
        cons_frames[RB.CONSENSUS] = RB.with_randomized_pit(cons_frames[RB.CONSENSUS], bounds, metric_seed)
        labels = [m for m in (models.focus, models.focus + RB.CAL, RB.CONSENSUS) if m in cons_frames]
        cons_boot = RB.run_bootstrap({m: cons_frames[m] for m in labels}, positions, lineup=False, n_boot=n_boot,
                                     seed=seed)
    results = H.evaluate_lines(main, cons_boot, FOCUS)
    weeks = sorted({(int(s), int(w)) for s, w in ppr.select("season", "week").unique().rows()})
    return Scored(main, cons_boot, models, results, ppr.height, RB.pair_counts(aligned), weeks,
                  [b for b in H.LINE_BASELINES if b + RB.CAL in present], cons_boot is not None)


# --------------------------------------------------------------------------- #
# Storage of parts and week records
# --------------------------------------------------------------------------- #


def scoring_root() -> Path:
    return get_settings().data_dir / "livetest" / "scoring"


def part_path(root: Path, season: int, week: int) -> Path:
    return root / "parts" / f"{season}_w{week:02d}.parquet"


def record_path(root: Path, season: int, week: int) -> Path:
    return root / "weeks" / f"{week_key(season, week)}.json"


def write_week(root: Path, rec: WeekRecord, frame: pl.DataFrame, extra: Mapping[str, Any]) -> None:
    pp = part_path(root, rec.season, rec.week)
    pp.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(pp)
    rp = record_path(root, rec.season, rec.week)
    rp.parent.mkdir(parents=True, exist_ok=True)
    payload = {**rec.as_dict(), **extra, "part": pp.name, "part_sha256": sha256_file(pp),
               "scored_at": iso(datetime.now(UTC))}
    rp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")


def load_parts(root: Path, weeks: Iterable[tuple[int, int]]) -> tuple[pl.DataFrame, list[tuple[int, int]]]:
    frames, found = [], []
    for s, w in weeks:
        p = part_path(root, s, w)
        if p.is_file():
            frames.append(pl.read_parquet(p))
            found.append((s, w))
    return (pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()), found


def load_records(root: Path, weeks: Iterable[tuple[int, int]]) -> dict[tuple[int, int], dict[str, Any]]:
    out = {}
    for s, w in weeks:
        p = record_path(root, s, w)
        if p.is_file():
            out[(s, w)] = json.loads(p.read_text(encoding="utf-8"))
    return out


# --------------------------------------------------------------------------- #
# Weekly run
# --------------------------------------------------------------------------- #


def prereg_window(data: Mapping[str, Any]) -> list[tuple[int, int]]:
    """The scored window: an explicit ``window`` list, else ``season``/``weeks`` plus ``extension``, else D-110."""
    win = data.get("window")
    if isinstance(win, Mapping) and win.get("weeks"):  # {"seasons": [...], "weeks": {"2026": [...], "2027": [...]}}
        return [(int(s), int(w)) for s, ws in win["weeks"].items() for w in ws]
    if win:
        return [(int(s), int(w)) for s, w in win]
    out: list[tuple[int, int]] = []
    if data.get("season") and data.get("weeks"):
        out += [(int(data["season"]), int(w)) for w in data["weeks"]]
    ext = data.get("extension") or {}
    if ext.get("season") and ext.get("weeks"):
        out += [(int(ext["season"]), int(w)) for w in ext["weeks"]]
    return out if len(out) >= 18 else list(DEFAULT_WINDOW)


def load_prereg(path: Path | str | None = None) -> H.Prereg | None:
    p = Path(path) if path else PROJECT_ROOT / PREREG_DOC
    try:
        return H.load_prereg(p)
    except (OSError, ValueError):
        return None


def _frozen_commit(prereg: H.Prereg | None) -> str | None:
    c = str((prereg.data if prereg else {}).get("commit") or "")
    return c if re.fullmatch(r"[0-9a-fA-F]{7,40}", c) else None


def score_week(
    season: int,
    week: int,
    *,
    source: BranchSource,
    dim_game: pl.DataFrame,
    push_record: Mapping[str, Any] | None,
    roots: Sequence[Path],
    outcomes: OutcomesFn = harness_outcomes,
    baselines: pl.DataFrame | None = None,
    consensus: pl.DataFrame | None = None,
    prereg: H.Prereg | None = None,
    allow_rehearsal: bool = False,
    root: Path | None = None,
    reports_dir: Path | None = None,
    repo_root: Path = PROJECT_ROOT,
    n_boot: int = H.SPEC_N_BOOT,
    dim_game_id: str | None = None,
) -> tuple[WeekRecord, Path]:
    """Verify, score and write one week. Raises :class:`ScoreRefused` on any refusal."""
    root = Path(root) if root is not None else scoring_root()
    reports_dir = Path(reports_dir) if reports_dir is not None else PROJECT_ROOT / "reports" / "livetest"
    c = load_commitment(source, season, week)
    status = week_status(dim_game, season, week)
    summary_bytes = source.read(f"{week_key(season, week)}/{SUMMARY_NAME}")
    rec, raw_path, summary = verify_week(
        c, branch_summary=summary_bytes, push_record=push_record, status=status, roots=roots,
        allow_rehearsal=allow_rehearsal, expected_commit=_frozen_commit(prereg),
        prereg=prereg.data if prereg else None, repo_root=repo_root,
        branch_commit=source.commit_of(c.rel) if source.worktree is None or (Path(source.worktree) / ".git").exists() else None,
    )
    if rec.refusals:
        raise ScoreRefused("; ".join(rec.refusals))
    assert raw_path is not None and summary is not None
    import io

    cal_rows = pl.read_parquet(io.BytesIO(summary))
    raw_rows = pl.read_parquet(raw_path)
    frame = week_frame(raw_rows, cal_rows, season, week, outcomes)
    parts = [frame]
    if baselines is not None and not baselines.is_empty():
        parts.append(baselines.filter((pl.col("season") == season) & (pl.col("week") == week)))
    week_all = pl.concat(parts, how="diagonal_relaxed")
    scored = score_frames(week_all, consensus, n_boot=n_boot)
    # window so far (information only): earlier parts plus this week
    window = prereg_window(prereg.data if prereg else {})
    earlier, found = load_parts(root, [w for w in window if w != (season, week)])
    so_far = pl.concat([f for f in (earlier, week_all.filter(pl.col("scoring") == SCORING) if "scoring" in week_all.columns else week_all) if not f.is_empty()], how="diagonal_relaxed")
    cons_all = consensus
    scored_so_far = score_frames(so_far, cons_all, n_boot=n_boot) if len(found) else None
    extra = {"dim_game_snapshot": dim_game_id, "rows": frame.height, "comparators": scored.comparators_present,
             "consensus": scored.consensus_present, "n_boot": n_boot, "scoring": SCORING,
             "window_weeks_scored": sorted(found + [(season, week)]), "window_total": len(window)}
    write_week(root, rec, week_all, extra)
    text = render_week(rec, scored, scored_so_far, extra, found)
    assert "Verdict:" not in text
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{week_key(season, week)}.md"
    out.write_text(text, encoding="utf-8")
    return rec, out


def _checks_table(checks: Sequence[Check]) -> str:
    return RB._md_table(["check", "result", "detail"],
                        [[c.name, "PASS" if c.ok else "FAIL", c.detail] for c in checks])


def _lines_table(results: Sequence[LineResult]) -> str:
    return RB._md_table(["line", "statement", "status (information only)", "detail"],
                        [[r.line.id, r.line.describe(FOCUS), "pass" if r.passed else "not passed", r.detail]
                         for r in results])


def render_week(rec: WeekRecord, scored: Scored, so_far: Scored | None, extra: Mapping[str, Any],
                earlier: Sequence[tuple[int, int]]) -> str:
    L: list[str] = []
    a = L.append
    wk = week_key(rec.season, rec.week)
    a(f"# Live test, {wk}: information only")
    a("")
    a("This weekly report never decides anything. The 22 pre-registered lines are applied once, over the whole "
      "window, after 2027 week 9 (D-110); a single week's bootstrap has one cluster, so no interval below is "
      "meaningful. Outputs are labeled not approved (D-099).")
    a("")
    a("## Commitment and integrity")
    a("")
    a(RB._md_table(["item", "value"], [
        ["commitment sha256", rec.commitment_sha256], ["digest", rec.digest], ["branch commit", rec.branch_commit or "n/a"],
        ["code commit", rec.code_commit], ["model version", rec.model_version],
        ["created at / first kickoff", f"{rec.created_at} / {rec.first_kickoff}"],
        ["GitHub push timestamp", f"{rec.external_timestamp} ({rec.external_source})"],
        ["games final in dim_game", f"{rec.games_final} of {rec.games} (snapshot {extra.get('dim_game_snapshot')})"],
        ["files verified", str(rec.files_verified)], ["rows scored (all scorings)", str(extra.get("rows"))],
        ["comparators present", ", ".join(scored.comparators_present) or "none (L01-L12 unavailable this week)"],
        ["consensus present", "yes" if scored.consensus_present else "no (L13-L16 unavailable this week)"],
    ]))
    a("")
    a(_checks_table(rec.checks))
    a("")
    a("## This week, PPR, calibrated chain_v2 (point values)")
    a("")
    rows = []
    for pos in POSITIONS:
        f = FOCUS + RB.CAL
        if not scored.main.has(pos, f):
            continue
        v = scored.main.value
        rows.append([pos, str(scored.main.n.get(pos, {}).get(f, "")), RB._num(v(pos, f, "mae")),
                     RB._num(v(pos, f, "pinball_q10")), RB._num(v(pos, f, "pinball_q50")), RB._num(v(pos, f, "pinball_q90")),
                     RB._num(v(pos, f, "coverage_tie_aware")), RB._num(v(pos, f, "coverage_80")),
                     RB._num(v(pos, f, "start_sit_common")) if pos in RB.ACCEPT_POSITIONS else "n/a"])
    a(RB._md_table(["position", "n", "MAE", "pinball q10", "pinball q50", "pinball q90", "tie-aware coverage",
                    "closed coverage", "start/sit (common set)"], rows))
    a("")
    if scored.comparators_present:
        a("Chain minus comparator this week (point differences; negative favours the chain):")
        a("")
        rows = []
        for pos in RB.ACCEPT_POSITIONS:
            for comp in scored.comparators_present:
                other = comp + RB.CAL
                if scored.main.has(pos, other):
                    rows.append([pos, f"`{comp}`"] + [RB._num(scored.main.delta(pos, FOCUS + RB.CAL, other, m).delta, 3, True)
                                                      for m in H.BEATS_METRICS])
        a(RB._md_table(["position", "comparator", "MAE", "pinball q10", "pinball q50", "pinball q90"], rows))
        a("")
    total = extra.get("window_total", 18)
    a(f"## The 22 lines over the weeks scored so far ({len(earlier) + 1} of {total}; information only)")
    a("")
    if so_far is None:
        a("First scored week: the lines are shown for this week alone (one cluster; no interval).")
        a("")
        a(_lines_table(scored.results))
    else:
        a("Weeks: " + ", ".join(week_key(s, w) for s, w in sorted([*earlier, (rec.season, rec.week)])) + ".")
        a("")
        a(_lines_table(so_far.results))
    a("")
    a("A line marked not passed here is not a failure of the test; the test has one evaluation, after 2027 week 9.")
    a("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# Final: integrity checks over the window and the single verdict
# --------------------------------------------------------------------------- #


def rules_hash(rules: Mapping[str, Any]) -> str:
    """``rules_sha256`` as the pre-registration computes it: canonical JSON of ``rules`` without the field."""
    body = {k: v for k, v in rules.items() if k != "rules_sha256"}
    return sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def livetest_integrity_checks(prereg: H.Prereg, records: Mapping[tuple[int, int], Mapping[str, Any]],
                              window: Sequence[tuple[int, int]], misses: Mapping[tuple[int, int], Any], *,
                              repo_root: Path = PROJECT_ROOT) -> list[Check]:
    """The pre-metric checks of the final evaluation (the live-test counterpart of ``integrity_checks``)."""
    data = prereg.data
    checks: list[Check] = []
    rules = data.get("rules") or {}
    pinned = rules.get("lines_sha256") or data.get("lines_sha256")
    checks.append(Check("approval lines", pinned == H.lines_spec_sha256(),
                        "the pre-registration pins the 22 lines of this code" if pinned == H.lines_spec_sha256()
                        else f"pre-registered lines_sha256 {pinned!r} differs from this code's {H.lines_spec_sha256()}"))
    if rules.get("rules_sha256"):
        want_r = rules_hash(rules)
        checks.append(Check("rules hash", rules["rules_sha256"] == want_r,
                            f"rules_sha256 {str(rules['rules_sha256'])[:16]}, canonical rules block {want_r[:16]}"))
    stats = {"n_boot": H.SPEC_N_BOOT, "seed": H.SPEC_SEED, "metric_seed": H.SPEC_METRIC_SEED}
    got = {k: data.get(k) for k in stats}
    checks.append(Check("bootstrap and seeds", got == stats, f"pre-registered {got}, the pinned spec uses {stats}"))
    commit = _frozen_commit(prereg)
    checks.append(Check("frozen commit", commit is not None, f"pre-registered commit {data.get('commit')!r}"))
    allowed, frozen = allowed_prefixes(data)
    checks.append(Check("allowed paths", bool(allowed) and bool(frozen),
                        f"{len(allowed)} allowed prefixes, {len(frozen)} frozen prefixes"))
    head = _run_git(repo_root, "rev-parse", "HEAD").stdout.strip()
    if commit:
        rel = commit_relation_allowed(commit, head, repo_root, allowed, frozen)
        dirty = _run_git(repo_root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
        checks.append(Check("final evaluation commit", rel.ok, f"HEAD {head[:12]}: {rel.detail}"
                            + ("; tracked files modified" if dirty else "; working tree clean")))
    scored = [w for w in window if w in records]
    missed = [w for w in window if w in misses]
    unknown = [w for w in window if w not in records and w not in misses]
    checks.append(Check("window complete", not unknown,
                        f"{len(scored)} scored, {len(missed)} missed, {len(unknown)} unaccounted {unknown[:3]}"))
    checks.append(Check("missed weeks (D-106)", len(missed) <= 1,
                        f"{len(missed)} missed week(s) {[week_key(*w) for w in missed]}"
                        + ("; one is excluded, the window is not extended" if len(missed) == 1 else "")))
    overlap = [w for w in window if w in records and w in misses]
    checks.append(Check("no week both scored and missed", not overlap, f"{[week_key(*w) for w in overlap]}"))
    for (s, w), r in sorted(records.items()):
        wk = week_key(s, w)
        failed = [c for c in r.get("checks", []) if not c.get("ok")]
        if commit and r.get("code_commit"):
            rel = commit_relation_allowed(commit, r["code_commit"], repo_root, allowed, frozen)
            failed = [c for c in failed if c["name"] != "code commit"] + ([] if rel.ok else [{"name": "code commit", "detail": rel.detail}])
        checks.append(Check(f"{wk} commitment", not failed,
                            f"commitment {str(r.get('commitment_sha256'))[:12]}, GitHub push {r.get('external_timestamp')}, "
                            f"kickoff {r.get('first_kickoff')}, {r.get('files_verified')} files"
                            + ("" if not failed else "; FAILED: " + "; ".join(f"{c['name']} ({c['detail']})" for c in failed[:4]))))
        part = part_path(scoring_root(), s, w) if "part_dir" not in r else Path(r["part_dir"]) / r["part"]
        if r.get("part_sha256") and part.is_file():
            checks.append(Check(f"{wk} part", sha256_file(part) == r["part_sha256"], f"{part.name} sha256 unchanged since scoring"))
    return checks


def final_evaluation(
    *,
    source: BranchSource,
    dim_game: pl.DataFrame,
    prereg: H.Prereg,
    root: Path | None = None,
    consensus: pl.DataFrame | None = None,
    reports_dir: Path | None = None,
    repo_root: Path = PROJECT_ROOT,
    dry_run: bool = False,
    n_boot: int = H.SPEC_N_BOOT,
    now: datetime | None = None,
) -> tuple[str, Path]:
    """The one evaluation of the window. Returns the verdict line and the report path."""
    root = Path(root) if root is not None else scoring_root()
    reports_dir = Path(reports_dir) if reports_dir is not None else PROJECT_ROOT / "reports" / "livetest"
    window = prereg_window(prereg.data)
    last = window[-1]
    st_last = week_status(dim_game, *last)
    if not st_last.is_final:
        raise ScoreRefused(f"the final is refused until {week_key(*last)} is final in dim_game "
                           f"({st_last.final} of {st_last.games} games final)")
    not_final = [week_key(s, w) for s, w in window if not week_status(dim_game, s, w).is_final]
    if not_final:
        raise ScoreRefused(f"weeks not final in dim_game: {not_final}")
    records = load_records(root, window)
    misses = {w: m for w in window if w not in records and (m := missed_marker(source, *w)) is not None}
    unknown = [week_key(*w) for w in window if w not in records and w not in misses]
    if unknown:
        raise ScoreRefused(f"the final is refused: weeks neither scored nor marked MISSED: {unknown}")
    checks = livetest_integrity_checks(prereg, records, window, misses, repo_root=repo_root)
    results: list[LineResult] | None = None
    scored: Scored | None = None
    if all(c.ok for c in checks):
        frame, found = load_parts(root, [w for w in window if w in records])
        scored = score_frames(frame, consensus, n_boot=n_boot)
        results = scored.results
    verdict = H.verdict_text(checks, results, dry_run=dry_run)
    L: list[str] = [f"# Live test final evaluation ({week_key(*window[0])} to {week_key(*last)})", "",
                    f"Evaluated {iso(now or datetime.now(UTC))}. Pre-registration `{PREREG_DOC}` sha256 `{prereg.sha256[:16]}...`.",
                    "", "## Integrity checks", "", _checks_table(checks), "", "## The 22 lines", ""]
    if results is not None and scored is not None:
        L += [f"Weeks scored: {', '.join(week_key(s, w) for s, w in scored.weeks)}"
              + (f"; excluded (D-106): {', '.join(week_key(*w) for w in misses)}" if misses else "") + ".", "",
              _lines_table(results), ""]
    else:
        L += ["Not computed: an integrity check failed.", ""]
    L += ["## Verdict", "", verdict, ""]
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / ("final_dry_run.md" if dry_run else "final.md")
    out.write_text("\n".join(L), encoding="utf-8")
    return verdict, out


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def default_push_record(season: int, week: int) -> Path:
    return get_settings().data_dir / "livetest" / "runner" / week_key(season, week) / PUSH_RECORD_NAME


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - wiring
    ensure_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--week", help="season-week, for example 2026w10")
    ap.add_argument("--final", action="store_true", help="the single final evaluation (refused until the window is complete)")
    ap.add_argument("--dry-run", action="store_true", help="with --final: label the verdict as a dry run")
    ap.add_argument("--worktree", default=str(DEFAULT_WORKTREE), help=f"commitments worktree (else git show origin/{BRANCH})")
    ap.add_argument("--no-worktree", action="store_true", help="read the branch with git show only")
    ap.add_argument("--push-record", default=None, help="runner push_record.json (default data/livetest/runner/<week>/)")
    ap.add_argument("--baselines-run", default=COMPARATORS_RUN,
                    help="harness run with the comparators' calibrated rows (default data/backtests/livetest_comparators; "
                         "run or extended by the livetest_comparators config when the week is missing)")
    ap.add_argument("--no-comparators", action="store_true", help="score without L01-L12 comparators")
    ap.add_argument("--no-consensus", action="store_true")
    ap.add_argument("--allow-rehearsal", action="store_true", help="score a rehearsal commitment (information only)")
    ap.add_argument("--prereg", default=None)
    args = ap.parse_args(argv)
    source = BranchSource(None if args.no_worktree else Path(args.worktree))
    prereg = load_prereg(args.prereg)
    if args.final:
        if prereg is None:
            print("the final needs the pre-registration", file=sys.stderr)
            return 2
        try:
            verdict, out = final_evaluation(source=source, dim_game=load_dim_game(), prereg=prereg,
                                            consensus=None if args.no_consensus else _all_consensus(prereg),
                                            dry_run=args.dry_run)
        except ScoreRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3
        print(verdict)
        print(f"wrote {out}")
        return 0
    if not args.week:
        ap.error("--week or --final is required")
    season, week = parse_week(args.week)
    rec_path = Path(args.push_record) if args.push_record else default_push_record(season, week)
    push_record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else None
    dim_game = load_dim_game([season])
    baselines = None
    if not args.no_comparators:
        try:
            baselines = ensure_comparators(season, week, dim_game, run=args.baselines_run)
        except ScoreRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3
    consensus = None
    if not args.no_consensus:
        try:
            consensus = consensus_rows(season, week)
        except Exception as exc:  # noqa: BLE001 - the weekly report says the lines are unavailable
            print(f"consensus unavailable: {exc}", file=sys.stderr)
    try:
        rec, out = score_week(season, week, source=source, dim_game=dim_game, push_record=push_record,
                              roots=[PROJECT_ROOT, get_settings().data_dir.parent], baselines=baselines,
                              consensus=consensus, prereg=prereg, allow_rehearsal=args.allow_rehearsal,
                              dim_game_id=dim_game_snapshot_id())
    except ScoreRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    failed = [c.name for c in rec.checks if not c.ok]
    print(f"scored {week_key(season, week)}: {len(rec.checks) - len(failed)} of {len(rec.checks)} checks pass"
          + (f"; failed: {failed}" if failed else ""))
    print(f"wrote {out}")
    return 0


def _all_consensus(prereg: H.Prereg) -> pl.DataFrame | None:  # pragma: no cover - restricted data
    from ffml.eval.ecr_baseline import consensus_predictions

    seasons = sorted({s for s, _ in prereg_window(prereg.data)})
    try:
        return consensus_predictions(seasons, SCORING, purpose=PURPOSE)
    except Exception as exc:  # noqa: BLE001
        print(f"consensus unavailable: {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
