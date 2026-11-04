"""Shared machinery for the chain_v2 live test (D-098, D-099).

Used by ``scripts/livetest_week.py`` (the weekly runner) and its tests. It holds:

* the week plan: first kickoff per regular-season week from ``dim_game``
  ``kickoff_utc``, never from weekday assumptions, with the trigger times
  that the scheduler and the runner share;
* commitment parsing and re-hash verification for the ``commitment.json``
  written by ``ffml livetest build-week``;
* verification that a code worktree is pinned at the pre-registered commit;
* the commitments repository: a separate git worktree on an orphan branch
  (default ``livetest-2026``) that only ever pushes that branch;
* the external record from GitHub (commit, branch ref, push activity);
* snapshot fingerprints, so a refresh provably leaves existing snapshots alone;
* the per-run JSON-lines log and a run lock.

Nothing here imports model code. Reading ``dim_game`` uses ``ffml.data.store``
from the interpreter running the runner (the main checkout's venv).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRANCH = "livetest-2026"
DEFAULT_REMOTE = "origin"
PROTECTED_BRANCHES = frozenset({"main", "master", "HEAD"})
DEFAULT_COMMITMENTS_WORKTREE = REPO_ROOT.parent / "ffml-livetest-commitments"
DEFAULT_CODE_WORKTREE = REPO_ROOT.parent / "ffml-livetest-code"
COMMITMENT_NAME = "commitment.json"
SUMMARY_NAME = "player_summary.parquet"
MISSED_NAME = "MISSED.json"
SUMMARY_MAX_BYTES = 5 * 1024 * 1024
TENTATIVE_LEAD = timedelta(hours=24)
BRANCH_GITATTRIBUTES = "* -text\n"
BRANCH_README = """# livetest-2026: pre-kickoff commitments for the chain_v2 live test

This orphan branch holds only commitments for the live test of chain_v2 over
2026 weeks 10-18 (D-098, D-099). It shares no history with `main`. The test
design and the frozen code commit are pre-registered on `main`.

- `<season>w<week>/commitment.json`: written by `ffml livetest build-week`
  before the week's first kickoff. It lists the output files with SHA-256,
  the model and calibrator hashes, the snapshot ids, the code commit,
  `created_at`, the first kickoff and a top-level digest.
- `<season>w<week>/player_summary.parquet`: a copy of the projection summary
  when it is under 5 MB. Its SHA-256 is in the commitment.
- `<season>w<week>/MISSED.json`: written after kickoff when no commitment was
  pushed in time. How a missed week counts is decided by the test design.
- `rehearsal/...`: rehearsal commitments. They are not part of the test.

The commit timestamps are set by the pushing machine. The external timestamp
is GitHub's push record (`gh api repos/<owner>/<repo>/activity?ref=livetest-2026`).
Outputs are labeled "not approved" and are never shown in the app (D-099).
Pushed by `scripts/livetest_week.py`; never force-pushed.
"""


class LivetestError(RuntimeError):
    """Base class for refusals and integrity problems in the live-test machinery."""


class PinError(LivetestError):
    """The code worktree is not pinned at the expected commit or environment."""


class GitError(LivetestError):
    """A git operation on the commitments repository failed or was refused."""


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return as_utc(ts).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO string (``Z`` allowed) or pass a datetime through; ``None`` stays ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    return as_utc(datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")))


def compact(ts: datetime) -> str:
    return as_utc(ts).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- #
# Week plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Timing:
    """Offsets relative to a week's (effective) first kickoff."""

    window_hours: float = 12.0  # the runner acts only from K - window
    run_offset_hours: float = 9.0  # scheduled start of the runner
    refresh_deadline_minutes: float = 180.0  # no refresh attempt starts after K - this
    cutoff_minutes: float = 30.0  # no refresh or build starts after K - this
    push_margin_minutes: float = 5.0  # no push starts after K - this
    watchdog_minutes: float = 60.0  # watchdog at K - this
    retry_minutes: float = 30.0  # repetition interval of the scheduled runner


@dataclass(frozen=True)
class WeekKickoff:
    season: int
    week: int
    first_kickoff_utc: datetime
    last_kickoff_utc: datetime
    games: int
    games_final: int
    tentative: bool  # every game at one identical time: kickoff times not set yet

    @property
    def key(self) -> str:
        return week_key(self.season, self.week)

    @property
    def effective_kickoff_utc(self) -> datetime:
        """The kickoff the deadlines use: 24 hours early while times are tentative.

        Week 18 times are set late and may move to Saturday; until the schedule
        shows real times, the runner and the watchdog act a day early.
        """
        return self.first_kickoff_utc - TENTATIVE_LEAD if self.tentative else self.first_kickoff_utc

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["first_kickoff_utc"] = iso(self.first_kickoff_utc)
        out["last_kickoff_utc"] = iso(self.last_kickoff_utc)
        out["effective_kickoff_utc"] = iso(self.effective_kickoff_utc)
        out["key"] = self.key
        return out


def week_key(season: int, week: int) -> str:
    return f"{int(season)}w{int(week):02d}"


def parse_weeks(spec: str) -> list[int]:
    """'10-18' or '10,11,12' into a sorted list."""
    weeks: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            weeks.update(range(int(lo), int(hi) + 1))
        else:
            weeks.add(int(part))
    return sorted(weeks)


def week_plan(dim_game: Any, season: int | None = None) -> list[WeekKickoff]:
    """First and last kickoff per regular-season week from a ``dim_game`` frame."""
    import polars as pl

    needed = {"season", "week", "game_type", "kickoff_utc"}
    missing = needed - set(dim_game.columns)
    if missing:
        raise ValueError(f"dim_game lacks columns {sorted(missing)}")
    games = dim_game.filter(pl.col("game_type") == "REG")
    if season is not None:
        games = games.filter(pl.col("season") == season)
    if "status" in games.columns:
        final = (pl.col("status") == "final").sum()
    elif "result" in games.columns:
        final = pl.col("result").is_not_null().sum()
    else:
        final = pl.lit(0)
    grouped = (
        games.filter(pl.col("kickoff_utc").is_not_null())
        .group_by(["season", "week"])
        .agg(
            pl.col("kickoff_utc").min().alias("first"),
            pl.col("kickoff_utc").max().alias("last"),
            pl.len().alias("games"),
            final.alias("final"),
            pl.col("kickoff_utc").n_unique().alias("distinct"),
        )
        .sort(["season", "week"])
    )
    plan = []
    for row in grouped.iter_rows(named=True):
        plan.append(
            WeekKickoff(
                season=int(row["season"]),
                week=int(row["week"]),
                first_kickoff_utc=as_utc(row["first"]),
                last_kickoff_utc=as_utc(row["last"]),
                games=int(row["games"]),
                games_final=int(row["final"] or 0),
                tentative=int(row["games"]) > 1 and int(row["distinct"]) == 1,
            )
        )
    return plan


def next_week(plan: Iterable[WeekKickoff], now: datetime) -> WeekKickoff | None:
    """The week whose first kickoff is next (strictly after ``now``)."""
    now = as_utc(now)
    upcoming = [w for w in plan if w.first_kickoff_utc > now]
    return min(upcoming, key=lambda w: w.first_kickoff_utc) if upcoming else None


def find_week(plan: Iterable[WeekKickoff], season: int, week: int) -> WeekKickoff | None:
    for w in plan:
        if w.season == season and w.week == week:
            return w
    return None


def triggers(wk: WeekKickoff, timing: Timing | None = None) -> dict[str, datetime]:
    """Scheduler and runner times for one week, all in UTC."""
    timing = timing or Timing()
    k = wk.effective_kickoff_utc
    return {
        "window_open": k - timedelta(hours=timing.window_hours),
        "run_start": k - timedelta(hours=timing.run_offset_hours),
        "refresh_deadline": k - timedelta(minutes=timing.refresh_deadline_minutes),
        "cutoff": k - timedelta(minutes=timing.cutoff_minutes),
        "watchdog": k - timedelta(minutes=timing.watchdog_minutes),
        "push_deadline": k - timedelta(minutes=timing.push_margin_minutes),
        "kickoff": wk.first_kickoff_utc,
        "effective_kickoff": k,
    }


def load_dim_game(data_dir: Path | str, seasons: Iterable[int] | None = None) -> Any:
    """``dim_game`` LATEST from ``<data_dir>/processed`` (read only)."""
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(Path(data_dir) / "processed")
    return store.read("dim_game", seasons=list(seasons) if seasons else None)


def dim_game_snapshot_id(data_dir: Path | str) -> str | None:
    pointer = Path(data_dir) / "processed" / "dim_game" / "LATEST"
    return pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None


def fetch_live_schedule(season: int, now: datetime | None = None) -> Any:
    """Current nflverse schedule for ``season`` as a ``dim_game``-shaped frame, in memory.

    Nothing is written: the snapshot store is updated only by the refresh.
    """
    from ffml.data.nflverse import DATASETS, clean, configure_nflreadpy, fetch
    from ffml.data.process.dims import build_dim_game

    configure_nflreadpy()
    spec = DATASETS["schedules"]
    frame, _ = clean(spec, fetch(spec, season))
    return build_dim_game(frame, now or utcnow())


# --------------------------------------------------------------------------- #
# Hashing and commitments
# --------------------------------------------------------------------------- #


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(data: Mapping[str, Any], exclude: str | None = "digest") -> str:
    """SHA-256 of the canonical JSON (sorted keys, no spaces, UTF-8) without ``exclude``."""
    body = {k: v for k, v in data.items() if k != exclude}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return sha256_bytes(text.encode("utf-8"))


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class FileEntry:
    path: str
    sha256: str
    bytes: int | None = None


@dataclass
class Commitment:
    path: Path
    raw: bytes
    data: dict[str, Any]
    files: list[FileEntry]

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.raw)

    def _first(self, *keys: str) -> Any:
        for key in keys:
            if self.data.get(key) not in (None, ""):
                return self.data[key]
        return None

    @property
    def season(self) -> int | None:
        value = self._first("season")
        return int(value) if value is not None else None

    @property
    def week(self) -> int | None:
        value = self._first("week")
        return int(value) if value is not None else None

    @property
    def created_at(self) -> datetime | None:
        return parse_time(self._first("created_at"))

    @property
    def first_kickoff(self) -> datetime | None:
        return parse_time(self._first("first_kickoff_utc", "first_kickoff", "kickoff_utc"))

    @property
    def code_commit(self) -> str | None:
        value = self._first("code_commit", "code", "git_commit", "commit")
        if isinstance(value, Mapping):
            value = value.get("commit") or value.get("sha")
        return str(value) if value is not None else None

    @property
    def code_clean(self) -> bool | None:
        code = self.data.get("code")
        if isinstance(code, Mapping) and "clean" in code:
            return bool(code["clean"])
        return None

    @property
    def digest_key(self) -> str | None:
        for key in ("digest", "commitment_digest", "top_level_digest"):
            if key in self.data:
                return key
        return None

    @property
    def digest(self) -> str | None:
        key = self.digest_key
        value = self.data.get(key) if key else None
        if isinstance(value, Mapping):
            value = value.get("sha256") or value.get("value")
        return str(value) if value is not None else None

    @property
    def stub(self) -> bool:
        return bool(self.data.get("stub"))

    @property
    def rehearsal(self) -> bool | None:
        value = self.data.get("rehearsal")
        return bool(value) if value is not None else None

    def summary_entry(self) -> FileEntry | None:
        for entry in self.files:
            if Path(entry.path.replace("\\", "/")).name == SUMMARY_NAME:
                return entry
        return None


def _entries(files: Any) -> list[FileEntry]:
    out: list[FileEntry] = []
    if isinstance(files, Mapping):
        for path, value in files.items():
            if isinstance(value, Mapping):
                sha = value.get("sha256") or value.get("hash")
                size = value.get("bytes") or value.get("size")
            else:
                sha, size = value, None
            out.append(FileEntry(str(path), str(sha).lower(), int(size) if size is not None else None))
    elif isinstance(files, list):
        for item in files:
            if not isinstance(item, Mapping):
                raise ValueError(f"file entry is not an object: {item!r}")
            path = item.get("path") or item.get("file") or item.get("name")
            sha = item.get("sha256") or item.get("hash")
            size = item.get("bytes") or item.get("size")
            if not path or not sha:
                raise ValueError(f"file entry needs a path and a sha256: {item!r}")
            out.append(FileEntry(str(path), str(sha).lower(), int(size) if size is not None else None))
    else:
        raise ValueError("commitment 'files' must be a list or an object")
    return out


def load_commitment(path: Path | str) -> Commitment:
    path = Path(path)
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    files = data.get("outputs", data.get("files"))
    if files is None:
        raise ValueError(f"{path} lists no output files ('outputs')")
    entries = _entries(files)
    history = data.get("calibration_history")
    if isinstance(history, Mapping) and history.get("sources"):
        entries += _entries(history["sources"])
    return Commitment(path=path, raw=raw, data=data, files=entries)


def resolve_entry(entry: FileEntry, roots: Iterable[Path]) -> Path | None:
    """Find a listed file: absolute, or relative to the first root that has it."""
    text = entry.path.replace("\\", "/")
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    for root in roots:
        for rel in (text, text[5:] if text.startswith("data/") else None):
            if rel is None:
                continue
            path = Path(root) / rel
            if path.exists():
                return path
    return None


@dataclass
class Verification:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    digest: dict[str, Any] = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.ok = False
        self.problems.append(message)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_commitment(
    commitment: Commitment,
    *,
    roots: Iterable[Path],
    season: int | None = None,
    week: int | None = None,
    kickoff: datetime | None = None,
    expected_commit: str | None = None,
    rehearsal: bool | None = None,
) -> Verification:
    """Re-hash every listed file and check the commitment's identity fields and digest.

    The digest is recomputed with :func:`canonical_digest`, the same canonical
    form as ``ffml.sim.livetest.commitment_digest`` (sorted keys, separators
    ``,`` and ``:``, UTF-8, everything except ``digest``). ``kickoff=None``
    skips the timing check (rehearsals).
    """
    v = Verification()
    roots = [Path(r) for r in roots]
    if season is not None and commitment.season != season:
        v.fail(f"season is {commitment.season}, expected {season}")
    if week is not None and commitment.week != week:
        v.fail(f"week is {commitment.week}, expected {week}")
    if rehearsal is not None and commitment.rehearsal is not None and commitment.rehearsal != rehearsal:
        v.fail(f"rehearsal flag is {commitment.rehearsal}, expected {rehearsal}")
    created = commitment.created_at
    if created is None:
        v.fail("created_at is missing")
    stated_kickoff = commitment.first_kickoff
    if stated_kickoff is None:
        v.warnings.append("the commitment does not state the first kickoff")
    if kickoff is not None:
        if stated_kickoff is not None and stated_kickoff != as_utc(kickoff):
            v.warnings.append(
                f"stated first kickoff {iso(stated_kickoff)} differs from the schedule {iso(kickoff)}"
            )
        if created is not None and created >= as_utc(kickoff):
            v.fail(f"created_at {iso(created)} is not before the first kickoff {iso(kickoff)}")
    if expected_commit:
        commit = commitment.code_commit
        if not commit:
            v.fail("code_commit is missing")
        elif not (commit.startswith(expected_commit) or expected_commit.startswith(commit)):
            v.fail(f"code_commit {commit} is not the pinned commit {expected_commit}")
        if commitment.code_clean is False:
            v.fail("the commitment says the code tree was not clean when it was built")
    if not commitment.files:
        v.fail("no output files listed")
    for entry in commitment.files:
        row: dict[str, Any] = {"path": entry.path, "expected": entry.sha256}
        if not _HEX64.match(entry.sha256):
            v.fail(f"{entry.path}: sha256 {entry.sha256!r} is not 64 hex characters")
        found = resolve_entry(entry, roots)
        if found is None:
            row["status"] = "missing"
            v.fail(f"{entry.path}: file not found")
        else:
            actual = sha256_file(found)
            row.update(resolved=str(found), actual=actual, status="ok" if actual == entry.sha256 else "mismatch")
            if actual != entry.sha256:
                v.fail(f"{entry.path}: sha256 {actual} does not match the manifest {entry.sha256}")
            if entry.bytes is not None and found.stat().st_size != entry.bytes:
                v.fail(f"{entry.path}: size {found.stat().st_size} differs from {entry.bytes}")
        v.files.append(row)
    stated = commitment.digest
    if stated is None:
        v.warnings.append("no top-level digest")
        v.digest = {"stated": None}
    else:
        computed = canonical_digest(commitment.data, commitment.digest_key)
        v.digest = {"stated": stated, "computed": computed, "method": "sha256 canonical JSON", "match": stated == computed}
        if stated != computed:
            v.fail(f"top-level digest {stated} does not match the body ({computed})")
    return v


# --------------------------------------------------------------------------- #
# Code worktree pin
# --------------------------------------------------------------------------- #


Runner = Callable[..., subprocess.CompletedProcess]


def _run(cmd: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None,
         timeout: float | None = 120, runner: Runner = subprocess.run) -> subprocess.CompletedProcess:
    return runner(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def clean_child_env(base: Mapping[str, str] | None = None, **paths: Path | str) -> dict[str, str]:
    """Environment for pipeline subprocesses.

    Drops ``FFML_HOLDOUT_UNLOCK`` and anything that could put the main
    checkout's code on ``sys.path`` (``PYTHONPATH``, ``PYTHONHOME``,
    ``VIRTUAL_ENV``), forces UTF-8, and sets ``FFML_<NAME>`` for each path.
    """
    env = dict(base if base is not None else os.environ)
    for name in ("FFML_HOLDOUT_UNLOCK", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP"):
        env.pop(name, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for name, value in paths.items():
        env[f"FFML_{name.upper()}"] = str(Path(value).resolve())
    return env


def _norm_req(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(text: str) -> dict[str, str]:
    """``name==version`` lines (comments, options and editables ignored)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        out[_norm_req(name.split("[", 1)[0].strip())] = version.split(";", 1)[0].strip()
    return out


def compare_requirements(lock: Mapping[str, str], installed: Mapping[str, str]) -> list[str]:
    problems = []
    for name in sorted(set(lock) | set(installed)):
        if name in ("ffml", "pip", "setuptools", "wheel"):
            continue
        want, have = lock.get(name), installed.get(name)
        if want is None:
            problems.append(f"{name}=={have} is installed but not in the lock")
        elif have is None:
            problems.append(f"{name}=={want} is in the lock but not installed")
        elif want != have:
            problems.append(f"{name}: lock {want}, installed {have}")
    return problems


@dataclass
class PinResult:
    worktree: str
    commit: str
    python: str
    ffml_file: str
    ffml_file_neutral_cwd: str
    sys_prefix: str
    env_checked: bool
    remote_contains: bool | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _inside(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


_PROBE = (
    "import json, sys, ffml; "
    "print(json.dumps({'file': ffml.__file__, 'prefix': sys.prefix, 'version': sys.version}))"
)


def verify_code_pin(
    worktree: Path | str,
    expected_commit: str,
    python: Path | str,
    *,
    main_repo: Path | str = REPO_ROOT,
    check_env: bool = True,
    require_editable: bool = True,
    runner: Runner = subprocess.run,
) -> PinResult:
    """Refuse unless ``worktree`` is a clean checkout of ``expected_commit`` run by ``python``.

    Checks: the path is a git worktree other than the main checkout; HEAD
    equals the expected commit; ``git status --porcelain`` is empty (ignored
    files such as the venv are allowed); ``ffml`` imports from inside the
    worktree both with the worktree as working directory and from a neutral
    directory (the editable install, which the refresh driver relies on); and
    the venv's ``pip freeze`` equals the worktree's ``requirements.lock.txt``.
    ``require_editable=False`` skips the neutral-directory probe (tests only).
    """
    wt = Path(worktree).resolve()
    if not wt.is_dir():
        raise PinError(f"code worktree {wt} does not exist")
    if wt == Path(main_repo).resolve():
        raise PinError("the code worktree must be a separate checkout, not the main repository")
    if not expected_commit or not re.fullmatch(r"[0-9a-fA-F]{7,40}", expected_commit):
        raise PinError(f"expected commit {expected_commit!r} is not a commit sha")
    top = _run(["git", "-C", str(wt), "rev-parse", "--show-toplevel"], runner=runner)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != wt:
        raise PinError(f"{wt} is not the top of a git worktree ({top.stderr.strip()})")
    want = _run(["git", "-C", str(wt), "rev-parse", "--verify", "--quiet", f"{expected_commit}^{{commit}}"], runner=runner)
    if want.returncode != 0:
        raise PinError(f"expected commit {expected_commit} is not known in {wt}")
    want_sha = want.stdout.strip()
    head = _run(["git", "-C", str(wt), "rev-parse", "HEAD"], runner=runner).stdout.strip()
    if head != want_sha:
        raise PinError(f"worktree HEAD is {head}, expected {want_sha}")
    status = _run(["git", "-C", str(wt), "status", "--porcelain=v1", "--untracked-files=all"], runner=runner)
    if status.returncode != 0:
        raise PinError(f"git status failed in {wt}: {status.stderr.strip()}")
    if status.stdout.strip():
        lines = status.stdout.strip().splitlines()
        raise PinError(f"worktree {wt} is not clean ({len(lines)} entries): {lines[:5]}")

    py = Path(python)
    if not py.exists():
        raise PinError(f"python {py} does not exist")
    env = clean_child_env()
    probes = {}
    with tempfile.TemporaryDirectory(prefix="ffml-pin-") as neutral:
        places = [("worktree", wt)] + ([("neutral", Path(neutral))] if require_editable else [])
        for label, cwd in places:
            proc = _run([str(py), "-c", _PROBE], cwd=cwd, env=env, runner=runner)
            if proc.returncode != 0:
                raise PinError(f"{py} cannot import ffml from {label} cwd: {proc.stderr.strip()[-400:]}")
            probes[label] = json.loads(proc.stdout.strip().splitlines()[-1])
    for label, probe in probes.items():
        if not _inside(probe["file"], wt):
            raise PinError(
                f"{py} imports ffml from {probe['file']} ({label} cwd), outside the worktree {wt}; "
                "install the worktree editable into its own venv (scripts/livetest_setup_worktree.py)"
            )
    warnings: list[str] = []
    if Path(probes["worktree"]["prefix"]).resolve() == (Path(main_repo) / ".venv").resolve():
        warnings.append("the pinned python is the main checkout's venv")
    if check_env:
        lock_path = wt / "requirements.lock.txt"
        if not lock_path.exists():
            raise PinError(f"{lock_path} is missing; cannot check the environment")
        freeze = _run([str(py), "-m", "pip", "freeze", "--exclude-editable"], cwd=wt, env=env, runner=runner)
        if freeze.returncode != 0:
            raise PinError(f"pip freeze failed: {freeze.stderr.strip()[-300:]}")
        diff = compare_requirements(
            parse_requirements(lock_path.read_text(encoding="utf-8")), parse_requirements(freeze.stdout)
        )
        if diff:
            raise PinError(f"venv does not match requirements.lock.txt: {diff[:8]}")
    contains = _run(["git", "-C", str(wt), "branch", "-r", "--contains", want_sha], runner=runner)
    remote_contains = bool(contains.stdout.strip()) if contains.returncode == 0 else None
    if remote_contains is False:
        warnings.append(f"commit {want_sha[:12]} is on no remote-tracking branch; push it so others can check it")
    return PinResult(
        worktree=str(wt),
        commit=want_sha,
        python=str(py),
        ffml_file=probes["worktree"]["file"],
        ffml_file_neutral_cwd=probes.get("neutral", {}).get("file", "not checked"),
        sys_prefix=probes["worktree"]["prefix"],
        env_checked=check_env,
        remote_contains=remote_contains,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Commitments repository (separate worktree, orphan branch)
# --------------------------------------------------------------------------- #


GIT_BASE = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotepath=false"]


class CommitmentsRepo:
    """The commitments worktree. It only ever pushes ``refs/heads/<branch>``."""

    def __init__(
        self,
        main_repo: Path | str,
        worktree: Path | str,
        branch: str = DEFAULT_BRANCH,
        remote: str = DEFAULT_REMOTE,
        runner: Runner = subprocess.run,
    ) -> None:
        if branch in PROTECTED_BRANCHES or branch.startswith("refs/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            raise GitError(f"refusing branch {branch!r}: commitments go to a dedicated branch only")
        self.main_repo = Path(main_repo).resolve()
        self.worktree = Path(worktree).resolve()
        self.branch = branch
        self.remote = remote
        self.runner = runner
        self.remote_ref = f"refs/remotes/{remote}/{branch}"

    # -- low level ---------------------------------------------------------- #

    def git(self, *args: str, cwd: Path | None = None, check: bool = True, timeout: float = 180,
            binary: bool = False) -> subprocess.CompletedProcess:
        if cwd is None:
            cwd = self.worktree if self.worktree.exists() else self.main_repo
        cmd = [*GIT_BASE, "-C", str(cwd), *args]
        if binary:
            proc = self.runner(cmd, capture_output=True, timeout=timeout)
        else:
            proc = _run(cmd, timeout=timeout, runner=self.runner)
        if check and proc.returncode != 0:
            err = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode("utf-8", "replace")
            raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {err.strip()[-500:]}")
        return proc

    def remote_url(self) -> str | None:
        proc = self.git("remote", "get-url", self.remote, cwd=self.main_repo, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    # -- setup -------------------------------------------------------------- #

    def _is_registered(self) -> bool:
        out = self.git("worktree", "list", "--porcelain", cwd=self.main_repo).stdout
        for line in out.splitlines():
            if line.startswith("worktree ") and Path(line[9:].strip()).resolve() == self.worktree:
                return True
        return False

    def current_branch(self) -> str | None:
        if not self.worktree.exists():
            return None
        proc = self.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def head(self) -> str | None:
        if not self.worktree.exists():
            return None
        proc = self.git("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None

    def remote_head(self) -> str | None:
        proc = self.git("rev-parse", "--verify", "--quiet", self.remote_ref, check=False)
        return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None

    def fetch(self) -> bool:
        """Update the remote-tracking ref; True when the remote has the branch."""
        cwd = self.worktree if self.worktree.exists() else self.main_repo
        proc = self.git("fetch", "--quiet", self.remote, f"+refs/heads/{self.branch}:{self.remote_ref}",
                        cwd=cwd, check=False)
        if proc.returncode == 0:
            return True
        if "couldn't find remote ref" in proc.stderr.lower():
            self.git("update-ref", "-d", self.remote_ref, cwd=cwd, check=False)
            return False
        raise GitError(f"git fetch {self.remote} {self.branch} failed: {proc.stderr.strip()[-400:]}")

    def ensure(self) -> str:
        """Create or reuse the worktree on the branch; returns a short description."""
        if self.worktree.exists():
            if not self._is_registered():
                raise GitError(f"{self.worktree} exists but is not a worktree of {self.main_repo}")
            branch = self.current_branch()
            if branch != self.branch:
                raise GitError(f"{self.worktree} is on {branch!r}, expected {self.branch!r}")
            return "reused"
        try:
            remote_exists = self.fetch()
        except GitError:
            remote_exists = False
        local = self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}", cwd=self.main_repo, check=False)
        if local.returncode == 0 and local.stdout.strip():
            self.git("worktree", "add", str(self.worktree), self.branch, cwd=self.main_repo)
            return "added (local branch)"
        if remote_exists:
            self.git("worktree", "add", "--track", "-b", self.branch, str(self.worktree), f"{self.remote}/{self.branch}",
                     cwd=self.main_repo)
            return "added (tracking remote branch)"
        proc = self.git("worktree", "add", "--orphan", "-b", self.branch, str(self.worktree), cwd=self.main_repo, check=False)
        if proc.returncode != 0:  # git older than 2.42
            self.git("worktree", "add", "--detach", str(self.worktree), cwd=self.main_repo)
            self.git("checkout", "--orphan", self.branch)
            self.git("rm", "-rf", "--quiet", ".", check=False)
        return "added (new orphan branch)"

    def sync(self) -> str:
        """Bring the local branch up to the remote one; refuse when they diverged."""
        remote = self.remote_head()
        local = self.head()
        if remote is None or remote == local:
            return "in sync" if remote else "no remote branch"
        if local is None:
            if self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
                raise GitError("unborn commitments branch has staged files; refusing to reset")
            self.git("reset", "--quiet", "--hard", remote)
            return "fast-forwarded (was unborn)"
        if self.git("merge-base", "--is-ancestor", local, remote, check=False).returncode == 0:
            self.git("merge", "--quiet", "--ff-only", remote)
            return "fast-forwarded"
        if self.git("merge-base", "--is-ancestor", remote, local, check=False).returncode == 0:
            return "ahead of remote"
        raise GitError(f"local {self.branch} {local[:12]} and remote {remote[:12]} diverged; needs a human")

    # -- content ------------------------------------------------------------ #

    def remote_blob(self, relpath: str) -> bytes | None:
        if self.remote_head() is None:
            return None
        spec = f"{self.remote_ref}:{relpath}"
        if self.git("cat-file", "-e", spec, check=False).returncode != 0:
            return None
        return self.git("cat-file", "blob", spec, binary=True).stdout

    def head_blob(self, relpath: str) -> bytes | None:
        if self.head() is None:
            return None
        spec = f"HEAD:{relpath}"
        if self.git("cat-file", "-e", spec, check=False).returncode != 0:
            return None
        return self.git("cat-file", "blob", spec, binary=True).stdout

    def remote_paths(self) -> list[str]:
        if self.remote_head() is None:
            return []
        return self.git("ls-tree", "-r", "--name-only", self.remote_ref).stdout.splitlines()

    def commit(self, files: Mapping[str, bytes], message: str) -> str | None:
        """Write ``files`` (branch path -> bytes), commit them, return the sha (None if unchanged).

        Commitment files are immutable: a path that already holds different
        bytes is refused.
        """
        extras = {".gitattributes": BRANCH_GITATTRIBUTES.encode(), "README.md": BRANCH_README.encode()}
        to_add: list[str] = []
        for rel, data in {**{k: v for k, v in extras.items() if not (self.worktree / k).exists()}, **files}.items():
            rel = rel.replace("\\", "/")
            if rel.startswith("/") or ".." in rel.split("/"):
                raise GitError(f"unsafe branch path {rel!r}")
            target = self.worktree / rel
            if target.exists():
                if target.read_bytes() != data:
                    raise GitError(f"{rel} already exists on the branch with different content; commitments are immutable")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            to_add.append(rel)
        # .gitattributes first so the rest is added without end-of-line conversion
        self.git("add", "--", *sorted(to_add, key=lambda p: p != ".gitattributes"))
        if self.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8", newline="\n") as fh:
            fh.write(message)
            msg_path = fh.name
        try:
            self.git("commit", "--quiet", "-F", msg_path)
        finally:
            os.unlink(msg_path)
        return self.head()

    def push(self, attempts: int = 3, backoff_seconds: float = 20.0,
             deadline: datetime | None = None, clock: Callable[[], datetime] = utcnow,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        """Push ``refs/heads/<branch>`` and nothing else. Never forces."""
        refspec = f"refs/heads/{self.branch}:refs/heads/{self.branch}"
        tries: list[dict[str, Any]] = []
        for attempt in range(1, attempts + 1):
            if deadline is not None and clock() >= deadline:
                tries.append({"attempt": attempt, "skipped": "push deadline reached"})
                break
            started = clock()
            proc = self.git("push", "--porcelain", self.remote, refspec, check=False, timeout=300)
            tries.append({
                "attempt": attempt,
                "started": iso(started),
                "finished": iso(clock()),
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip()[-800:],
                "stderr": proc.stderr.strip()[-800:],
            })
            if proc.returncode == 0:
                return {"ok": True, "attempts": tries, "refspec": refspec}
            if attempt < attempts:
                sleep(backoff_seconds * attempt)
        return {"ok": False, "attempts": tries, "refspec": refspec}

    def ls_remote(self) -> str | None:
        proc = self.git("ls-remote", self.remote, f"refs/heads/{self.branch}", check=False, timeout=120)
        if proc.returncode != 0:
            raise GitError(f"git ls-remote failed: {proc.stderr.strip()[-300:]}")
        line = proc.stdout.strip().splitlines()
        return line[0].split()[0] if line else None


# --------------------------------------------------------------------------- #
# GitHub's view (external timestamp)
# --------------------------------------------------------------------------- #


def parse_github_remote(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    match = re.match(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return (match.group(1), match.group(2)) if match else None


def _gh_json(path: str, runner: Runner, gh: str = "gh") -> Any:
    proc = _run([gh, "api", "-H", "Accept: application/vnd.github+json", path], timeout=60, runner=runner)
    if proc.returncode != 0:
        raise LivetestError(f"gh api {path} failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return json.loads(proc.stdout)


def github_view(
    owner: str,
    repo: str,
    branch: str,
    sha: str,
    *,
    attempts: int = 4,
    delay_seconds: float = 5.0,
    runner: Runner = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """What GitHub says about the pushed commit.

    ``push_activity.timestamp`` (repository activity API) is set by GitHub's
    servers when the push is received, so it is the external timestamp. The
    commit's own dates are set by the pushing machine.
    """
    view: dict[str, Any] = {"owner": owner, "repo": repo, "branch": branch, "sha": sha, "errors": []}
    try:
        commit = _gh_json(f"repos/{owner}/{repo}/commits/{sha}", runner)
        view["commit"] = {
            "sha": commit.get("sha"),
            "html_url": commit.get("html_url"),
            "committer_date_client": (commit.get("commit") or {}).get("committer", {}).get("date"),
        }
    except (LivetestError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        view["errors"].append(str(exc))
    try:
        ref = _gh_json(f"repos/{owner}/{repo}/git/ref/heads/{branch}", runner)
        view["ref_sha"] = (ref.get("object") or {}).get("sha")
    except (LivetestError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        view["errors"].append(str(exc))
    view["push_activity"] = None
    for attempt in range(attempts):
        try:
            items = _gh_json(f"repos/{owner}/{repo}/activity?ref=refs/heads/{branch}&per_page=30", runner)
            match = next((i for i in items if i.get("after") == sha), None)
            if match:
                view["push_activity"] = {
                    "id": match.get("id"),
                    "timestamp": match.get("timestamp"),
                    "activity_type": match.get("activity_type"),
                    "actor": (match.get("actor") or {}).get("login"),
                    "before": match.get("before"),
                    "after": match.get("after"),
                }
                break
        except (LivetestError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            if attempt == attempts - 1:
                view["errors"].append(str(exc))
        if attempt < attempts - 1:
            sleep(delay_seconds)
    return view


# --------------------------------------------------------------------------- #
# Snapshot fingerprints
# --------------------------------------------------------------------------- #


SNAPSHOT_TIERS = ("raw", "processed", "features")


def fingerprint_tree(root: Path) -> dict[str, list[int]]:
    """``relative path -> [size, mtime_ns]`` for every file under ``root``."""
    out: dict[str, list[int]] = {}
    if not root.exists():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            st = path.stat()
            out[path.relative_to(root).as_posix()] = [st.st_size, st.st_mtime_ns]
    return out


def fingerprint_snapshots(data_dir: Path | str, tiers: Iterable[str] = SNAPSHOT_TIERS) -> dict[str, Any]:
    """Every committed snapshot directory: its files (size, mtime) and manifest hash."""
    data_dir = Path(data_dir)
    out: dict[str, Any] = {}
    for tier in tiers:
        root = data_dir / tier
        if not root.exists():
            continue
        for snap in root.rglob("snapshot=*"):
            if not snap.is_dir():
                continue
            files = fingerprint_tree(snap)
            if not files:
                continue
            manifest = snap / "_manifest.json"
            out[snap.relative_to(data_dir).as_posix()] = {
                "files": files,
                "manifest_sha256": sha256_file(manifest) if manifest.exists() else None,
            }
    return out


def compare_fingerprints(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Problems where a snapshot present before is missing or changed after."""
    problems = []
    for key, old in before.items():
        new = after.get(key)
        if new is None:
            problems.append(f"{key}: snapshot removed")
        elif new != old:
            changed = sorted(set(old["files"]) ^ set(new["files"])) or [
                f for f in old["files"] if old["files"][f] != new["files"].get(f)
            ]
            problems.append(f"{key}: snapshot modified ({changed[:5]})")
    return problems


def latest_ids(data_dir: Path | str, tiers: Iterable[str] = SNAPSHOT_TIERS) -> dict[str, str]:
    data_dir = Path(data_dir)
    out = {}
    for tier in tiers:
        root = data_dir / tier
        if root.exists():
            for pointer in root.rglob("LATEST"):
                out[pointer.parent.relative_to(data_dir).as_posix()] = pointer.read_text(encoding="utf-8").strip()
    return out


# --------------------------------------------------------------------------- #
# Run log and lock
# --------------------------------------------------------------------------- #


class RunLog:
    """JSON-lines log for one run: ``<logs_dir>/<UTC stamp>_<label>.jsonl``."""

    def __init__(self, logs_dir: Path, label: str, now: datetime | None = None, echo: bool = True) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = compact(now or utcnow())
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label)
        path = logs_dir / f"{stamp}_{safe}.jsonl"
        n = 1
        while path.exists():
            path = logs_dir / f"{stamp}_{safe}-{n}.jsonl"
            n += 1
        self.path = path
        self.echo = echo
        self.events: list[dict[str, Any]] = []

    def event(self, step: str, status: str, **fields: Any) -> dict[str, Any]:
        record = {"ts": iso(utcnow()), "step": step, "status": status, **fields}
        self.events.append(record)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=False) + "\n")
        if self.echo:
            extra = fields.get("message") or ""
            try:
                print(f"[{record['ts']}] {step}: {status}{' - ' + str(extra) if extra else ''}", flush=True)
            except (OSError, ValueError):  # no console (pythonw)
                pass
        return record


class LockBusy(LivetestError):
    pass


class RunLock:
    """Exclusive lock file; a lock older than ``stale_minutes`` is taken over."""

    def __init__(self, path: Path, stale_minutes: float = 300.0) -> None:
        self.path = path
        self.stale = timedelta(minutes=stale_minutes)
        self.taken_over: dict[str, Any] | None = None

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "started": iso(utcnow()), "argv": sys.argv})
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    info = json.loads(self.path.read_text(encoding="utf-8"))
                    started = parse_time(info.get("started"))
                except (OSError, ValueError):
                    info, started = {}, None
                if started is not None and utcnow() - started < self.stale:
                    raise LockBusy(f"another runner holds {self.path} since {iso(started)} (pid {info.get('pid')})") from None
                self.taken_over = info
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            return self
        raise LockBusy(f"could not take {self.path}")

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


