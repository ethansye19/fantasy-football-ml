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
2. **Refuse** to score when any game of the week is not final in ``dim_game``
   (D-121: a game still not final 14 days after the data cutoff is excluded
   instead, its rows removed and listed),
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
in ``dim_game`` (or past its data cutoff plus 14 days, D-121) and each is either scored or carries ``MISSED.json`` on the
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import PROJECT_ROOT, ensure_utf8, get_settings
from ffml.eval import metrics as M
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
#: sealed interim results (D-111): weekly metrics go here, never into a report before the final
SEALED_DIR = "sealed"
#: where the runner pushes each week's sealed-file hashes on the branch: <SEALED_BRANCH_DIR>/<week>.sha256
SEALED_BRANCH_DIR = "scoring"
#: report-only sensitivity check (D-111): the binding rule stays 500 draws, seed 0
SENSITIVITY_N_BOOT = 5000


class ScoreRefused(RuntimeError):
    """The scorer refuses to run (week not final, integrity failure, window incomplete)."""


class RetryLater(ScoreRefused):
    """Not yet: before the week's data cutoff, the week not final, or no snapshot at or after the cutoff."""


class FetchFailed(ScoreRefused):
    """The final could not fetch the branch, or the fetched ref differs from the remote head (retryable)."""


#: command-line exit codes (the runner relies on them; keep them stable)
EXIT_OK = 0
EXIT_USAGE = 2  # argparse errors, and --final without a pre-registration
EXIT_REFUSED = 3  # an integrity refusal or another failure that a retry will not fix
EXIT_RETRY_LATER = 4  # --week: before the data cutoff, the week not final, or no snapshot at or after the cutoff
EXIT_BLOCKED = 5  # --prebuild-check: an unlogged commit or a frozen-path commit
EXIT_FETCH_RETRY = 75  # --final: fetch or ls-remote failed; nothing written (EX_TEMPFAIL)


def utcnow() -> datetime:
    """The scorer's clock (tests replace it)."""
    return datetime.now(UTC)


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

    def added_by(self, rel: str) -> tuple[str, str] | None:
        """``(sha, committer time ISO)`` of the branch commit that added ``rel`` (None outside a git ref)."""
        if self.worktree is not None and not (Path(self.worktree) / ".git").exists():
            return None
        out = _run_git(self.repo_root, "log", "--format=%H %cI", "--diff-filter=A", self.ref, "--", rel)
        rows = [r.split() for r in out.stdout.splitlines() if r.strip()] if out.returncode == 0 else []
        return (rows[-1][0], rows[-1][1]) if rows and len(rows[-1]) == 2 else None


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


PROCESSED_DATASETS: tuple[str, ...] = ("fact_player_game", "fact_dst_game", "dim_game", "dim_player")


def processed_snapshot_ids() -> dict[str, str | None]:  # pragma: no cover - reads data
    """LATEST snapshot id of every processed dataset the outcomes and PIT bounds are built from."""
    root = get_settings().processed_dir
    out = {}
    for ds in PROCESSED_DATASETS:
        pointer = root / ds / "LATEST"
        out[ds] = pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None
    return out


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


CHANGELOG_DOC = "docs/status/livetest-changelog-2026.md"
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def changelog_commits(text: str) -> set[str]:
    """Commit shas (7 to 40 hex) in the ``Commit`` column of the change log's table."""
    out: set[str] = set()
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        try:
            cell = cells[header.index("commit")]
        except (ValueError, IndexError):
            continue
        out.update(m.group(0).lower() for m in _SHA_RE.finditer(cell))
    return out


def _changelog_append_only(sha: str, root: Path) -> bool:
    """True only for a non-merge commit whose single changed file is the change log and which only adds lines.

    Such a commit adds entries; an entry can name only a commit that already
    exists, so it cannot list itself. A merge commit, a commit that also touches
    another path, and a commit that edits or deletes any existing line of the
    change log (an edit shows as a deleted line) do not qualify.
    """
    parents = _run_git(root, "rev-list", "--parents", "-n", "1", sha)
    if parents.returncode != 0 or len(parents.stdout.split()) != 2:  # the commit itself plus exactly one parent
        return False
    names = _run_git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    files = [f.strip().replace("\\", "/") for f in names.stdout.splitlines() if f.strip()] if names.returncode == 0 else []
    if files != [CHANGELOG_DOC]:
        return False
    stat = _run_git(root, "diff-tree", "--no-commit-id", "--numstat", "-r", sha, "--", CHANGELOG_DOC)
    rows = [r.split("\t") for r in stat.stdout.splitlines() if r.strip()] if stat.returncode == 0 else []
    if len(rows) != 1 or not rows[0][0].isdigit() or not rows[0][1].isdigit():
        return False
    added, deleted = int(rows[0][0]), int(rows[0][1])
    return added > 0 and deleted == 0


def uncovered_commits(x: str, y: str, root: Path, logged: set[str]) -> list[str]:
    """Commits in ``x..y`` without an entry (prefix match) in the change log.

    A commit that only appends to the change log (:func:`_changelog_append_only`)
    is not required to have an entry: an entry can name only a commit that
    already exists, so the commit adding the last entries can never list itself.
    """
    fx, fy = H._full_sha(x, root), H._full_sha(y, root)
    if fx is None or fy is None or fx == fy:
        return []
    out = _run_git(root, "rev-list", f"{fx}..{fy}")
    shas = out.stdout.split() if out.returncode == 0 else []
    return [sha for sha in shas
            if not any(sha.startswith(tok) for tok in logged) and not _changelog_append_only(sha, root)]


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
    deadline: str | None = None  # the earlier of the committed and the actual first kickoff
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
    excluded: Sequence[str] = (),
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

    excl = tuple(str(g) for g in excluded)
    # D-121: a week is scored when every game is final, or without exactly the games still not final
    final_ok = status.is_final if not excl else (status.final > 0 and set(excl) == set(status.unfinished))
    check("week final", final_ok,
          f"{status.final} of {status.games} regular-season games final in dim_game"
          + (f"; not final: {list(status.unfinished)[:6]}" if status.unfinished else "")
          + (f"; excluded, {EXCLUSION_REASON}: {list(excl)}" if excl else ""), refuse=True)
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
    # timing: the binding deadline is the earlier of the committed first kickoff (dim_game at build time)
    # and the actual first kickoff per the final schedule (pre-registration deadline_rule)
    kicks = [t for t in (c.first_kickoff, status.first_kickoff) if t is not None]
    k = min(kicks) if kicks else None
    rec.deadline = iso(k)
    check("deadline", k is not None,
          f"binding {iso(k)}: committed first kickoff {rec.first_kickoff}, dim_game first kickoff "
          f"{rec.dim_game_first_kickoff}" if k is not None else "no first kickoff in the commitment or dim_game",
          refuse=True)
    check("created before the deadline", k is not None and c.created_at is not None and c.created_at < k,
          f"created_at {rec.created_at}, deadline {rec.deadline}", refuse=True)
    pr = push_record or {}
    stamp = parse_time(pr.get("external_timestamp"))
    pa = ((pr.get("github") or {}).get("push_activity") or {})
    src = str(pr.get("external_timestamp_source") or "")
    external = stamp is not None and "github" in src.lower() and pa.get("timestamp") is not None
    rec.external_timestamp, rec.external_source = iso(stamp), src or None
    check("push record matches the commitment",
          bool(pr) and pr.get("commitment_sha256") == c.sha256 and pr.get("commitment_digest") == c.digest,
          "push_record.json names this commitment's sha256 and digest" if pr else "no push_record.json", refuse=True)
    check("pushed before the deadline (GitHub timestamp)", external and k is not None and stamp < k,
          f"GitHub push_activity.timestamp {iso(stamp)} against the deadline {iso(k)}" if external else
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
    # the pushed blobs are immutable: the push record's hashes equal the branch copies read now
    committed = dict(pr.get("files_committed") or {})
    blobs = {f"{week_key(c.season, c.week)}/{COMMITMENT_NAME}": c.sha256}
    if branch_summary is not None:
        blobs[f"{week_key(c.season, c.week)}/{SUMMARY_NAME}"] = sha256_bytes(branch_summary)
    if committed:
        bad = [rel_ for rel_, sha in committed.items() if rel_ in blobs and blobs[rel_] != sha]
        check("pushed blobs unchanged", not bad,
              f"{len([r for r in committed if r in blobs])} pushed files hash as at push time" if not bad
              else f"changed since the push: {bad}")
    stored = (((prereg or {}).get("history_sources") or {}).get("stored_parts") or {})
    if stored.get("digest"):
        recs = [src_ for src_ in c.history_sources() if str(src_.get("origin", "")).startswith("stored")]
        lines = sorted(f"{r['path']} {r['sha256']}" for r in recs)
        got = sha256_bytes("\n".join(lines).encode("utf-8"))
        ok_n = stored.get("count") is None or len(recs) == int(stored["count"])
        check("stored parts digest", got == stored["digest"] and ok_n,
              f"{len(recs)} stored parts, digest {got[:12]}" + ("" if got == stored["digest"] else
                                                                 f" (pre-registered {str(stored['digest'])[:12]})"))
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


def harness_outcomes(raw: pl.DataFrame, season: int, store: Any = None) -> pl.DataFrame:  # pragma: no cover - reads data
    """Outcome columns exactly as ``run_walk_forward`` combines them (then the deterministic PIT).

    ``store`` pins the processed snapshots read (D-116 A3); default LATEST.
    """
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
    actuals = actual_statlines(range(int(get_settings().first_season), season + 1), PURPOSE, store=store)
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


def load_actuals(season: int, store: Any = None) -> pl.DataFrame:  # pragma: no cover - reads data
    from ffml.scoring.actuals import actual_statlines

    return actual_statlines(range(int(get_settings().first_season), season + 1), PURPOSE, store=store)


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


def consensus_rows(season: int, week: int, store: Any = None,
                   restricted_store: Any = None) -> pl.DataFrame:  # pragma: no cover - restricted data
    from ffml.eval.ecr_baseline import consensus_predictions

    return consensus_predictions([season], SCORING, purpose=PURPOSE, store=store,
                                 restricted_store=restricted_store).filter(pl.col("week") == week)


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
    bootstrap_frames: str = "full"  # "lines" when only the frames the 22 lines read were bootstrapped (D-116 B2)


#: D-116 B2 / A6: bootstrap only the line frames. On after the rehearsal-world proof showed zero differences at
#: 500 draws (worlds A, B, C) and 5,000 draws (A, C): data/reports/livetest_subset_proof.json
BOOTSTRAP_LINE_FRAMES_ONLY = True
#: frames the 22 lines read from the main bootstrap: the calibrated chain and the three line baselines' calibrated forms
LINE_FRAME_LABELS: tuple[str, ...] = (FOCUS + RB.CAL, *(b + RB.CAL for b in H.LINE_BASELINES))


def cluster_universes(frames: Mapping[str, pl.DataFrame], positions: Sequence[str]) -> dict[str, list[tuple]]:
    """Per position, the cluster universe ``run_bootstrap`` draws from for these frames (after ``prepare_frames``)."""
    prepared = RB.prepare_frames(frames)
    return {pos: M.cluster_universe([f.filter(pl.col("position") == pos) for f in prepared.values()])
            for pos in positions}


def line_frames(aligned: Mapping[str, pl.DataFrame], positions: Sequence[str]) -> tuple[dict[str, pl.DataFrame], str]:
    """The line frames when bootstrapping them alone is identical by construction, else every frame.

    ``run_bootstrap`` draws ``default_rng(seed).integers(0, K, (n_boot, K))``
    for each position from the cluster universe of the frames it is given,
    and computes each frame's metrics from that frame alone. The draws, and
    therefore every replicate of the line frames, are unchanged by leaving
    other frames out exactly when the universe of the line frames equals the
    universe of all frames at every position. The aligned frames share their
    keys, so this holds; it is checked here, and any difference falls back
    to the full computation.
    """
    sub = {m: f for m, f in aligned.items() if m in LINE_FRAME_LABELS}
    if not sub or len(sub) == len(aligned):
        return dict(aligned), "full"
    if cluster_universes(sub, positions) != cluster_universes(aligned, positions):
        return dict(aligned), "full"
    return sub, "lines"


def score_frames(ppr: pl.DataFrame, consensus: pl.DataFrame | None, *, n_boot: int = H.SPEC_N_BOOT,
                 seed: int = H.SPEC_SEED, metric_seed: int = H.SPEC_METRIC_SEED,
                 positions: Sequence[str] = POSITIONS, subset: bool = BOOTSTRAP_LINE_FRAMES_ONLY) -> Scored:
    """Exactly :func:`ffml.eval.report_holdout.compute_run`'s metric path on in-memory frames."""
    ppr = ppr.filter(pl.col("scoring") == SCORING) if "scoring" in ppr.columns else ppr
    present = set(ppr["model"].unique().to_list())
    models = RB.resolve_models(present, FOCUS)
    raw = [m for m in models.raw if m in present]
    ref = RB.reference_models(models, present)
    aligned, _, _ = RB.headline_frames(ppr, raw, present, ref)
    bounds = H.bounds_from_run(ppr)
    aligned = {m: RB.with_randomized_pit(f, bounds, metric_seed) for m, f in aligned.items()}
    boot_frames, used = (line_frames(aligned, positions) if subset else (aligned, "full"))
    main = RB.run_bootstrap(boot_frames, positions, lineup=False, n_boot=n_boot, seed=seed)
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
                  [b for b in H.LINE_BASELINES if b + RB.CAL in present], cons_boot is not None, used)


# --------------------------------------------------------------------------- #
# Storage of parts and week records
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# D-116: outcomes, data cutoff, pinned snapshots, flagged markers, pre-build check
# --------------------------------------------------------------------------- #

#: outcome columns shared by every model of a scored week (the chain's, D-116 A3)
OUTCOME_COLUMNS: tuple[str, ...] = ("actual", "active", "boom_actual", "bust_actual", "pos_min", "pos_max",
                                    "week_cutoff")
_OUTCOME_KEYS: tuple[str, ...] = ("entity_id", "season", "week", "scoring")


def with_outcomes_of(rows: pl.DataFrame, frame: pl.DataFrame) -> pl.DataFrame:
    """``rows`` (the comparators' week) with the outcome columns of the chain's rows in ``frame``.

    Every model of a sealed week is then scored against one set of outcomes,
    those built from the pinned snapshots. ``pred_cdf_at_actual`` is
    recomputed from the new outcomes when possible (it is not behind any line).
    """
    if rows.is_empty():
        return rows
    keys = [k for k in _OUTCOME_KEYS if k in rows.columns and k in frame.columns]
    cols = [c for c in OUTCOME_COLUMNS if c in frame.columns]
    src = frame.filter(pl.col("model") == FOCUS + RB.CAL) if "model" in frame.columns else frame
    src = (src if not src.is_empty() else frame).select(*keys, *cols).unique(keys, keep="first", maintain_order=True)
    out = rows.drop([c for c in (*cols, "pred_cdf_at_actual") if c in rows.columns])
    src = src.with_columns([pl.col(k).cast(out.schema[k]) for k in keys])
    out = out.join(src, on=keys, how="left")
    try:
        from ffml.eval.harness import with_pit

        out = with_pit(out)
    except Exception:  # noqa: BLE001 - optional column
        pass
    return out


def outcomes_sha256(frame: pl.DataFrame) -> str:
    """SHA-256 of the outcome rows a week was scored against (keys plus outcome columns, sorted, CSV)."""
    keys = [k for k in _OUTCOME_KEYS if k in frame.columns]
    cols = [c for c in OUTCOME_COLUMNS if c in frame.columns]
    rows = frame.filter(pl.col("model") == FOCUS + RB.CAL) if "model" in frame.columns else frame
    sub = rows.select(*keys, *cols).unique(keys, keep="first").sort(keys)
    return sha256_bytes(sub.write_csv().encode("utf-8"))


#: D-116 A3: 12:00 UTC on the first Friday at least 72 hours after the week's last kickoff
CUTOFF_LAG = timedelta(hours=72)
CUTOFF_HOUR_UTC = 12
FRIDAY = 4
#: D-121: a game still not final this long after the week's data cutoff is excluded and the week scored without it
EXCLUSION_LAG = timedelta(days=14)
EXCLUSION_REASON = "not final 14 days after the week's data cutoff (D-121)"


def data_cutoff(dim_game: pl.DataFrame, season: int, week: int) -> datetime | None:
    """The week's data cutoff (D-116 A3), or None when ``dim_game`` has no regular-season game for it."""
    g = dim_game.filter((pl.col("season") == season) & (pl.col("week") == week))
    if "game_type" in g.columns:
        g = g.filter(pl.col("game_type") == "REG")
    if g.is_empty() or "kickoff_utc" not in g.columns:
        return None
    last = parse_time(g["kickoff_utc"].max())
    if last is None:
        return None
    earliest = last + CUTOFF_LAG
    day = datetime(earliest.year, earliest.month, earliest.day, CUTOFF_HOUR_UTC, tzinfo=UTC)
    while day.weekday() != FRIDAY or day < earliest:
        day += timedelta(days=1)
    return day


def exclusion_after(cutoff: datetime) -> datetime:
    """D-121: from this point a game of the week that is still not final is excluded (the cutoff plus 14 days)."""
    return cutoff + EXCLUSION_LAG


#: the datasets weekly scoring reads, by store tier (processed, and the restricted consensus archive)
PINNED_DATASETS: tuple[tuple[str, str], ...] = (
    ("processed", "dim_game"), ("processed", "fact_player_game"), ("processed", "fact_dst_game"),
    ("restricted", "fact_ecr_weekly"),
)


def store_roots() -> dict[str, Path]:
    processed = Path(get_settings().processed_dir)
    return {"processed": processed, "restricted": processed / "restricted"}


def snapshot_time(snapshot_id: str) -> datetime | None:
    try:
        return datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def first_snapshot_at_or_after(root: Path, dataset: str, cutoff: datetime) -> str | None:
    from ffml.data.store import SnapshotStore

    ids = SnapshotStore(root).list_snapshots(dataset) if (Path(root) / dataset).is_dir() else []
    timed = sorted((t, sid) for sid in ids if (t := snapshot_time(sid)) is not None)
    return next((sid for t, sid in timed if t >= cutoff), None)


def choose_pins(cutoff: datetime, *, roots: Mapping[str, Path] | None = None,
                recorded: Mapping[str, str] | None = None,
                datasets: Sequence[tuple[str, str]] = PINNED_DATASETS) -> dict[str, str]:
    """``{"<tier>/<dataset>": snapshot id}``: the first snapshot at or after the cutoff for each dataset read.

    A rescore reuses ``recorded`` (the week's sealed record), so a week is
    always scored against the same snapshots. A tier whose dataset does not
    exist at all (no consensus archive) is left out. Raises
    :class:`RetryLater` while a dataset has no snapshot at or after the cutoff.
    """
    if recorded:
        return dict(recorded)
    roots = dict(roots or store_roots())
    pins: dict[str, str] = {}
    missing = []
    for tier, ds in datasets:
        if tier != "processed" and not (Path(roots[tier]) / ds).is_dir():
            continue
        sid = first_snapshot_at_or_after(roots[tier], ds, cutoff)
        if sid is None:
            missing.append(f"{tier}/{ds}")
        else:
            pins[f"{tier}/{ds}"] = sid
    if missing:
        raise RetryLater(f"no snapshot at or after the data cutoff {iso(cutoff)} yet for {missing}")
    return pins


@dataclass(frozen=True)
class WeekPins:
    """The snapshots a week is scored against and the point they were chosen from (D-116 A3, D-121)."""

    pins: dict[str, str]
    pin_from: datetime


def choose_week_pins(season: int, week: int, cutoff: datetime, *, now: datetime,
                     roots: Mapping[str, Path] | None = None, recorded: Mapping[str, Any] | None = None,
                     datasets: Sequence[tuple[str, str]] = PINNED_DATASETS) -> WeekPins:
    """The pin point of a week and its pins (D-116 A3 with D-121).

    - A rescore reuses the pins and the pin point recorded in the week's sealed JSON.
    - The first ``dim_game`` snapshot taken at or after the cutoff, before the cutoff plus
      :data:`EXCLUSION_LAG` and not after ``now``, in which every game of the week is final.
      When it is the first snapshot after the cutoff the pin point is the cutoff, so the pins are
      exactly those of D-116. Otherwise
      it is that snapshot's time; a refresh writes ``dim_game`` before the other tables, so every pin
      comes from that refresh.
    - Otherwise, once ``now`` has reached the cutoff plus the lag, that point. The games still not final
      in its ``dim_game`` are excluded (:func:`games_to_exclude`).
    - Before then, the cutoff. The week is not final in those pins and waits (exit 4).

    Raises :class:`RetryLater` while a dataset has no snapshot at or after the pin point.
    """
    if recorded and recorded.get("data_snapshots"):
        return WeekPins(dict(recorded["data_snapshots"]), parse_time(recorded.get("pin_from_utc")) or cutoff)
    roots = dict(roots or store_roots())
    after = exclusion_after(cutoff)
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(roots["processed"])
    ids = store.list_snapshots("dim_game") if (Path(roots["processed"]) / "dim_game").is_dir() else []
    timed = sorted((t, sid) for sid in ids
                   if (t := snapshot_time(sid)) is not None and cutoff <= t < after and t <= now)
    for i, (t, sid) in enumerate(timed):
        try:
            games = store.read("dim_game", seasons=[season], snapshot_id=sid)
        except FileNotFoundError:
            continue
        if week_status(games, season, week).is_final:
            point = cutoff if i == 0 else t
            return WeekPins(choose_pins(point, roots=roots, datasets=datasets), point)
    point = after if now >= after else cutoff
    return WeekPins(choose_pins(point, roots=roots, datasets=datasets), point)


def games_to_exclude(status: WeekStatus, pin_from: datetime, cutoff: datetime) -> tuple[str, ...]:
    """D-121: the ids of the games the week is scored without; ``()`` when every game is final.

    Raises :class:`RetryLater` while a game is not final and the pin point is before the cutoff plus
    :data:`EXCLUSION_LAG`, and :class:`ScoreRefused` when no game of the week is final at all.
    """
    wk = week_key(status.season, status.week)
    if status.is_final:
        return ()
    after = exclusion_after(cutoff)
    if pin_from < after:
        raise RetryLater(f"{wk} is not final in the pinned dim_game ({status.final} of {status.games} games); a game "
                         f"still not final at {iso(after)} is excluded and the week scored without it (D-121)")
    if status.final == 0:
        raise ScoreRefused(f"{wk}: no game of the week is final in the pinned dim_game at {iso(pin_from)}; D-121 "
                           "excludes single games, never a whole week")
    if len(status.unfinished) != status.games - status.final:
        raise ScoreRefused(f"{wk}: the pinned dim_game lacks the game ids of the games not final")
    return tuple(status.unfinished)


@dataclass(frozen=True)
class WeekPlan:
    """What ``--week`` scores against: the cutoff, the pins, the pinned ``dim_game`` and the excluded games."""

    cutoff: datetime
    pins: dict[str, str]
    pin_from: datetime
    stores: dict[str, Any]
    dim_game: pl.DataFrame
    status: WeekStatus
    excluded: tuple[str, ...]


def plan_week(season: int, week: int, latest_dim_game: pl.DataFrame, *, now: datetime,
              roots: Mapping[str, Path] | None = None, record: Mapping[str, Any] | None = None) -> WeekPlan:
    """The scoring plan of ``--week`` (D-116 A3, D-121). ``record`` is the week's sealed JSON when it is rescored.

    Raises :class:`RetryLater` (exit 4) before the cutoff, while a dataset has no snapshot at or after
    the pin point, and while a game waits; :class:`ScoreRefused` (exit 3) for a week not in ``dim_game``.
    """
    wk = week_key(season, week)
    record = dict(record or {})
    rescore = bool(record.get("data_snapshots"))
    cutoff = (parse_time(record.get("data_cutoff")) if rescore else None) or data_cutoff(latest_dim_game, season, week)
    if cutoff is None:
        raise ScoreRefused(f"{wk} has no regular-season game in dim_game")
    if now < cutoff:
        raise RetryLater(f"the data cutoff for {wk} is {iso(cutoff)}")
    wp = choose_week_pins(season, week, cutoff, now=now, roots=roots, recorded=record if rescore else None)
    stores = pinned_stores(wp.pins, roots)
    dim_game = stores["processed"].read("dim_game", seasons=[season])
    status = week_status(dim_game, season, week)
    excluded = games_to_exclude(status, wp.pin_from, cutoff)
    return WeekPlan(cutoff, dict(wp.pins), wp.pin_from, stores, dim_game, status, excluded)


def excluded_teams(dim_game: pl.DataFrame, season: int, week: int, game_ids: Sequence[str]) -> tuple[str, ...]:
    """The teams of the excluded games (``home_team`` and ``away_team`` when ``dim_game`` has them)."""
    if not game_ids or "game_id" not in dim_game.columns:
        return ()
    g = dim_game.filter((pl.col("season") == season) & (pl.col("week") == week)
                        & pl.col("game_id").cast(pl.String).is_in([str(x) for x in game_ids]))
    teams = {str(t) for col in ("home_team", "away_team") if col in g.columns for t in g[col].to_list() if t}
    return tuple(sorted(teams))


def without_games(rows: pl.DataFrame | None, game_ids: Sequence[str], teams: Sequence[str], season: int,
                  week: int) -> pl.DataFrame | None:
    """``rows`` without the player, team and DST rows of the excluded games of one week (D-121).

    A row goes when its ``game_id`` is excluded, or, within the week, when its team (or a DST entity)
    played in an excluded game: every team plays at most one game a week.
    """
    if rows is None or rows.is_empty() or not game_ids:
        return rows
    ids, tm = [str(g) for g in game_ids], [str(t) for t in teams]
    parts = []
    if "game_id" in rows.columns:
        parts.append(pl.col("game_id").cast(pl.String).is_in(ids))
    if tm and "team" in rows.columns:
        parts.append(pl.col("team").cast(pl.String).is_in(tm))
    if tm and "entity_id" in rows.columns and "position" in rows.columns:
        parts.append((pl.col("position") == "DST") & pl.col("entity_id").cast(pl.String).is_in(tm))
    if not parts:
        raise ScoreRefused("the rows carry neither game_id nor team, so the excluded games cannot be removed (D-121)")
    hit = parts[0]
    for p in parts[1:]:
        hit = hit | p
    if "season" in rows.columns and "week" in rows.columns:
        hit = hit & (pl.col("season") == season) & (pl.col("week") == week)
    return rows.filter(~hit.fill_null(False))


def excluded_records(dim_game: pl.DataFrame, season: int, week: int, game_ids: Sequence[str]) -> list[dict[str, Any]]:
    """``excluded_games`` as recorded: the game id, its teams, its status in the pinned ``dim_game`` and the reason."""
    out = []
    for gid in game_ids:
        g = dim_game.filter((pl.col("season") == season) & (pl.col("week") == week)
                            & (pl.col("game_id").cast(pl.String) == str(gid)))
        status = str(g["status"][0]) if "status" in g.columns and g.height else None
        out.append({"game_id": str(gid), "teams": list(excluded_teams(dim_game, season, week, [gid])),
                    "status_in_pinned_dim_game": status, "reason": EXCLUSION_REASON})
    return out


def pinned_stores(pins: Mapping[str, str], roots: Mapping[str, Path] | None = None) -> dict[str, Any]:
    """Snapshot stores that read only the pinned snapshot of each dataset; reading anything else fails."""
    from ffml.data.store import SnapshotStore

    class PinnedStore(SnapshotStore):
        def __init__(self, root: Path, tier: str) -> None:
            super().__init__(root)
            self.tier = tier
            self.pins = {k.split("/", 1)[1]: v for k, v in pins.items() if k.startswith(tier + "/")}

        def resolve_snapshot_id(self, dataset: str, snapshot_id: str | None = None) -> str:
            if snapshot_id is None:
                if dataset not in self.pins:
                    raise ScoreRefused(f"{self.tier}/{dataset} is read but has no pinned snapshot (D-116 A3)")
                snapshot_id = self.pins[dataset]
            return super().resolve_snapshot_id(dataset, snapshot_id)

    roots = dict(roots or store_roots())
    out: dict[str, Any] = {tier: PinnedStore(Path(roots[tier]), tier) for tier in roots}
    if not any(k.startswith("restricted/") for k in pins):
        out["restricted"] = None
    return out


def changelog_at(rev: str, root: Path) -> set[str]:
    """Commits named in the change log as it stood at ``rev`` (empty when the file did not exist there)."""
    out = _run_git(root, "show", f"{rev}:{CHANGELOG_DOC}")
    return changelog_commits(out.stdout) if out.returncode == 0 else set()


def flagged_marker(source: BranchSource, w: tuple[int, int], marker: Mapping[str, Any] | None,
                   records: Mapping[tuple[int, int], Any]) -> dict[str, Any]:
    """D-116 A5: a MISSED marker for a committed or sealed week, with when and why it was flagged."""
    wk = week_key(*w)
    added = source.added_by(f"{wk}/{MISSED_NAME}")
    reasons = []
    if source.read(f"{wk}/{COMMITMENT_NAME}") is not None:
        reasons.append("a commitment.json on the branch")
    if source.read(f"{SEALED_BRANCH_DIR}/{wk}.sha256") is not None:
        reasons.append("a sealed-hash record on the branch")
    if w in records:
        reasons.append("a local sealed record")
    m = dict(marker or {})
    return {"week": wk, "commit": added[0] if added else None, "commit_time": added[1] if added else None,
            "created_at": m.get("created_at"), "recorded_at": m.get("recorded_at"),
            "reason": "the week has " + " and ".join(reasons) + "; it is scored and the marker is not used"}


def _prereg_at(rev: str, root: Path) -> dict[str, Any] | None:
    out = _run_git(root, "show", f"{rev}:{PREREG_DOC}")
    if out.returncode != 0:
        return None
    m = re.search(r"```json\s*\n(.*?)\n```", out.stdout, flags=re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def resolve_frozen_commit(to: str, root: Path, prereg_data: Mapping[str, Any] | None = None
                          ) -> tuple[str | None, dict[str, Any] | None, str]:
    """``(X, pre-registration, source)``: the first filled ``commit`` in --prereg, at --to, on main, on origin/main.

    The pinned worktree may sit at X itself, whose copy still carries the
    placeholder; main (a ref the worktree shares) carries the filled field.
    """
    for label, data in (("--prereg", prereg_data), (f"--to {to[:12]}", _prereg_at(to, root)),
                        ("main", _prereg_at("main", root)), ("origin/main", _prereg_at("origin/main", root))):
        c = str((data or {}).get("commit") or "")
        if data and re.fullmatch(r"[0-9a-fA-F]{7,40}", c):
            full = H._full_sha(c, root)
            if full:
                return full, dict(data), label
    return None, None, "no pre-registration with a filled commit in --prereg, at --to, on main or on origin/main"


def prebuild_check(from_sha: str | None, to_sha: str, root: Path = PROJECT_ROOT,
                   prereg_data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """D-116 B1 / A1: every commit in from..to is logged in the change log at --to, and none touches a frozen path.

    An empty ``from_sha`` means the pre-registered X. A commit that only
    appends to the change log needs no entry (:func:`_changelog_append_only`).
    Any path not listed as allowed is frozen (the pre-registration's path policy).
    """
    res: dict[str, Any] = {"ok": False, "uncovered": [], "frozen": [], "detail": "", "from": None, "to": None,
                           "x": None, "commits": 0}
    fto = H._full_sha(str(to_sha or ""), root)
    if fto is None:
        res["detail"] = f"--to {to_sha!r} is not a known commit"
        return res
    x, data, source = resolve_frozen_commit(fto, root, prereg_data)
    if x is None or data is None:
        res["detail"] = source
        return res
    ffrom = x if not from_sha else H._full_sha(str(from_sha), root)
    res.update({"x": x, "to": fto, "from": ffrom})
    if ffrom is None:
        res["detail"] = f"--from {from_sha!r} is not a known commit"
        return res
    if _run_git(root, "merge-base", "--is-ancestor", x, fto).returncode != 0:
        res["detail"] = f"--to {fto[:12]} does not descend from the pre-registered X {x[:12]}"
        return res
    if _run_git(root, "merge-base", "--is-ancestor", ffrom, fto).returncode != 0:
        res["detail"] = f"--from {ffrom[:12]} is not an ancestor of --to {fto[:12]}"
        return res
    listed = _run_git(root, "rev-list", f"{ffrom}..{fto}")
    commits = listed.stdout.split() if listed.returncode == 0 else []
    logged = changelog_at(fto, root)
    allowed, frozen = allowed_prefixes(data)
    uncovered = [c for c in commits if not any(c.startswith(t) for t in logged) and not _changelog_append_only(c, root)]
    hits = []
    for c in commits:
        names = _run_git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-m", c).stdout.splitlines()
        bad = sorted({n.strip().replace("\\", "/") for n in names
                      if n.strip() and not path_allowed(n.strip().replace("\\", "/"), allowed, frozen)})
        if bad:
            hits.append({"commit": c, "paths": bad})
    ok = not uncovered and not hits
    res.update({"ok": ok, "uncovered": uncovered, "frozen": hits, "commits": len(commits)})
    res["detail"] = (f"{len(commits)} commit(s) in {ffrom[:12]}..{fto[:12]}; X {x[:12]} from {source}; "
                     + ("all logged in the change log at --to and none on a frozen path" if ok else
                        f"{len(uncovered)} without a change-log entry at --to; {len(hits)} touching a frozen path"))
    return res


def default_reports_dir() -> Path:
    """``<settings.reports_dir>/livetest`` (``FFML_REPORTS_DIR``; the runner points it outside the pinned worktree)."""
    return Path(get_settings().reports_dir) / "livetest"


def scoring_root() -> Path:
    return get_settings().data_dir / "livetest" / "scoring"


def part_path(root: Path, season: int, week: int) -> Path:
    return root / SEALED_DIR / f"{week_key(season, week)}.parquet"


def record_path(root: Path, season: int, week: int) -> Path:
    return root / SEALED_DIR / f"{week_key(season, week)}.json"


def seal_path(root: Path, season: int, week: int) -> Path:
    return root / SEALED_DIR / f"{week_key(season, week)}.sha256"


def read_seal(text: str) -> dict[str, str]:
    """``{file name: sha256}`` from a ``.sha256`` file (``<sha256>  <name>`` per line)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            out[parts[-1]] = parts[0]
    return out


def verify_seals(source: BranchSource, root: Path, weeks: Iterable[tuple[int, int]]) -> list[Check]:
    """Every sealed file of every scored week hashes as the branch copy of its ``.sha256`` says."""
    checks: list[Check] = []
    for s_, w_ in weeks:
        wk = week_key(s_, w_)
        raw = source.read(f"{SEALED_BRANCH_DIR}/{wk}.sha256")
        if raw is None:
            checks.append(Check(f"{wk} sealed hashes", False, f"{SEALED_BRANCH_DIR}/{wk}.sha256 is not on the branch"))
            continue
        want = read_seal(raw.decode("utf-8"))
        local = seal_path(root, s_, w_)
        local_list = read_seal(local.read_text(encoding="utf-8")) if local.is_file() else {}
        problems = []
        if not want:
            problems.append("branch file lists no hashes")
        if local_list and local_list != want:
            problems.append("local .sha256 differs from the branch copy")
        for name, sha in want.items():
            f = root / SEALED_DIR / name
            if not f.is_file():
                problems.append(f"{name} missing")
            elif sha256_file(f) != sha:
                problems.append(f"{name} sha256 differs from the branch record")
        checks.append(Check(f"{wk} sealed hashes", not problems,
                            f"{len(want)} sealed files match the branch record" if not problems else "; ".join(problems)))
    return checks


def consensus_part_path(root: Path, season: int, week: int) -> Path:
    return root / SEALED_DIR / f"{week_key(season, week)}_consensus.parquet"


def load_consensus_parts(root: Path, weeks: Iterable[tuple[int, int]]) -> pl.DataFrame | None:
    """The sealed consensus rows of ``weeks`` (a week without a scrape has none)."""
    frames = [pl.read_parquet(cp) for s_, w_ in weeks if (cp := consensus_part_path(root, s_, w_)).is_file()]
    return pl.concat(frames, how="diagonal_relaxed") if frames else None


def write_sealed(root: Path, rec: WeekRecord, frame: pl.DataFrame, extra: Mapping[str, Any],
                 metrics: Mapping[str, Any], consensus: pl.DataFrame | None = None) -> dict[str, str]:
    """Write the week's sealed parquet, consensus rows and JSON, then the ``.sha256`` list the runner pushes."""
    pp = part_path(root, rec.season, rec.week)
    pp.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(pp)
    cp = consensus_part_path(root, rec.season, rec.week)
    if consensus is not None and not consensus.is_empty():
        consensus.write_parquet(cp)
    elif cp.exists():
        cp.unlink()
    rp = record_path(root, rec.season, rec.week)
    payload = {**rec.as_dict(), **extra, "part": pp.name, "part_sha256": sha256_file(pp),
               "consensus_part": cp.name if cp.exists() else None,
               "consensus_part_sha256": sha256_file(cp) if cp.exists() else None,
               "scored_at": iso(utcnow()), "sealed_metrics": dict(metrics)}
    rp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    seals = {pp.name: sha256_file(pp), **({cp.name: sha256_file(cp)} if cp.exists() else {}), rp.name: sha256_file(rp)}
    seal_path(root, rec.season, rec.week).write_text(
        "".join(f"{sha}  {name}\n" for name, sha in seals.items()), encoding="utf-8")
    return seals


def week_status_path(root: Path, season: int, week: int) -> Path:
    return root / "weeks" / f"{week_key(season, week)}.json"


def write_week_status(root: Path, rec: WeekRecord, extra: Mapping[str, Any], seals: Mapping[str, str]) -> Path:
    """``weeks/<week>.json``: the week's public status for the runner (snapshots, hashes, check names); no metric."""
    path = week_status_path(root, rec.season, rec.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "week": week_key(rec.season, rec.week),
        "dim_game_snapshot": extra.get("dim_game_snapshot"),
        "processed_snapshots": extra.get("processed_snapshots") or {},
        "data_cutoff_utc": extra.get("data_cutoff"),
        "data_snapshots": extra.get("data_snapshots") or {},
        "pin_from_utc": extra.get("pin_from_utc"),
        "exclusion_after_utc": extra.get("exclusion_after_utc"),
        "excluded_games": list(extra.get("excluded_games") or []),
        "outcome_rows_sha256": extra.get("outcome_rows_sha256"),
        "commitment_sha256": rec.commitment_sha256,
        "sealed": dict(seals),
        "checks": [{"name": c.name, "ok": c.ok} for c in rec.checks],
        "scored_at": iso(utcnow()),
    }
    path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return path


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
    snapshot_ids: Mapping[str, Any] | None = None,
    weekly_sensitivity: bool = False,
    sensitivity_n_boot: int = SENSITIVITY_N_BOOT,
    data_cutoff: datetime | None = None,
    data_snapshots: Mapping[str, Any] | None = None,
    excluded_games: Sequence[str] = (),
    pin_from: datetime | None = None,
) -> tuple[WeekRecord, Path]:
    """Verify, score into the sealed store and write the verification report (no metric leaves this function).

    Raises :class:`ScoreRefused` on any refusal. The sealed JSON holds this
    week's point metrics and line results and the cumulative 22-line results
    over the weeks sealed so far (500 draws; also ``sensitivity_n_boot`` draws
    when ``weekly_sensitivity``); nothing of it is rendered or printed.
    """
    root = Path(root) if root is not None else scoring_root()
    reports_dir = Path(reports_dir) if reports_dir is not None else default_reports_dir()
    c = load_commitment(source, season, week)
    status = week_status(dim_game, season, week)
    summary_bytes = source.read(f"{week_key(season, week)}/{SUMMARY_NAME}")
    rec, raw_path, summary = verify_week(
        c, branch_summary=summary_bytes, push_record=push_record, status=status, roots=roots,
        allow_rehearsal=allow_rehearsal, expected_commit=_frozen_commit(prereg),
        prereg=prereg.data if prereg else None, repo_root=repo_root,
        branch_commit=source.commit_of(c.rel) if source.worktree is None or (Path(source.worktree) / ".git").exists() else None,
        excluded=excluded_games,
    )
    if rec.refusals:
        raise ScoreRefused("; ".join(rec.refusals))
    assert raw_path is not None and summary is not None
    import io

    cal_rows = pl.read_parquet(io.BytesIO(summary))
    raw_rows = pl.read_parquet(raw_path)
    excluded = tuple(str(g) for g in excluded_games)
    teams = excluded_teams(dim_game, season, week, excluded)
    # D-121: the excluded games' player, team and DST rows leave the chain, the comparators and the consensus
    frame = without_games(week_frame(raw_rows, cal_rows, season, week, outcomes), excluded, teams, season, week)
    if frame is None or frame.is_empty():
        raise ScoreRefused(f"no rows of {week_key(season, week)} remain after excluding {list(excluded)}")
    parts = [frame]
    if baselines is not None and not baselines.is_empty():
        # one set of outcomes for every model: the comparators' rows take the chain's outcome columns (D-116 A3)
        week_base = with_outcomes_of(baselines.filter((pl.col("season") == season) & (pl.col("week") == week)), frame)
        parts.append(without_games(week_base, excluded, teams, season, week))
    week_all = pl.concat(parts, how="diagonal_relaxed")
    week_cons = without_games(consensus.filter((pl.col("season") == season) & (pl.col("week") == week)),
                              excluded, teams, season, week) \
        if consensus is not None and not consensus.is_empty() else None
    scored = score_frames(week_all, week_cons, n_boot=n_boot)
    window = prereg_window(prereg.data if prereg else {})
    earlier_weeks = [w for w in window if w != (season, week)]
    earlier, found = load_parts(root, earlier_weeks)
    this_ppr = week_all.filter(pl.col("scoring") == SCORING) if "scoring" in week_all.columns else week_all
    so_far = pl.concat([f for f in (earlier, this_ppr) if not f.is_empty()], how="diagonal_relaxed")
    cons_parts = [f for f in (load_consensus_parts(root, found), week_cons) if f is not None and not f.is_empty()]
    cons_so_far = pl.concat(cons_parts, how="diagonal_relaxed") if cons_parts else None
    cumulative = score_frames(so_far, cons_so_far, n_boot=n_boot)
    metrics: dict[str, Any] = {
        "week_points": {pos: {m: dict(v) for m, v in scored.main.points.get(pos, {}).items()} for pos in POSITIONS},
        "week_lines": [{"id": r.line.id, "passed": r.passed, "detail": r.detail} for r in scored.results],
        "cumulative_weeks": sorted(found + [(season, week)]),
        "cumulative_lines_500": [{"id": r.line.id, "passed": r.passed, "detail": r.detail} for r in cumulative.results],
        "cumulative_lines_sensitivity": None,
    }
    if weekly_sensitivity:
        sens = score_frames(so_far, cons_so_far, n_boot=sensitivity_n_boot)
        metrics["cumulative_lines_sensitivity"] = {"n_boot": sensitivity_n_boot, "lines": [
            {"id": r.line.id, "passed": r.passed, "detail": r.detail} for r in sens.results]}
    extra = {"dim_game_snapshot": dim_game_id, "processed_snapshots": dict(snapshot_ids or {}),
             "rows": frame.height, "comparators": scored.comparators_present,
             "consensus": scored.consensus_present, "n_boot": n_boot, "scoring": SCORING,
             "window_weeks_scored": sorted(found + [(season, week)]), "window_total": len(window),
             "data_cutoff": iso(data_cutoff), "data_snapshots": dict(data_snapshots or {}),
             "outcome_rows_sha256": outcomes_sha256(frame), "bootstrap_frames": scored.bootstrap_frames,
             "pin_from_utc": iso(pin_from or data_cutoff),
             "exclusion_after_utc": iso(exclusion_after(data_cutoff)) if data_cutoff else None,
             "excluded_games": excluded_records(dim_game, season, week, excluded)}
    seals = write_sealed(root, rec, week_all, extra, metrics, consensus=week_cons)
    write_week_status(root, rec, extra, seals)
    text = render_verification(rec, extra, seals)
    assert "Verdict:" not in text
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{week_key(season, week)}_verification.md"
    out.write_text(text, encoding="utf-8")
    return rec, out


#: checks whose detail may carry numbers that are not hashes, timestamps or counts (calibrator strings)
_OPAQUE_CHECKS: tuple[str, ...] = ("calibrator:",)


def _public_detail(c: Check) -> str:
    if c.name.startswith(_OPAQUE_CHECKS):
        return "equal to the pre-registration" if c.ok else "differs from the pre-registration"
    return c.detail


def _checks_table(checks: Sequence[Check], public: bool = False) -> str:
    return RB._md_table(["check", "result", "detail"],
                        [[c.name, "PASS" if c.ok else "FAIL", _public_detail(c) if public else c.detail]
                         for c in checks])


def _lines_table(results: Sequence[LineResult]) -> str:
    return RB._md_table(["line", "statement", "status (information only)", "detail"],
                        [[r.line.id, r.line.describe(FOCUS), "pass" if r.passed else "not passed", r.detail]
                         for r in results])


def render_verification(rec: WeekRecord, extra: Mapping[str, Any], seals: Mapping[str, str]) -> str:
    """The weekly report: verification status only (D-111). No metric, line result or ranking."""
    L: list[str] = []
    a = L.append
    wk = week_key(rec.season, rec.week)
    a(f"# Live test, {wk}: verification only")
    a("")
    a("Interim results are sealed until the final evaluation (D-111). This report states what was verified "
      "and which sealed files were written; it carries no metric, line result or ranking, and no weekly report "
      "ever prints a verdict line.")
    a("")
    a("## Commitment and timing")
    a("")
    a(RB._md_table(["item", "value"], [
        ["commitment sha256", rec.commitment_sha256], ["digest", rec.digest], ["branch commit", rec.branch_commit or "n/a"],
        ["code commit", rec.code_commit], ["model version", rec.model_version],
        ["created at", rec.created_at], ["committed first kickoff", rec.first_kickoff],
        ["dim_game first kickoff", rec.dim_game_first_kickoff], ["binding deadline", rec.deadline],
        ["GitHub push timestamp", f"{rec.external_timestamp} ({rec.external_source})"],
        ["games final in dim_game", f"{rec.games_final} of {rec.games} (snapshot {extra.get('dim_game_snapshot')})"],
        ["processed snapshots (LATEST at scoring)",
         "; ".join(f"{k} {v}" for k, v in (extra.get("processed_snapshots") or {}).items()) or "n/a"],
        ["data cutoff (D-116 A3)", extra.get("data_cutoff") or "n/a"],
        ["exclusion point: the cutoff plus 14 days (D-121)", extra.get("exclusion_after_utc") or "n/a"],
        ["pins taken from", extra.get("pin_from_utc") or "n/a"],
        ["games excluded (D-121)", "; ".join(f"{g['game_id']} ({g['reason']})" for g in extra.get("excluded_games") or [])
         or "none"],
        ["snapshots scored against (first at or after the pin point)",
         "; ".join(f"{k} {v}" for k, v in (extra.get("data_snapshots") or {}).items()) or "n/a"],
        ["outcome rows sha256", extra.get("outcome_rows_sha256") or "n/a"],
        ["files verified", str(rec.files_verified)], ["rows sealed", str(extra.get("rows"))],
        ["comparators present", ", ".join(extra.get("comparators") or []) or "none"],
        ["consensus present", "yes" if extra.get("consensus") else "no"],
        ["weeks sealed so far", ", ".join(week_key(s_, w_) for s_, w_ in extra.get("window_weeks_scored", []))
         + f" ({len(extra.get('window_weeks_scored', []))} of {extra.get('window_total')})"],
    ]))
    a("")
    a("## Checks")
    a("")
    a(_checks_table(rec.checks, public=True))
    a("")
    a("## Sealed files")
    a("")
    a(RB._md_table(["file", "sha256"], [[name, sha] for name, sha in seals.items()]))
    a("")
    a(f"The runner pushes `{wk}.sha256` to the branch under `{SEALED_BRANCH_DIR}/`; the final evaluation verifies "
      "every sealed file against that record before reading it.")
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
                              repo_root: Path = PROJECT_ROOT, changelog: Path | None = None,
                              root: Path | None = None) -> list[Check]:
    """The pre-metric checks of the final evaluation (the live-test counterpart of ``integrity_checks``)."""
    data = prereg.data
    checks: list[Check] = []
    log_path = Path(changelog) if changelog is not None else repo_root / CHANGELOG_DOC
    logged = changelog_commits(log_path.read_text(encoding="utf-8")) if log_path.is_file() else set()
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
        missing = uncovered_commits(commit, head, repo_root, logged)
        checks.append(Check("final evaluation change log", not missing,
                            f"{len(logged)} logged commits; every commit in X..HEAD is logged" if not missing
                            else f"commits in X..HEAD without a change-log entry: {[m[:12] for m in missing[:5]]}"))
    scored = [w for w in window if w in records]
    missed = [w for w in window if w in misses]
    unknown = [w for w in window if w not in records and w not in misses]
    checks.append(Check("window complete", not unknown,
                        f"{len(scored)} scored, {len(missed)} missed, {len(unknown)} unaccounted {unknown[:3]}"))
    raw_allowed = (data.get("missed_weeks") or {}).get("allowed")
    rule_text = str(data.get("miss_rule") or data.get("one_miss_rule") or "D-106")[:160]
    if isinstance(raw_allowed, bool) or not isinstance(raw_allowed, int) or raw_allowed < 0:
        checks.append(Check("missed weeks (miss rule)", False,
                            f"missed_weeks.allowed must be a non-negative integer in the pre-registration, got "
                            f"{raw_allowed!r}"))
    else:
        allowed_misses = raw_allowed
        checks.append(Check("missed weeks (miss rule)", len(missed) <= allowed_misses,
                            f"{len(missed)} missed week(s) {[week_key(*w) for w in missed]}, {allowed_misses} allowed"
                            + ("; excluded, the window is not extended" if 0 < len(missed) <= allowed_misses else
                               "; more than allowed: INVALID" if len(missed) > allowed_misses else "")
                            + f"; rule: {rule_text}"))
    overlap = [w for w in window if w in records and w in misses]
    checks.append(Check("no week both scored and missed", not overlap, f"{[week_key(*w) for w in overlap]}"))
    for (s, w), r in sorted(records.items()):
        wk = week_key(s, w)
        failed = [c for c in r.get("checks", []) if not c.get("ok")]
        if commit and r.get("code_commit"):
            rel = commit_relation_allowed(commit, r["code_commit"], repo_root, allowed, frozen)
            failed = [c for c in failed if c["name"] != "code commit"] + ([] if rel.ok else [{"name": "code commit", "detail": rel.detail}])
        if commit and r.get("code_commit") and H._full_sha(str(r["code_commit"]), repo_root) not in (None, H._full_sha(commit, repo_root)):
            # D-116 A2: logged before first use, i.e. in the change log as it stood at the build commit
            at_build = changelog_at(str(r["code_commit"]), repo_root)
            missing = uncovered_commits(commit, str(r["code_commit"]), repo_root, at_build)
            checks.append(Check(f"{wk} change log", not missing,
                                "every commit between X and the build commit was in the change log at the build "
                                "commit" if not missing
                                else f"not in the change log at the build commit: {[m[:12] for m in missing[:5]]}"))
        ex = list(r.get("excluded_games") or [])
        if ex:
            pf, cut = parse_time(r.get("pin_from_utc")), parse_time(r.get("data_cutoff"))
            ok_ex = pf is not None and cut is not None and pf >= exclusion_after(cut)
            checks.append(Check(f"{wk} games excluded (D-121)", ok_ex,
                                f"scored without {[g.get('game_id') for g in ex]}: pins from {r.get('pin_from_utc')}, "
                                f"data cutoff {r.get('data_cutoff')}"
                                + ("" if ok_ex else "; the pin point is before the cutoff plus 14 days")))
        checks.append(Check(f"{wk} commitment", not failed,
                            f"commitment {str(r.get('commitment_sha256'))[:12]}, GitHub push {r.get('external_timestamp')}, "
                            f"kickoff {r.get('first_kickoff')}, {r.get('files_verified')} files"
                            + ("" if not failed else "; FAILED: " + "; ".join(f"{c['name']} ({c['detail']})" for c in failed[:4]))))
        part = part_path(Path(root) if root is not None else scoring_root(), s, w)
        if r.get("part_sha256") and part.is_file():
            checks.append(Check(f"{wk} sealed part", sha256_file(part) == r["part_sha256"],
                                f"{part.name} sha256 unchanged since scoring"))
    return checks


def branch_record_checks(source: BranchSource, records: Mapping[tuple[int, int], Mapping[str, Any]],
                         weeks: Sequence[tuple[int, int]], branch_ref: Mapping[str, Any] | None) -> list[Check]:
    """QA-F29: each scored week's commitment and summary on the branch record equal what was scored."""
    out: list[Check] = []
    if branch_ref is not None:
        out.append(Check("branch record", bool(branch_ref.get("ok")), str(branch_ref.get("detail", ""))))
    for w in weeks:
        r = records[w]
        wk = week_key(*w)
        c_raw = source.read(f"{wk}/{COMMITMENT_NAME}")
        problems = []
        if c_raw is None:
            problems.append("commitment.json is not on the branch record")
        elif sha256_bytes(c_raw) != r.get("commitment_sha256"):
            problems.append("the branch commitment differs from the scored one")
        summ = source.read(f"{wk}/{SUMMARY_NAME}")
        if summ is not None and r.get("summary_sha256") and sha256_bytes(summ) != r.get("summary_sha256"):
            problems.append("the branch player_summary differs from the scored one")
        out.append(Check(f"{wk} branch commitment", not problems,
                         "the branch record's commitment (and summary) equal the scored ones" if not problems
                         else "; ".join(problems)))
    return out


def fetch_branch_ref(repo_root: Path = PROJECT_ROOT, remote: str = REMOTE, branch: str = BRANCH) -> dict[str, Any]:
    """Fetch the commitments branch and compare the tracking ref with the remote's head (QA-F29)."""
    ref = f"{remote}/{branch}"
    fetched = _run_git(repo_root, "fetch", "--quiet", remote, f"+refs/heads/{branch}:refs/remotes/{ref}")
    if fetched.returncode != 0:
        raise FetchFailed(f"the final is refused: git fetch {remote} {branch} failed: {fetched.stderr.strip()[:200]}")
    local = _run_git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}").stdout.strip()
    listed = _run_git(repo_root, "ls-remote", remote, f"refs/heads/{branch}")
    remote_line = listed.stdout.split() if listed.returncode == 0 else []
    remote_sha = remote_line[0] if remote_line else ""
    ok = bool(local) and local == remote_sha
    if not ok:  # D-116 A4: retryable; no verdict is recorded
        raise FetchFailed(f"the final is refused: {ref} {local[:12] or '?'} does not equal the remote head "
                          f"{remote_sha[:12] or '?'} (ls-remote exit {listed.returncode})")
    return {"ref": ref, "sha": local, "remote_sha": remote_sha, "ok": ok, "fetched_at": iso(datetime.now(UTC)),
            "detail": f"{ref} {local[:12]} equals the remote head" if ok else
            f"{ref} {local[:12] or '?'} differs from the remote head {remote_sha[:12] or '?'}"}


def line_intervals(sc: Scored, line: H.PreregLine, digits: int = 3) -> str:
    """Compact text of the interval(s) behind one line."""
    f = FOCUS + RB.CAL
    if line.kind == "beats_baseline":
        other = f"{line.comparator}{RB.CAL}"
        if not (sc.main.has(line.position, f) and sc.main.has(line.position, other)):
            return "n/a"
        return "; ".join(f"{RB.METRIC_LABEL[m]} [{RB._num(d.lo, digits, True)}, {RB._num(d.hi, digits, True)}]"
                         for m in H.BEATS_METRICS for d in [sc.main.delta(line.position, f, other, m)])
    if line.kind == "startsit_consensus":
        if sc.cons is None or not sc.cons.has(line.position, f):
            return "n/a"
        d = sc.cons.delta(line.position, f, RB.CONSENSUS, "start_sit_common")
        return f"[{RB._num(d.lo, digits, True)}, {RB._num(d.hi, digits, True)}]"
    return RB._num(sc.main.value(line.position, f, "coverage_tie_aware"), digits)


SensitivityFn = Callable[[pl.DataFrame, pl.DataFrame | None], Scored]


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
    sensitivity: bool = True,
    sensitivity_n_boot: int = SENSITIVITY_N_BOOT,
    sensitivity_fn: SensitivityFn | None = None,
    require_seals: bool = True,
    branch_ref: Mapping[str, Any] | None = None,
) -> tuple[str, Path]:
    """The one evaluation of the window. Returns the verdict line and the report path.

    Refused until every window week is final and each is sealed or marked
    MISSED, and when any sealed file does not hash as the branch record says.
    The verdict uses the 500-draw, seed-0 intervals only; the
    ``sensitivity_n_boot``-draw intervals are reported beside them and
    stored, and any line whose PASS/FAIL would differ is listed.
    """
    root = Path(root) if root is not None else scoring_root()
    reports_dir = Path(reports_dir) if reports_dir is not None else default_reports_dir()
    window = prereg_window(prereg.data)
    last = window[-1]
    clock = now or utcnow()

    def settled(s_: int, w_: int) -> bool:
        """Final in dim_game, or past the cutoff plus 14 days, when its games still not final are excluded (D-121)."""
        st = week_status(dim_game, s_, w_)
        if st.is_final:
            return True
        c = data_cutoff(dim_game, s_, w_)
        return st.games > 0 and c is not None and clock >= exclusion_after(c)

    st_last = week_status(dim_game, *last)
    if not settled(*last):
        raise ScoreRefused(f"the final is refused until {week_key(*last)} is final in dim_game "
                           f"({st_last.final} of {st_last.games} games final)")
    not_final = [week_key(s_, w_) for s_, w_ in window if not settled(s_, w_)]
    if not_final:
        raise ScoreRefused(f"weeks not final in dim_game: {not_final}")
    records = load_records(root, window)
    # QA-F28: the branch, not the local files, decides which weeks were committed. A week with a commitment
    # or a sealed-hash record on the branch is never excluded as a miss, whatever markers or local files say.
    on_branch = {w for w in window if source.read(f"{week_key(*w)}/{COMMITMENT_NAME}") is not None
                 or source.read(f"{SEALED_BRANCH_DIR}/{week_key(*w)}.sha256") is not None}
    markers = {w: m for w in window if (m := missed_marker(source, *w)) is not None}
    unsealed = [week_key(*w) for w in window if w in on_branch and w not in records]
    if unsealed:
        raise ScoreRefused("the final is refused: weeks with a commitment or sealed record on the branch but no "
                           f"local sealed record: {unsealed}; such a week is never excluded as a miss; restore its "
                           "sealed files (they must match the branch record) or score it")
    misses = {w: m for w, m in markers.items() if w not in on_branch and w not in records}
    conflicts = [w for w in window if w in markers and (w in on_branch or w in records)]
    flagged = [flagged_marker(source, w, markers[w], records) for w in conflicts]
    unknown = [week_key(*w) for w in window if w not in records and w not in misses]
    if unknown:
        raise ScoreRefused(f"the final is refused: weeks neither sealed nor marked MISSED: {unknown}")
    scored_weeks = [w for w in window if w in records]
    seal_checks = verify_seals(source, root, scored_weeks) if require_seals else []
    bad_seals = [c for c in seal_checks if not c.ok]
    if bad_seals:
        raise ScoreRefused("sealed files do not match the branch record: "
                           + "; ".join(f"{c.name}: {c.detail}" for c in bad_seals))
    checks = livetest_integrity_checks(prereg, records, window, misses, repo_root=repo_root, root=root) + seal_checks
    checks += branch_record_checks(source, records, scored_weeks, branch_ref)
    # a marker for a committed week is listed but never excludes it and never invalidates the test (QA-F28)
    checks.append(Check("MISSED markers consistent with the branch", True,
                        "no marker for a committed or sealed week" if not conflicts else
                        "markers ignored for committed weeks, which are scored: "
                        + ", ".join(week_key(*w) for w in conflicts)))
    results: list[LineResult] | None = None
    scored: Scored | None = None
    sens: Scored | None = None
    if all(c.ok for c in checks):
        frame, found = load_parts(root, scored_weeks)
        if consensus is None:  # D-116 A3: the final reads only sealed parts, never fresh actuals or consensus
            consensus = load_consensus_parts(root, scored_weeks)
        scored = score_frames(frame, consensus, n_boot=n_boot)
        results = scored.results
        if sensitivity:
            sens = sensitivity_fn(frame, consensus) if sensitivity_fn else score_frames(frame, consensus,
                                                                                         n_boot=sensitivity_n_boot)
    verdict = H.verdict_text(checks, results, dry_run=dry_run)
    L: list[str] = [f"# Live test final evaluation ({week_key(*window[0])} to {week_key(*last)})", "",
                    f"Evaluated {iso(now or datetime.now(UTC))}. Pre-registration `{PREREG_DOC}` sha256 `{prereg.sha256[:16]}...`.",
                    "", "Branch record: " + (f"`{branch_ref.get('ref')}` at `{branch_ref.get('sha')}`, fetched "
                                             f"{branch_ref.get('fetched_at')}" if branch_ref else "as given by the caller"),
                    "", "## Integrity checks (including the sealed-file hashes against the branch record)", "",
                    _checks_table(checks), "", "## The 22 lines (binding: 500 week-clustered paired draws, seed 0)", ""]
    differing: list[dict[str, Any]] = []
    if results is not None and scored is not None:
        L += [f"Weeks scored: {', '.join(week_key(s_, w_) for s_, w_ in scored.weeks)}"
              + (f"; excluded (miss rule): {', '.join(week_key(*w) for w in misses)}" if misses else "") + ".", "",
              _lines_table(results), ""]
        if sens is not None:
            L += [f"## Report-only sensitivity check: {sensitivity_n_boot:,} draws (D-111)", "",
                  "The binding verdict above uses the 500-draw intervals only. The same lines with "
                  f"{sensitivity_n_boot:,} week-clustered paired draws (seed 0) are shown for disclosure; they "
                  "cannot change a line's PASS/FAIL or the verdict.", ""]
            rows = []
            for r5, rs in zip(results, sens.results, strict=True):
                differs = r5.passed != rs.passed
                rows.append([r5.line.id, "PASS" if r5.passed else "FAIL", line_intervals(scored, r5.line),
                             "PASS" if rs.passed else "FAIL", line_intervals(sens, rs.line), "yes" if differs else ""])
                if differs:
                    differing.append({"id": r5.line.id, "binding": r5.passed, "binding_intervals": line_intervals(scored, r5.line),
                                      "sensitivity": rs.passed, "sensitivity_intervals": line_intervals(sens, rs.line)})
            L += [RB._md_table(["line", "binding", "binding interval(s)", f"{sensitivity_n_boot:,}-draw",
                                f"{sensitivity_n_boot:,}-draw interval(s)", "differs"], rows), ""]
            if differing:
                L += ["Lines whose PASS/FAIL would differ under the sensitivity check (disclosed; not binding):", ""]
                L += [f"- {d['id']}: binding {'PASS' if d['binding'] else 'FAIL'} {d['binding_intervals']}; "
                      f"{sensitivity_n_boot:,} draws {'PASS' if d['sensitivity'] else 'FAIL'} {d['sensitivity_intervals']}"
                      for d in differing] + [""]
            else:
                L += ["No line differs between the binding and the sensitivity intervals.", ""]
    else:
        L += ["Not computed: an integrity check failed.", ""]
    excluded_all = [{"week": week_key(*w), **g} for w in scored_weeks
                    for g in (records.get(w) or {}).get("excluded_games") or []]
    if excluded_all:
        L += ["## Games excluded (D-121)", "",
              "Games still not final 14 days after their week's data cutoff. Their player, team and DST rows were "
              "left out of the chain, the comparators and the consensus for that week:", "",
              RB._md_table(["week", "game", "teams", "status in the pinned dim_game", "reason"],
                           [[g["week"], g.get("game_id"), ", ".join(g.get("teams") or []) or "n/a",
                             g.get("status_in_pinned_dim_game") or "n/a", g.get("reason")] for g in excluded_all]), ""]
    if conflicts:
        L += ["## Integrity findings that do not change the verdict", "",
              "MISSED markers found for weeks that have a commitment or a sealed record. Those weeks were scored; "
              "the markers were not used (D-116 A5):", "",
              RB._md_table(["week", "marker added by branch commit", "GitHub commit time", "created_at in the marker",
                            "why flagged"],
                           [[f["week"], f["commit"] or "n/a (not a git ref)", f["commit_time"] or "n/a",
                             f["created_at"] or "absent", f["reason"]] for f in flagged]), ""]
    L += ["## Verdict", "", verdict, ""]
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / ("final_dry_run.md" if dry_run else "final.md")
    out.write_text("\n".join(L), encoding="utf-8")
    sealed_out = root / SEALED_DIR / ("final_lines_dry_run.json" if dry_run else "final_lines.json")
    sealed_out.parent.mkdir(parents=True, exist_ok=True)
    sealed_out.write_text(json.dumps({
        "evaluated_at": iso(now or datetime.now(UTC)), "verdict": verdict, "dry_run": dry_run,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
        "binding": {"n_boot": n_boot, "lines": [{"id": r.line.id, "passed": r.passed, "detail": r.detail,
                                                  "intervals": line_intervals(scored, r.line)} for r in results]} if results and scored else None,
        "sensitivity": {"n_boot": sensitivity_n_boot, "lines": [{"id": r.line.id, "passed": r.passed, "detail": r.detail,
                                                                 "intervals": line_intervals(sens, r.line)} for r in sens.results],
                        "differing": differing} if sens is not None else None,
        "unsealed_weeks": [week_key(*w) for w in scored_weeks],
        "flagged_markers": flagged,
        "excluded_games": excluded_all,
    }, indent=1, default=str), encoding="utf-8")
    return verdict, out


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def default_push_record(season: int, week: int) -> Path:
    return get_settings().data_dir / "livetest" / "runner" / week_key(season, week) / PUSH_RECORD_NAME


def _week_record(season: int, week: int) -> dict[str, Any] | None:
    rp = record_path(scoring_root(), season, week)
    return json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else None


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - wiring
    """Exit codes: 0 ok; 2 usage; 3 refused; 4 retry later (--week); 5 blocked (--prebuild-check);
    75 retryable fetch failure (--final)."""
    ensure_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--week", help="season-week, for example 2026w10")
    ap.add_argument("--final", action="store_true", help="the single final evaluation (refused until the window is complete)")
    ap.add_argument("--prebuild-check", action="store_true",
                    help="before a build: every commit in --from..--to logged at --to, none on a frozen path (D-116)")
    ap.add_argument("--from", dest="from_", default="", help="with --prebuild-check: last build's code commit ('' = X)")
    ap.add_argument("--to", default=None, help="with --prebuild-check: the commit the pinned worktree holds now")
    ap.add_argument("--repo", default=str(PROJECT_ROOT), help="with --prebuild-check: the repository (default: this code's)")
    ap.add_argument("--data-cutoff", metavar="WEEK", default=None, help="print the week's data cutoff (ISO UTC)")
    ap.add_argument("--dry-run", action="store_true", help="with --final: label the verdict as a dry run")
    ap.add_argument("--worktree", default=str(DEFAULT_WORKTREE),
                    help=f"commitments worktree for weekly scoring (else git show origin/{BRANCH}); "
                         "--final always fetches and reads the remote ref")
    ap.add_argument("--no-worktree", action="store_true", help="read the branch with git show only")
    ap.add_argument("--push-record", default=None, help="runner push_record.json (default data/livetest/runner/<week>/)")
    ap.add_argument("--baselines-run", default=COMPARATORS_RUN,
                    help="harness run with the comparators' calibrated rows (default data/backtests/livetest_comparators; "
                         "run or extended by the livetest_comparators config when the week is missing)")
    ap.add_argument("--no-comparators", action="store_true", help="score without L01-L12 comparators")
    ap.add_argument("--no-consensus", action="store_true")
    ap.add_argument("--allow-rehearsal", action="store_true", help="score a rehearsal commitment (information only)")
    ap.add_argument("--prereg", default=None)
    ap.add_argument("--weekly-sensitivity", action="store_true",
                    help="also seal the cumulative lines at the sensitivity draw count each week (slow)")
    ap.add_argument("--no-sensitivity", action="store_true", help="with --final: skip the report-only check")
    args = ap.parse_args(argv)
    prereg = load_prereg(args.prereg)

    if args.prebuild_check:
        if not args.to:
            ap.error("--prebuild-check needs --to")
        res = prebuild_check(args.from_ or None, args.to, Path(args.repo),
                             prereg.data if (prereg is not None and args.prereg) else None)
        print(json.dumps({k: res[k] for k in ("ok", "uncovered", "frozen", "detail", "from", "to", "x", "commits")}))
        return EXIT_OK if res["ok"] else EXIT_BLOCKED

    if args.data_cutoff:
        season, week = parse_week(args.data_cutoff)
        games = load_dim_game([season])
        cutoff = data_cutoff(games, season, week)
        if cutoff is None:  # not in dim_game yet (a 2027 week before the schedule is published)
            print(f"retry later: {week_key(season, week)} has no regular-season game in dim_game", file=sys.stderr)
            return EXIT_RETRY_LATER
        g = games.filter((pl.col("season") == season) & (pl.col("week") == week))
        if "game_type" in g.columns:
            g = g.filter(pl.col("game_type") == "REG")
        print(json.dumps({"week": week_key(season, week), "data_cutoff_utc": iso(cutoff),
                          "last_kickoff_utc": iso(parse_time(g["kickoff_utc"].max())),
                          "rule": "12:00 UTC on the first Friday at least 72 h after the last kickoff (D-116 A3)",
                          "exclusion_after_utc": iso(exclusion_after(cutoff)),
                          "exclusion_rule": "a game still not final at exclusion_after_utc is excluded and the week "
                                            "scored without it (D-121)"}))
        return EXIT_OK

    if args.final:
        if prereg is None:
            print("the final needs the pre-registration", file=sys.stderr)
            return EXIT_USAGE
        try:
            # QA-F29 / D-116 A4: the branch record is the freshly fetched remote ref; a failed fetch is retryable
            branch_ref = fetch_branch_ref()
        except FetchFailed as exc:
            print(f"retry later: {exc}", file=sys.stderr)
            return EXIT_FETCH_RETRY
        try:
            source = BranchSource(None, ref=branch_ref["ref"])
            verdict, out = final_evaluation(source=source, dim_game=load_dim_game(), prereg=prereg,
                                            branch_ref=branch_ref, dry_run=args.dry_run,
                                            sensitivity=not args.no_sensitivity)
        except ScoreRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        print(verdict)
        print(f"wrote {out}")
        return EXIT_OK

    if not args.week:
        ap.error("--week, --final, --prebuild-check or --data-cutoff is required")
    season, week = parse_week(args.week)
    wk = week_key(season, week)
    try:
        plan = plan_week(season, week, load_dim_game([season]), now=utcnow(), record=_week_record(season, week))
    except RetryLater as exc:
        print(f"retry later: {exc}", file=sys.stderr)
        return EXIT_RETRY_LATER
    except ScoreRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    pins, stores, dim_game, cutoff = plan.pins, plan.stores, plan.dim_game, plan.cutoff
    rec_path = Path(args.push_record) if args.push_record else default_push_record(season, week)
    push_record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else None
    source = BranchSource(None if args.no_worktree else Path(args.worktree))
    baselines = None
    if not args.no_comparators:
        try:
            baselines = ensure_comparators(season, week, dim_game, run=args.baselines_run,
                                           actuals_fn=lambda s_: load_actuals(s_, stores["processed"]))
        except ScoreRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return EXIT_REFUSED
    consensus = None
    if not args.no_consensus and stores.get("restricted") is not None:
        try:
            consensus = consensus_rows(season, week, store=stores["processed"], restricted_store=stores["restricted"])
        except ScoreRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - the week is sealed without consensus rows; L13-L16 skip it
            print(f"consensus unavailable: {exc}", file=sys.stderr)
    try:
        rec, out = score_week(season, week, source=source, dim_game=dim_game, push_record=push_record,
                              roots=[PROJECT_ROOT, get_settings().data_dir.parent], baselines=baselines,
                              consensus=consensus, prereg=prereg, allow_rehearsal=args.allow_rehearsal,
                              outcomes=lambda raw, s_: harness_outcomes(raw, s_, stores["processed"]),
                              dim_game_id=pins.get("processed/dim_game"), snapshot_ids=processed_snapshot_ids(),
                              weekly_sensitivity=args.weekly_sensitivity, data_cutoff=cutoff, data_snapshots=pins,
                              excluded_games=plan.excluded, pin_from=plan.pin_from)
    except ScoreRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    failed = [c.name for c in rec.checks if not c.ok]
    print(f"sealed {wk}: {len(rec.checks) - len(failed)} of {len(rec.checks)} checks pass"
          + (f"; failed: {failed}" if failed else "") + "; no metric is printed before the final (D-111)")
    print(f"wrote {out}")
    return EXIT_OK


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
