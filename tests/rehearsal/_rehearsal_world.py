"""Synthetic world for the end-to-end rehearsal of the live-test scorer (QA, D-115, D-116 C).

Everything this module writes is invented: sixteen teams ``S01``..``S16``, their
players, stat lines, schedules, consensus ranks, comparator projections and the
chain's commitments. No real or restricted row is read or written, the holdout
unlock flag is never set, and every path lives under the world's temporary
directory.

Layout of one world (``<root>``), mirroring the production layout of the main
checkout (``data/`` and ``artifacts/`` inside the checkout, gitignored there)::

    <root>/clone/                       git clone of the main repository at the candidate X
    <root>/clone/data/                  FFML_DATA_DIR (synthetic stores, commitments, sealed parts)
    <root>/clone/artifacts/             FFML_ARTIFACTS_DIR (model artifacts, release marker)
    <root>/settings-reports/            FFML_REPORTS_DIR (weekly verification reports and the final report)
    <root>/remote.git/                  bare repository: the fake ``origin`` holding livetest-2026
    <root>/ffml-livetest-commitments/   the runner's commitments worktree

The processed and restricted stores follow a simulated calendar: snapshot ids
are the simulated UTC time of each refresh (a pre-build refresh before every
build and a Friday refresh just after each week's data cutoff), so the scorer's
"first snapshot at or after the cutoff" rule (D-116 A3) runs on real snapshot
directories. The data builders run inside the clone's interpreter context
(``_rehearsal_driver.py``), so ``ffml`` is always the clone's code.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAIN_REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DRIVER = HERE / "_rehearsal_driver.py"
#: the candidate frozen commit X (override for a later candidate)
FROZEN_CANDIDATE = os.environ.get("FFML_REHEARSAL_COMMIT", "071c3a4")
PREREG_DOC = "docs/status/livetest-preregistration-2026.md"
CHANGELOG_DOC = "docs/status/livetest-changelog-2026.md"
WINDOW: tuple[tuple[int, int], ...] = (*((2026, w) for w in range(10, 19)), *((2027, w) for w in range(1, 10)))
HISTORY_WEEKS: dict[int, tuple[int, ...]] = {2024: tuple(range(1, 19)), 2026: tuple(range(1, 19)),
                                             2027: tuple(range(1, 10))}
#: synthetic commit identity and dates (deterministic shas for the commits the rehearsal makes)
GIT_IDENTITY = ("FFML rehearsal (synthetic)", "rehearsal@invalid.example")
#: the first processed snapshot of every world (before the window's first build)
SNAPSHOT_T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
#: the 2027 schedule appears in dim_game only from this date (published in May)
SCHEDULE_2027_PUBLISHED = datetime(2027, 5, 15, 0, 0, tzinfo=UTC)
GAME_HOURS = 4  # a game is final in dim_game this long after its kickoff

TEAMS: tuple[str, ...] = tuple(f"S{i:02d}" for i in range(1, 17))
ROSTER: tuple[tuple[str, int], ...] = (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 2), ("K", 1), ("DST", 1))
BASE_POINTS: dict[str, tuple[float, ...]] = {"QB": (19.0,), "RB": (14.0, 8.5), "WR": (14.0, 10.5, 7.5),
                                             "TE": (8.5, 5.5), "K": (8.0,), "DST": (7.5,)}
#: log-scale spread of the true weekly distribution (the calibrated chain knows it exactly)
SIGMA: dict[str, float] = {"QB": 0.38, "RB": 0.55, "WR": 0.60, "TE": 0.55, "K": 0.42, "DST": 0.55}
PLAYER_SD = 0.18      # player skill around the depth-chart base
MATCHUP_SD = 0.25     # weekly matchup effect: known to the chain, unknown to the baselines
BASELINE_NOISE = 0.40  # each baseline's error on the player's skill
CONSENSUS_NOISE = 0.30
RAW_SIGMA_FACTOR = 0.8  # the raw chain's intervals are too narrow; the walk-forward calibrator fixes that
QUANTILES: tuple[float, ...] = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
QCOLS: tuple[str, ...] = tuple(f"pred_q{int(round(q * 100)):02d}" for q in QUANTILES)
SCORINGS: tuple[str, ...] = ("ppr", "half", "std")
LINE_BASELINES: tuple[str, ...] = ("rolling_avg_4", "prior_season", "rolling_median_8")
ECR_PAGE: dict[str, str] = {"QB": "qb", "RB": "ppr-rb", "WR": "ppr-wr", "TE": "ppr-te", "K": "k", "DST": "dst"}

#: as scheduled on 2026-11-04 (pre-registration ``deadlines_utc_as_scheduled_2026-11-04``); week 18
#: has its final (non-tentative) times in this synthetic schedule
KICKOFF_2026_WINDOW: dict[int, str] = {
    10: "2026-11-13T01:15:00+00:00", 11: "2026-11-20T01:15:00+00:00", 12: "2026-11-26T01:00:00+00:00",
    13: "2026-12-04T01:15:00+00:00", 14: "2026-12-11T01:15:00+00:00", 15: "2026-12-18T01:15:00+00:00",
    16: "2026-12-25T01:15:00+00:00", 17: "2027-01-01T01:15:00+00:00", 18: "2027-01-10T18:00:00+00:00",
}


def week_key(season: int, week: int) -> str:
    return f"{season}w{week:02d}"


def first_kickoff(season: int, week: int) -> datetime:
    if season == 2026 and week in KICKOFF_2026_WINDOW:
        return datetime.fromisoformat(KICKOFF_2026_WINDOW[week])
    start = {2024: datetime(2024, 9, 6, 0, 20, tzinfo=UTC), 2026: datetime(2026, 9, 11, 0, 20, tzinfo=UTC),
             2027: datetime(2027, 9, 10, 0, 20, tzinfo=UTC)}[season]
    return start + timedelta(days=7 * (week - 1))


def game_kickoffs(season: int, week: int) -> list[datetime]:
    """Eight games: Thursday night (the first kickoff), Sunday early, late and night, Monday night."""
    k = first_kickoff(season, week)
    if season == 2026 and week == 18:  # a Sunday-only week
        offsets = [0, 0, 0, 205, 205, 205, 440, 440]
    else:  # from Friday ~01:15 UTC: Sunday 17:00, Sunday 20:25, Monday 00:20, Tuesday 01:15 UTC
        offsets = [0, 3825, 3825, 3825, 3825, 4030, 4265, 5760]
    return [k + timedelta(minutes=m) for m in offsets]


def data_cutoff(season: int, week: int) -> datetime:
    """12:00 UTC on the first Friday at least 72 hours after the week's last kickoff (D-116 A3)."""
    earliest = max(game_kickoffs(season, week)) + timedelta(hours=72)
    day = datetime(earliest.year, earliest.month, earliest.day, 12, tzinfo=UTC)
    while day.weekday() != 4 or day < earliest:
        day += timedelta(days=1)
    return day


def game_id(season: int, week: int, index: int) -> str:
    away, home = matchups(season, week)[index]
    return f"{season}_{week:02d}_{away}_{home}"


def game_kickoff(season: int, week: int, index: int) -> datetime:
    return game_kickoffs(season, week)[index]


def matchups(season: int, week: int) -> list[tuple[str, str]]:
    """Round-robin pairs (circle method), different every week."""
    n = len(TEAMS)
    rot = (season * 31 + week) % (n - 1)
    others = list(TEAMS[1:])
    others = others[rot:] + others[:rot]
    ring = [TEAMS[0], *others]
    return [(ring[i], ring[n - 1 - i]) for i in range(n // 2)]


def attempt_time(season: int, week: int) -> datetime:
    """The runner's scheduled first run: K - 24 h 15 min (D-109). Builds take 5 simulated minutes."""
    return first_kickoff(season, week) - timedelta(hours=24, minutes=15)


def friday_refresh(season: int, week: int) -> datetime:
    return data_cutoff(season, week) + timedelta(minutes=30)


def friday_scoring(season: int, week: int) -> datetime:
    """The Friday scoring task runs from 13:00 UTC (D-116 A3)."""
    return data_cutoff(season, week) + timedelta(hours=1)


# --------------------------------------------------------------------------- #
# World specification
# --------------------------------------------------------------------------- #


@dataclass
class WorldSpec:
    """One synthetic world. ``missed`` weeks get no commitment (the machine was off)."""

    name: str
    seed: int = 20261105
    missed: list[tuple[int, int]] = field(default_factory=list)
    #: factor on the calibrated chain's QB spread (1.0 = calibrated; 0.8 = intervals too narrow, L17 fails)
    qb_sigma_factor: float = 1.0
    #: (baseline, position) whose calibrated rows equal the chain's calibrated rows (that line fails)
    tie_baseline: tuple[str, str] | None = None
    #: weeks scored with the real CLI (`--week`) instead of the hook
    cli_weeks: list[tuple[int, int]] = field(default_factory=list)
    weekly_sensitivity: bool = True
    #: stat corrections: preliminary values before each window week's cutoff, corrected 2027 values a week later
    corrections: bool = False
    #: the pre-build check scenario (blocked builds; see _rehearsal_scenarios.prebuild_events)
    prebuild: bool = False
    #: D-121: game id -> "never" (never final) or the ISO time it becomes final (a late game)
    unfinal_games: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> WorldSpec:
        d = json.loads(text)
        d["missed"] = [tuple(x) for x in d.get("missed", [])]
        d["cli_weeks"] = [tuple(x) for x in d.get("cli_weeks", [])]
        if d.get("tie_baseline"):
            d["tie_baseline"] = tuple(d["tie_baseline"])
        return cls(**d)


@dataclass
class World:
    spec: WorldSpec
    root: Path

    @property
    def clone(self) -> Path:
        return self.root / "clone"

    @property
    def remote(self) -> Path:
        return self.root / "remote.git"

    @property
    def commitments(self) -> Path:
        return self.root / "ffml-livetest-commitments"

    @property
    def data_dir(self) -> Path:
        return self.clone / "data"

    @property
    def artifacts_dir(self) -> Path:
        return self.clone / "artifacts"

    @property
    def settings_reports_dir(self) -> Path:
        return self.root / "settings-reports"

    @property
    def sealed_dir(self) -> Path:
        return self.data_dir / "livetest" / "scoring" / "sealed"

    @property
    def weeks_dir(self) -> Path:
        return self.data_dir / "livetest" / "scoring" / "weeks"

    @property
    def weekly_reports_dir(self) -> Path:
        """Weekly verification reports and the final report: ``FFML_REPORTS_DIR/livetest`` since QA-F27."""
        return self.settings_reports_dir / "livetest"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    def env(self, **extra: str) -> dict[str, str]:
        """Child environment: the clone first on ``sys.path``, synthetic directories, no unlock flag."""
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("FFML_") and k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP",
                                                          "ALERT_WEBHOOK_URL")}
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(self.clone),
                   FFML_DATA_DIR=str(self.data_dir), FFML_ARTIFACTS_DIR=str(self.artifacts_dir),
                   FFML_REPORTS_DIR=str(self.settings_reports_dir),
                   FFML_REHEARSAL_CLONE=str(self.clone), FFML_REHEARSAL_SPEC=self.spec.to_json())
        env.pop("FFML_HOLDOUT_UNLOCK", None)
        if stream_path():
            env["REHEARSAL_STREAM_FILE"] = stream_path()
        env.update(extra)
        return env


def stream_path() -> str | None:
    """The file every scenario result is appended to as soon as it exists (``FFML_REHEARSAL_EVIDENCE`` + .partial.jsonl)."""
    if os.environ.get("REHEARSAL_STREAM_FILE"):
        return os.environ["REHEARSAL_STREAM_FILE"]
    ev = os.environ.get("FFML_REHEARSAL_EVIDENCE")
    return f"{ev}.partial.jsonl" if ev else None


def stream(world: str, key: str, value: Any) -> Any:
    """Append one result as one JSON line (one write call, so lines of parallel worlds do not interleave)."""
    path = stream_path()
    if path:
        line = json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), "world": world, "key": key,
                           "result": value}, default=str) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    return value


# --------------------------------------------------------------------------- #
# Git helpers (run in the test process; they never touch the main checkout's refs)
# --------------------------------------------------------------------------- #


def git(cwd: Path, *args: str, env: Mapping[str, str] | None = None, check: bool = True) -> str:
    proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(env) if env is not None else None)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()[-800:]}")
    return proc.stdout.strip()


def git_dates(when: datetime) -> dict[str, str]:
    stamp = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
            "GIT_AUTHOR_NAME": GIT_IDENTITY[0], "GIT_AUTHOR_EMAIL": GIT_IDENTITY[1],
            "GIT_COMMITTER_NAME": GIT_IDENTITY[0], "GIT_COMMITTER_EMAIL": GIT_IDENTITY[1]}


def rmtree_force(path: Path) -> None:
    """Remove a tree that holds git objects (read-only on Windows)."""
    import stat

    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if Path(path).exists():
        shutil.rmtree(path, onerror=onerror)


def make_clone(world: World) -> str:
    """Clone main (only that branch: the real livetest-2026 is never fetched), pin X, fake origin."""
    world.root.mkdir(parents=True, exist_ok=True)
    git(world.root, "clone", "-q", "--no-local", "--no-hardlinks", "--single-branch", "--branch", "main",
        MAIN_REPO.as_uri(), str(world.clone))
    x = git(world.clone, "rev-parse", "--verify", f"{FROZEN_CANDIDATE}^{{commit}}")
    git(world.clone, "reset", "-q", "--hard", x)
    git(world.clone, "config", "user.name", GIT_IDENTITY[0])
    git(world.clone, "config", "user.email", GIT_IDENTITY[1])
    git(world.clone, "config", "commit.gpgsign", "false")
    git(world.root, "init", "-q", "--bare", str(world.remote))
    git(world.clone, "remote", "remove", "origin")
    git(world.clone, "remote", "add", "origin", world.remote.as_uri())
    world.settings_reports_dir.mkdir(parents=True, exist_ok=True)
    world.evidence.mkdir(parents=True, exist_ok=True)
    return x


def read_text_lf(path: Path) -> tuple[str, str]:
    raw = path.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def write_text_eol(path: Path, text: str, eol: str) -> None:
    path.write_bytes(text.replace("\n", eol).encode("utf-8"))


def fill_preregistration(clone: Path, x: str, stored_digest: str) -> None:
    """The freeze edit (commit field), plus the rehearsal-only stored-parts digest of the synthetic parts."""
    p = clone / PREREG_DOC
    text, eol = read_text_lf(p)
    old_commit = '"commit": "<frozen commit>",'
    old_digest = '"digest": "309ca749e087c9facd0beec9a63fba23f5d1596ea039ce33853c9a67369ffa54",'
    assert text.count(old_commit) == 1 and text.count(old_digest) == 1, "pre-registration fields not found once"
    text = text.replace(old_commit, f'"commit": "{x}",').replace(old_digest, f'"digest": "{stored_digest}",')
    write_text_eol(p, text, eol)


def changelog_row(sha: str, paths: str, reason: str, first_week: str = "2026w10", date: str = "2026-11-12") -> str:
    return f"| {date} | `{sha[:12]}` | {paths} | {reason} | no effect on commitments (synthetic rehearsal) | QA rehearsal | {first_week} |"


def append_changelog(clone: Path, rows: Sequence[str]) -> None:
    """Insert rows after the last row of the change-log table (pure additions)."""
    p = clone / CHANGELOG_DOC
    text, eol = read_text_lf(p)
    lines = text.split("\n")
    last = max(i for i, ln in enumerate(lines) if ln.startswith("|"))
    lines[last + 1:last + 1] = list(rows)
    write_text_eol(p, "\n".join(lines), eol)


def edit_changelog(clone: Path, old: str, new: str) -> None:
    p = clone / CHANGELOG_DOC
    text, eol = read_text_lf(p)
    assert text.count(old) == 1, f"{old!r} not found once in the change log"
    write_text_eol(p, text.replace(old, new), eol)


def commit_paths(clone: Path, paths: Sequence[str], message: str, when: datetime) -> str:
    git(clone, "add", "--", *paths)
    git(clone, "commit", "-q", "-m", message, env=git_dates(when))
    return git(clone, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- #
# Synthetic data (runs in the driver, inside the clone's interpreter context)
# --------------------------------------------------------------------------- #


def _norm_ppf(p):
    from scipy.stats import norm

    return norm.ppf(p)


def _norm_cdf(x):
    from scipy.stats import norm

    return norm.cdf(x)


def all_weeks() -> list[tuple[int, int]]:
    return [(s, w) for s in sorted(HISTORY_WEEKS) for w in HISTORY_WEEKS[s]]


def truth_frame(spec: WorldSpec):
    """One row per entity and game week: skill, matchup, true median, spread, outcome and stat-correction deltas."""
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(spec.seed)
    players = []
    for team in TEAMS:
        for pos, n in ROSTER:
            for d in range(n):
                eid = team if pos == "DST" else f"SYN-{team}-{pos}{d}"
                players.append((eid, pos, team, d, BASE_POINTS[pos][d]))
    skill = {p[0]: p[4] * float(np.exp(rng.normal(0.0, PLAYER_SD))) for p in players}
    rows = []
    for season, week in all_weeks():
        kicks = game_kickoffs(season, week)
        for gi, (away, home) in enumerate(matchups(season, week)):
            gid = f"{season}_{week:02d}_{away}_{home}"
            for team, opp in ((away, home), (home, away)):
                for eid, pos, tm, d, _ in players:
                    if tm != team:
                        continue
                    rows.append({"entity_id": eid, "position": pos, "team": team, "opponent": opp, "game_id": gid,
                                 "season": season, "week": week, "kickoff_utc": kicks[gi], "depth": d,
                                 "skill": skill[eid]})
    f = pl.DataFrame(rows).with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
                                        pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC")))
    n = f.height
    matchup = np.exp(rng.normal(0.0, MATCHUP_SD, n))
    u = rng.random(n)
    # stratified PIT in the test window, per position: the calibrated chain's coverage is ~0.80 by design
    pos_arr = f["position"].to_numpy()
    window = set(WINDOW)
    in_window = np.array([(s, w) in window for s, w in zip(f["season"].to_list(), f["week"].to_list(), strict=True)])
    for pos in SIGMA:
        idx = np.flatnonzero(in_window & (pos_arr == pos))
        perm = rng.permutation(idx.size)
        u[idx] = (perm + rng.random(idx.size)) / idx.size
    sigma = np.array([SIGMA[p] for p in pos_arr])
    median = f["skill"].to_numpy() * matchup
    latent = median * np.exp(sigma * _norm_ppf(np.clip(u, 1e-9, 1 - 1e-9)))
    integer = np.isin(pos_arr, ["K", "DST"])
    points = np.where(integer, np.maximum(np.rint(latent), 0.0), np.round(latent, 1))
    b_noise = {b: rng.normal(0.0, BASELINE_NOISE, n) for b in LINE_BASELINES}
    c_noise = rng.normal(0.0, CONSENSUS_NOISE, n)
    # stat corrections (used only when spec.corrections): a preliminary value before the cutoff for 40% of
    # window rows, and a corrected value one week after the cutoff for 30% of 2027 rows
    step = np.where(integer, 1.0, 0.7)
    prelim = np.where(in_window & (rng.random(n) < 0.4), step * rng.choice([-1.0, 1.0], n), 0.0)
    is2027 = f["season"].to_numpy() == 2027
    corr = np.where(in_window & is2027 & (rng.random(n) < 0.3), 2 * step * rng.choice([-1.0, 1.0], n), 0.0)
    f = f.with_columns(pl.Series("matchup", matchup), pl.Series("median", median), pl.Series("sigma", sigma),
                       pl.Series("u", u), pl.Series("points", points), pl.Series("prelim_delta", prelim),
                       pl.Series("corr_delta", corr),
                       *[pl.Series(f"noise_{b}", v) for b, v in b_noise.items()], pl.Series("noise_consensus", c_noise))
    return f


def stat_columns(truth, points_col: str = "points"):
    """Stat lines that score exactly ``points`` under PPR (ffml/scoring and the evaluator reference)."""
    import numpy as np
    import polars as pl

    pos = truth["position"].to_numpy()
    y = truth[points_col].to_numpy()
    n = len(y)
    z = np.zeros(n, dtype=np.int64)
    out = {c: z.copy() for c in ("pass_att", "completions", "pass_yds", "pass_td", "interceptions", "carries",
                                 "rush_yds", "rush_td", "targets", "receptions", "rec_yds", "rec_td",
                                 "two_pt_conversions", "fumbles_lost", "return_tds", "fg_made_0_19",
                                 "fg_made_20_29", "fg_made_30_39", "fg_made_40_49", "fg_made_50_59", "fg_made_60p",
                                 "fg_made_0_39", "fg_made_50p", "fg_missed", "pat_att", "pat_made")}
    tenth = np.rint(y * 10).astype(np.int64)
    qb = pos == "QB"
    td = np.minimum(tenth // 100, 3)
    out["pass_td"][qb] = td[qb]
    out["pass_att"][qb] = 30
    out["completions"][qb] = 20
    out["rush_yds"][qb] = tenth[qb] - 40 * td[qb]
    out["carries"][qb] = 4
    rb = pos == "RB"
    rec = tenth // 60
    out["receptions"][rb] = rec[rb]
    out["targets"][rb] = rec[rb] + 1
    out["rush_yds"][rb] = tenth[rb] - 10 * rec[rb]
    out["carries"][rb] = np.maximum(out["rush_yds"][rb] // 45, 1)
    wr = np.isin(pos, ["WR", "TE"])
    rec = tenth // 40
    out["receptions"][wr] = rec[wr]
    out["targets"][wr] = rec[wr] + 2
    out["rec_yds"][wr] = tenth[wr] - 10 * rec[wr]
    k = pos == "K"
    pts = np.rint(y).astype(np.int64)
    fg = np.minimum(pts // 3, 4)
    out["fg_made_30_39"][k] = fg[k]
    out["fg_made_0_39"][k] = fg[k]
    out["pat_made"][k] = pts[k] - 3 * fg[k]
    out["pat_att"][k] = pts[k] - 3 * fg[k]
    return pl.DataFrame({c: pl.Series(c, v, dtype=pl.Int32) for c, v in out.items()})


def _week_times() -> dict[tuple[int, int], tuple[datetime, datetime]]:
    return {(s, w): (data_cutoff(s, w), data_cutoff(s, w) + timedelta(days=7)) for s, w in all_weeks()}


def state_as_of(truth, when: datetime, spec: WorldSpec):
    """``(dim_game, played rows with as-of points, consensus ranks)`` as a refresh at ``when`` would see them."""
    import numpy as np
    import polars as pl

    times = _week_times()
    games = []
    for season, week in all_weeks():
        if season == 2027 and when < SCHEDULE_2027_PUBLISHED:
            continue
        kicks = game_kickoffs(season, week)
        for gi, (away, home) in enumerate(matchups(season, week)):
            gid = f"{season}_{week:02d}_{away}_{home}"
            final = kicks[gi] + timedelta(hours=GAME_HOURS) <= when
            late = spec.unfinal_games.get(gid)
            if late == "never":
                final = False
            elif late:
                final = datetime.fromisoformat(late) <= when
            games.append({"game_id": gid, "season": season, "week": week,
                          "game_type": "REG", "away_team": away, "home_team": home, "kickoff_utc": kicks[gi],
                          "status": "final" if final else "scheduled", "away_score": 20 if final else None,
                          "home_score": 24 if final else None, "result": 4 if final else None})
    dim_game = pl.DataFrame(games).with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
                                                pl.col("kickoff_utc").cast(pl.Datetime("us", "UTC")))
    final_ids = set(dim_game.filter(pl.col("status") == "final")["game_id"].to_list())
    played = truth.filter(pl.col("game_id").is_in(list(final_ids)))
    pts = played["points"].to_numpy().copy()
    if spec.corrections:
        cut = np.array([times[(s, w)][0] <= when for s, w in zip(played["season"].to_list(), played["week"].to_list(), strict=True)])
        corr = np.array([times[(s, w)][1] <= when for s, w in zip(played["season"].to_list(), played["week"].to_list(), strict=True)])
        pts = np.where(~cut, pts + played["prelim_delta"].to_numpy(), pts)
        pts = np.where(corr, pts + played["corr_delta"].to_numpy(), pts)
        pts = np.maximum(pts, 0.0)
    played = played.with_columns(pl.Series("points_as_of", pts))
    scraped = [(s, w) for s, w in all_weeks() if first_kickoff(s, w) - timedelta(hours=24) <= when
               and not (s == 2027 and when < SCHEDULE_2027_PUBLISHED)]
    ecr_base = truth.filter(pl.struct("season", "week").map_elements(
        lambda r: (r["season"], r["week"]) in set(scraped), return_dtype=pl.Boolean))
    return dim_game, played, ecr_base


def write_snapshot_as_of(truth, when: datetime, spec: WorldSpec, processed_root: Path | None = None) -> dict[str, str]:
    """A refresh at simulated time ``when``: one new snapshot of each dataset the scorer pins, ids = ``when``."""
    import numpy as np
    import polars as pl

    from ffml.config import get_settings
    from ffml.data import store as ST

    processed = Path(processed_root) if processed_root is not None else get_settings().processed_dir
    dim_game, played, ecr_base = state_as_of(truth, when, spec)
    stats = stat_columns(played, "points_as_of")
    base = played.select("entity_id", "position", "team", "opponent", "game_id", "season", "week", "kickoff_utc")
    full = pl.concat([base, stats], how="horizontal")
    players = full.filter(pl.col("position") != "DST").rename({"entity_id": "player_id"}).with_columns(
        pl.lit("REG").alias("game_type"), pl.lit(True).alias("played"))
    dst = played.filter(pl.col("position") == "DST").select(
        "team", "season", "week", "game_id", "opponent", pl.lit("REG").alias("game_type"),
        pl.col("points_as_of").cast(pl.Int32).alias("sacks"), *[pl.lit(0, dtype=pl.Int32).alias(c) for c in (
            "interceptions", "fumble_recoveries", "forced_fumbles", "def_td", "return_td", "safeties",
            "blocked_kicks")],
        pl.lit(24, dtype=pl.Int32).alias("points_allowed"), pl.lit(330.0).alias("yards_allowed"))
    dim_player = truth.filter(pl.col("position") != "DST").select(
        pl.col("entity_id").alias("player_id"), "position", "team").unique(maintain_order=True).with_columns(
        (pl.lit("Synthetic ") + pl.col("player_id")).alias("display_name"))
    score = (np.log(ecr_base["skill"].to_numpy()) + 0.5 * np.log(ecr_base["matchup"].to_numpy())
             + ecr_base["noise_consensus"].to_numpy())
    ecr = ecr_base.select("entity_id", "position", "team", "season", "week").with_columns(
        pl.Series("_score", score)).with_columns(
        pl.col("_score").rank("ordinal", descending=True).over(["season", "week", "position"]).cast(pl.Float64).alias("ecr"))
    scrape = {(s, w): (first_kickoff(s, w) - timedelta(days=1)).date().isoformat() for s, w in all_weeks()}
    ecr = ecr.with_columns(
        pl.col("position").replace_strict(ECR_PAGE, return_dtype=pl.String).alias("fp_page"),
        pl.lit(2.0).alias("sd"), ("fp-" + pl.col("entity_id")).alias("fantasypros_id"),
        pl.when(pl.col("position") == "DST").then(None).otherwise(pl.col("entity_id")).alias("player_id"),
        pl.lit(True).alias("valid_for_game"), pl.lit("REG").alias("game_type"),
        pl.struct("season", "week").map_elements(lambda r: scrape[(r["season"], r["week"])],
                                                 return_dtype=pl.String).alias("scrape_date"),
    ).drop("_score", "entity_id")

    def by_season(frame):
        return {int(s): frame.filter(pl.col("season") == s) for s in sorted(frame["season"].unique().to_list())}

    saved = ST._utc_now
    ST._utc_now = lambda: when.astimezone(UTC)
    try:
        store = ST.SnapshotStore(processed)
        meta = {"synthetic": True, "as_of": when.isoformat()}
        ids = {
            "dim_game": store.write_snapshot("dim_game", by_season(dim_game), meta=meta),
            "fact_player_game": store.write_snapshot("fact_player_game", by_season(players), meta=meta),
            "fact_dst_game": store.write_snapshot("fact_dst_game", by_season(dst), meta=meta),
            "dim_player": store.write_snapshot("dim_player", {"all": dim_player}, meta=meta),
        }
        rstore = ST.SnapshotStore(processed / "restricted")
        ids["fact_ecr_weekly"] = rstore.write_snapshot("fact_ecr_weekly", by_season(ecr), meta=meta)
    finally:
        ST._utc_now = saved
    return ids


def prediction_frame(truth, model: str, center, sigma, *, extra: Mapping[str, Any] | None = None):
    """Seven quantiles of a log-normal with the given log-median ``center`` and log-spread ``sigma``."""
    import numpy as np
    import polars as pl

    center = np.asarray(center, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    cols = {c: np.exp(center + sigma * _norm_ppf(q)) for c, q in zip(QCOLS, QUANTILES, strict=True)}
    top = np.array([BASE_POINTS[p][0] for p in truth["position"].to_list()])
    boom = 1.0 - _norm_cdf((np.log(1.5 * top) - center) / sigma)
    bust = _norm_cdf((np.log(0.5 * top) - center) / sigma)
    mean = np.exp(center + sigma ** 2 / 2)
    frames = []
    for scoring in SCORINGS:
        frames.append(truth.select("entity_id", "position", "team", "game_id", "season", "week").with_columns(
            pl.lit(scoring).alias("scoring"), pl.lit(model).alias("model"), pl.Series("pred_mean", mean),
            *[pl.Series(c, v) for c, v in cols.items()], pl.Series("boom_prob", boom), pl.Series("bust_prob", bust),
            pl.lit(0.97).alias("active_prob"), pl.Series("pred_mean_uncond", 0.97 * mean),
            pl.Series("pred_q50_uncond", cols["pred_q50"]), *[pl.lit(v).alias(k) for k, v in (extra or {}).items()]))
    return pl.concat(frames)


def _window_rows(truth):
    import polars as pl

    window = set(WINDOW)
    return truth.filter(pl.struct("season", "week").map_elements(lambda r: (r["season"], r["week"]) in window,
                                                                 return_dtype=pl.Boolean))


def chain_frames(spec: WorldSpec, truth):
    """``(calibrated, raw)`` chain rows for every window week (the calibrated chain is the true distribution)."""
    import numpy as np

    w = _window_rows(truth)
    center = np.log(w["median"].to_numpy())
    sigma = w["sigma"].to_numpy().copy()
    raw = prediction_frame(w, "chain_v1", center, sigma * RAW_SIGMA_FACTOR)
    qb = (w["position"] == "QB").to_numpy()
    sigma[qb] = sigma[qb] * spec.qb_sigma_factor
    cal = prediction_frame(w, "chain_v2", center, sigma)
    return w, cal, raw


def baseline_frames(spec: WorldSpec, truth, cal):
    """The three line baselines, raw and calibrated, PPR and half (the run's other scorings are not read)."""
    import numpy as np
    import polars as pl

    w = _window_rows(truth)
    frames = []
    for b in LINE_BASELINES:
        center = np.log(w["skill"].to_numpy()) + w[f"noise_{b}"].to_numpy()
        s_raw = w["sigma"].to_numpy()
        s_cal = np.sqrt(s_raw ** 2 + MATCHUP_SD ** 2 + BASELINE_NOISE ** 2)
        raw = prediction_frame(w, b, center, s_raw)
        calf = prediction_frame(w, b + "+cal", center, s_cal)
        if spec.tie_baseline and spec.tie_baseline[0] == b:
            pos = spec.tie_baseline[1]
            same = cal.filter(pl.col("position") == pos).with_columns(pl.lit(b + "+cal").alias("model"))
            calf = pl.concat([calf.filter(pl.col("position") != pos), same.select(calf.columns)])
        frames += [raw, calf]
    out = pl.concat(frames, how="diagonal_relaxed")
    return out.filter(pl.col("scoring").is_in(["ppr", "half"]))


def write_comparators_run(world: World, spec: WorldSpec, truth, cal) -> Path:
    """data/backtests/livetest_comparators: predictions with harness outcome columns (from a scratch full store).

    Since D-116 A3 the scorer replaces the comparators' outcome columns with the chain's pinned ones, so these
    columns only make the run look like a real one.
    """
    import polars as pl

    from ffml.data.store import SnapshotStore
    from ffml.eval import livetest_score as LS
    from ffml.eval.harness import PREDICTIONS, backtests_root

    scratch = world.root / "scratch-full-store"
    write_snapshot_as_of(truth, datetime(2028, 1, 1, tzinfo=UTC), spec, processed_root=scratch)
    store = SnapshotStore(scratch)
    rows = baseline_frames(spec, truth, cal)
    parts = [LS.harness_outcomes(rows.filter(pl.col("season") == s), s, store) for s in sorted({s for s, _ in WINDOW})]
    frame = pl.concat(parts, how="diagonal_relaxed")
    run_dir = backtests_root() / LS.COMPARATORS_RUN
    run_dir.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(run_dir / PREDICTIONS)
    (run_dir / "_manifest.json").write_text(json.dumps({
        "run_id": LS.COMPARATORS_RUN, "config_name": LS.COMPARATORS_CONFIG, "purpose": LS.PURPOSE,
        "models": sorted(frame["model"].unique().to_list()), "report_seasons": [2026, 2027],
        "note": "SYNTHETIC comparators for the QA rehearsal: the three line baselines only (raw and +cal)",
    }, indent=2) + "\n", encoding="utf-8")
    rmtree_force(scratch)
    return run_dir


def write_stored_parts() -> str:
    """The 97 stored raw parts the commitments list (synthetic content); returns their digest."""
    import polars as pl

    from ffml.sim import livetest as LV

    records = []
    for src in LV.STORED_SOURCES:
        for w in src.weeks:
            p = LV.stored_part(src.season, w)
            p.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"entity_id": ["SYN-STORED"], "season": [src.season], "week": [w], "model": [LV.RAW_KEY],
                          "note": ["synthetic stored part (QA rehearsal)"]}).write_parquet(p)
            records.append({"season": src.season, "week": w, "origin": f"stored:{src.run_id}", "path": LV.rel(p),
                            "sha256": LV.sha256_file(p)})
    assert len(records) == 97
    return LV.digest_of(records)


def stored_records() -> list[dict[str, Any]]:
    from ffml.sim import livetest as LV

    out = []
    for src in LV.STORED_SOURCES:
        for w in src.weeks:
            p = LV.stored_part(src.season, w)
            out.append({"season": src.season, "week": w, "origin": f"stored:{src.run_id}", "path": LV.rel(p),
                        "sha256": LV.sha256_file(p)})
    return out


def walkforward_weeks_before(season: int, week: int) -> list[tuple[int, int]]:
    """Walk-forward cache weeks from 2026 w8 up to the week before ``(season, week)``."""
    out = [(2026, w) for w in range(8, 19)] + [(2027, w) for w in range(1, 10)]
    return [x for x in out if x < (season, week)]


def write_release_marker() -> dict[str, Any]:
    """Synthetic copy of the production state: 2025 released as data, not approved (D-098)."""
    from ffml.config import get_settings

    s = get_settings()
    payload = {"season": 2025, "released_at": "2026-11-04T20:00:00+00:00", "model_version": "SYNTHETIC",
               "evaluator_report": "SYNTHETIC (QA rehearsal)", "approved": False,
               "note": "synthetic marker for the QA rehearsal; mirrors the production state", "git": "synthetic"}
    s.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (s.artifacts_dir / "holdout_release.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def build_world(world: World) -> dict[str, Any]:
    """Everything except the commitments (the runner builds those week by week, through the build hook)."""
    from ffml.config import get_settings

    spec = world.spec
    write_release_marker()
    truth = truth_frame(spec)
    t0_ids = write_snapshot_as_of(truth, SNAPSHOT_T0, spec)
    w, cal, raw = chain_frames(spec, truth)
    run_dir = write_comparators_run(world, spec, truth, cal)
    digest = write_stored_parts()
    cache = get_settings().data_dir / "rehearsal_world"
    cache.mkdir(parents=True, exist_ok=True)
    cal.write_parquet(cache / "chain_cal.parquet")
    raw.write_parquet(cache / "chain_raw.parquet")
    truth.write_parquet(cache / "truth.parquet")
    return {"snapshots_t0": t0_ids, "stored_digest": digest, "comparators_run": str(run_dir),
            "rows": {"truth": truth.height, "chain_cal": cal.height, "chain_raw": raw.height}}


def build_commitment(world: World, season: int, week: int, created: datetime) -> Path:
    """What ``ffml livetest build-week`` writes, with synthetic rows (the builder's own helpers)."""
    import polars as pl

    from ffml.config import get_settings
    from ffml.holdout import release_info
    from ffml.models import chain_v2
    from ffml.sim import livetest as LV

    s = get_settings()
    kickoff = first_kickoff(season, week)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    version = f"chain_v2-primary-c{season}w{week:02d}-{stamp}"
    cache = s.data_dir / "rehearsal_world"
    cal = pl.read_parquet(cache / "chain_cal.parquet").filter((pl.col("season") == season) & (pl.col("week") == week))
    raw = pl.read_parquet(cache / "chain_raw.parquet").filter((pl.col("season") == season) & (pl.col("week") == week))
    artifact = LV.artifact_root() / version
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "train_manifest.json").write_bytes(LV._json_bytes(
        {"model": chain_v2.MODEL_NAME, "version": version, "cutoff": [season, week], "synthetic": True}))
    state = {"method": "walk_forward", "calibration_version": "v3-tie-aware-quantile",
             "quantile_calibrator": chain_v2.QUANTILE_CALIBRATOR_REPR, "isotonic": chain_v2.ISOTONIC_CALIBRATOR_REPR,
             "history_first": [2021, 1], "history_last": [season, week - 1] if week > 1 else [season - 1, 18],
             "synthetic": True}
    state_file = artifact / "calibration_state.json"
    state_file.write_bytes(LV._json_bytes(state))
    state_sha = LV.sha256_file(state_file)
    cal = cal.with_columns(pl.lit(f"walk_forward:{state_sha[:12]}").alias("calibration_version"),
                           pl.lit("not approved").alias("approval_status"), pl.lit(version).alias("model_version"))
    raw = raw.with_columns(pl.lit("not approved").alias("approval_status"))
    run_id = f"{season}w{week:02d}-{stamp}-private"
    pred_dir = s.private_predictions_dir / run_id
    pred_dir.mkdir(parents=True, exist_ok=True)
    cal.write_parquet(pred_dir / "player_summary.parquet")
    raw.write_parquet(pred_dir / "player_summary_raw.parquet")
    (pred_dir / "_manifest.json").write_bytes(LV._json_bytes(
        {"run_id": run_id, "environment": "private_test", "approval_status": "not approved",
         "label": "not approved", "model_version": version, "rows": cal.height, "synthetic": True}))
    written = []
    for ws, ww in walkforward_weeks_before(season, week):
        p = LV.walkforward_dir() / "parts" / LV.RAW_KEY / f"{ws}_w{ww:02d}.parquet"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"entity_id": ["SYN-WF"], "season": [ws], "week": [ww], "model": [LV.RAW_KEY]}).write_parquet(p)
            p.with_suffix(".json").write_bytes(LV._json_bytes({"season": ws, "week": ww, "synthetic": True,
                                                                "sha256": LV.sha256_file(p)}))
            written += [p, p.with_suffix(".json")]
    sources = stored_records() + [
        {"season": ws, "week": ww, "origin": "walkforward",
         "path": LV.rel(LV.walkforward_dir() / "parts" / LV.RAW_KEY / f"{ws}_w{ww:02d}.parquet"),
         "sha256": LV.sha256_file(LV.walkforward_dir() / "parts" / LV.RAW_KEY / f"{ws}_w{ww:02d}.parquet")}
        for ws, ww in walkforward_weeks_before(season, week)]
    model_files = LV.file_records(LV.tree_files(artifact), "model_artifact")
    pred_files = LV.file_records(LV.tree_files(pred_dir), "predictions")
    wf_files = LV.file_records(written, "walkforward_part")
    pins = chain_v2.check_pins()
    code = LV.code_state()  # the commit the build runs (the runner's code_cwd: the clone's HEAD)
    payload = {
        "format": LV.COMMITMENT_FORMAT, "model": chain_v2.MODEL_NAME, "season": season, "week": week,
        "rehearsal": False, "environment": "private_test", "approval_status": "not approved",
        "label": "not approved", "decision": "D-098", "as_of": created.isoformat(),
        "rows_as_of": created.isoformat(), "cutoff": kickoff.isoformat(), "created_at": created.isoformat(),
        "first_kickoff": kickoff.isoformat(),
        "seconds_before_kickoff": round((kickoff - created).total_seconds(), 1), "model_version": version,
        "registry": {"path": "artifacts/registry.json", "status": "candidate", "name": version, "calibration": None},
        "model_artifact": {"path": LV.rel(artifact), "sha256": LV.digest_of(model_files),
                           "files": {r["path"]: r["sha256"] for r in model_files}, "cutoff": [season, week],
                           "train_manifest_sha256": LV.sha256_file(artifact / "train_manifest.json")},
        "calibrator_state": {"path": LV.rel(state_file), "sha256": state_sha, "rows": 0, "cutoff": kickoff.isoformat(),
                             **{k: state[k] for k in ("calibration_version", "quantile_calibrator", "isotonic",
                                                      "history_first", "history_last")}},
        "calibration_history": {"sha256": LV.digest_of(sources), "weeks": len(sources), "sources": sources},
        "predictions": {"path": LV.rel(pred_dir), "run_id": run_id,
                        "manifest_sha256": LV.sha256_file(pred_dir / "_manifest.json"), "rows": cal.height,
                        "skipped_final_games": 0},
        "outputs": [*pred_files, *model_files, *wf_files],
        "snapshots": {"features": {"player_week": "SYNTHETIC"}, "processed_live_rows": {"dim_game": "SYNTHETIC"},
                      "processed_at_start": {}},
        "seeds": {"seed": chain_v2.SEED, "week_seed": chain_v2.SEED + 100 * season + week},
        "n_sims": chain_v2.N_SIMS, "pins": pins,
        "code": {**code, "commit_at_end": code["commit"], "code_clean_at_end": code["code_clean"], "head_moved": False},
        "holdout_release": release_info(),
        "build": {"seconds": 300.0, "peak_memory_mb": 4100.0, "timings": {}, "python": sys.version.split()[0],
                  "synthetic": True},
    }
    path = LV.commitment_path(season, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    LV.write_commitment(path, payload)
    return path


# --------------------------------------------------------------------------- #
# Running things (test process)
# --------------------------------------------------------------------------- #


_COUNTER = itertools.count(1)

#: ``python -m ffml.eval.livetest_score <args>`` from the clone: asserts ffml is the clone's, then runs ``main()``.
#: ``FFML_REHEARSAL_NOW`` sets ``livetest_score.utcnow`` (the scorer's clock, which its docstring says tests
#: replace) to the simulated time; nothing else is changed.
SCORER_WRAPPER = (
    "import os, pathlib, sys\n"
    "from datetime import datetime\n"
    "import ffml\n"
    "clone = pathlib.Path(os.environ['FFML_REHEARSAL_CLONE']).resolve()\n"
    "where = pathlib.Path(ffml.__file__).resolve()\n"
    "assert clone in where.parents, f'ffml imported from {where}, not from the clone {clone}'\n"
    "import ffml.eval.livetest_score as LS\n"
    "assert clone in pathlib.Path(LS.__file__).resolve().parents\n"
    "now = os.environ.get('FFML_REHEARSAL_NOW')\n"
    "if now:\n"
    "    LS.utcnow = lambda: datetime.fromisoformat(now)\n"
    "print(f'REHEARSAL ffml from {where}; clock {now or \"real\"}', file=sys.stderr)\n"
    "sys.exit(LS.main(sys.argv[1:]))\n"
)


@dataclass
class Proc:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {"args": self.args, "returncode": self.returncode, "stdout": self.stdout[-6000:],
                "stderr": self.stderr[-6000:], "seconds": round(self.seconds, 1)}


def run(world: World, args: Sequence[str], *, cwd: Path | None = None, timeout: float = 7200,
        env: Mapping[str, str] | None = None) -> Proc:
    import time

    t0 = time.perf_counter()
    proc = subprocess.run(list(args), cwd=cwd or world.clone, env=dict(env or world.env()), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return Proc(list(args), proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - t0)


def scorer_cli(world: World, *cli_args: str, now: datetime | None = None, timeout: float = 7200,
               cwd: Path | None = None, env: Mapping[str, str] | None = None) -> Proc:
    """The scorer's CLI from the clone (the wrapper asserts ffml is the clone's)."""
    e = dict(env or world.env())
    if now is not None:
        e["FFML_REHEARSAL_NOW"] = now.isoformat()
    return run(world, [sys.executable, "-c", SCORER_WRAPPER, *cli_args], timeout=timeout, cwd=cwd, env=e)


def driver(world: World, command: str, payload: Mapping[str, Any] | None = None, *, timeout: float = 7200,
           cwd: Path | None = None, env: Mapping[str, str] | None = None) -> tuple[Proc, dict[str, Any]]:
    """Run ``_rehearsal_driver.py <command>`` in the clone's context; returns the process and its JSON result."""
    out = world.evidence / f"driver_{next(_COUNTER):03d}_{command}.json"
    p = run(world, [sys.executable, str(DRIVER), command, json.dumps(payload or {}, default=str), str(out)],
            timeout=timeout, cwd=cwd, env=env)
    result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    return p, result


def verdict_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "Verdict:" in ln]


METRIC_WORDS: tuple[str, ...] = ("MAE", "pinball", "coverage", "start/sit", "Spearman", "Verdict", "not passed",
                                 "beats", "pass ", "interval")


def metric_leaks(text: str) -> list[str]:
    """Decimal numbers (other than dotted versions) and metric words in a weekly output."""
    body = re.sub(r"\b\d+\.\d+\.\d+\b", " ", text)
    found = [f"decimal {m}" for m in re.findall(r"\b\d+\.\d+\b", body)]
    found += [f"word {w!r}" for w in METRIC_WORDS if w in text]
    return found


def line_status(final_md: str) -> dict[str, str]:
    """``{line id: 'pass' | 'not passed'}`` from the binding lines table of the final report."""
    out: dict[str, str] = {}
    section = final_md.split("## The 22 lines", 1)[-1].split("## Report-only", 1)[0].split("## Verdict", 1)[0]
    section = section.split("## Integrity findings", 1)[0]
    for ln in section.splitlines():
        m = re.match(r"^\|\s*(L\d\d)\s*\|.*?\|\s*(pass|not passed)\s*\|", ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def checks_table(final_md: str) -> list[tuple[str, str, str]]:
    section = final_md.split("## Integrity checks", 1)[-1].split("## The 22 lines", 1)[0]
    rows = []
    for ln in section.splitlines():
        if not ln.startswith("|") or ln.startswith("| check") or ln.startswith("| ---"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 3:
            rows.append((cells[0], cells[1], "|".join(cells[2:])))
    return rows


def flagged_table(final_md: str) -> list[list[str]]:
    """Rows of the "Integrity findings that do not change the verdict" table (flagged MISSED markers)."""
    if "## Integrity findings" not in final_md:
        return []
    section = final_md.split("## Integrity findings", 1)[1].split("## Verdict", 1)[0]
    rows = []
    for ln in section.splitlines():
        if ln.startswith("|") and not ln.startswith("| week") and not ln.startswith("| ---"):
            rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_listing(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8")


def iter_weeks(spec: WorldSpec) -> Iterable[tuple[int, int, bool]]:
    for s, w in WINDOW:
        yield s, w, (s, w) in set(spec.missed)
