"""Live-test change-log helper (D-115, D-120): list, and append rows for, commits without an entry.

    python scripts/livetest_changelog.py --from <X> [--to HEAD]               # list what is missing
    python scripts/livetest_changelog.py --from <X> --append [--commit]       # add rows for the safe ones

Given the frozen commit X, it lists every commit in ``X..TO`` that has no row in
``docs/status/livetest-changelog-2026.md``. A commit that only appends to the
change log needs no row and is not listed (the scorer's rule: one parent,
the change log is its only file, lines added and none deleted).

With ``--append`` it adds one row per missing commit whose every path is
outside the pinned build: date (the commit's, UTC), commit, paths,
reason (the commit subject), verification "paths outside the pinned build;
no effect on commitments", verified by "orchestrator", first week "n/a".

It refuses to write a row for a commit that touches the pinned code
(``ffml/``, ``scripts/``, ``pyproject.toml``, ``requirements.lock.txt``): those
need a hand-written row with the Evaluator's verification. It also refuses a
commit that touches a path the pre-registration freezes (``frozen_paths``, or
any path not under ``allowed_paths``): that needs the owner's ruling (D-111),
and a row would not make it valid. Merge commits are left for a hand-written
row too.

Rows are inserted after the last row of the table, so existing rows are never
edited and the change log only gains lines. ``--commit`` commits the change
log alone (``git commit -- <change log>``), so that commit is itself
append-only and exempt (D-115); the helper checks this after committing.

Exit codes: 0 nothing missing (or every missing commit was rowed); 1 commits
remain that need a hand-written row or a ruling; 2 usage or git error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import livetest_core as core  # noqa: E402

CHANGELOG_DOC = "docs/status/livetest-changelog-2026.md"
PINNED_CODE_PREFIXES = ("ffml/", "scripts/")
PINNED_CODE_FILES = ("pyproject.toml", "requirements.lock.txt")
AUTO_VERIFICATION = "paths outside the pinned build; no effect on commitments"
AUTO_VERIFIED_BY = "orchestrator"
AUTO_FIRST_WEEK = "n/a"
MAX_PATHS_IN_ROW = 12
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


class ChangelogError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise ChangelogError(f"git {' '.join(args)} failed: {proc.stderr.strip()[-400:]}")
    return proc


def logged_commits(text: str) -> set[str]:
    """Commit shas (7 to 40 hex) in the ``Commit`` column of the change log's table (the scorer's parser)."""
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


def append_only(repo: Path, sha: str, changelog: str = CHANGELOG_DOC) -> bool:
    """The scorer's exemption: one parent, the change log is the only file, lines added and none deleted."""
    parents = git(repo, "rev-list", "--parents", "-n", "1", sha, check=False)
    if parents.returncode != 0 or len(parents.stdout.split()) != 2:
        return False
    names = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha, check=False)
    files = [f.strip() for f in names.stdout.splitlines() if f.strip()]
    if files != [changelog]:
        return False
    stat = git(repo, "diff-tree", "--no-commit-id", "--numstat", "-r", sha, "--", changelog, check=False)
    rows = [r.split("\t") for r in stat.stdout.splitlines() if r.strip()]
    if len(rows) != 1 or not rows[0][0].isdigit() or not rows[0][1].isdigit():
        return False
    return int(rows[0][0]) > 0 and int(rows[0][1]) == 0


@dataclass
class CommitInfo:
    sha: str
    date: str
    subject: str
    paths: list[str]
    merge: bool
    status: str = ""  # auto | needs_hand_row | needs_ruling | merge
    why: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def classify(info: CommitInfo, allowed: list[str], frozen: list[str]) -> CommitInfo:
    if info.merge:
        info.status, info.why = "merge", ["merge commit: write the row by hand"]
        return info
    blocked = [p for p in info.paths if not core.path_allowed(p, allowed, frozen)]
    pinned = [p for p in info.paths if p.startswith(PINNED_CODE_PREFIXES) or p in PINNED_CODE_FILES]
    if blocked:
        info.status = "needs_ruling"
        info.why = [f"{p}: frozen by the pre-registration (not an allowed path)" for p in blocked[:10]]
    elif pinned:
        info.status = "needs_hand_row"
        info.why = [f"{p}: pinned code; a hand-written row with the Evaluator's verification" for p in pinned[:10]]
    else:
        info.status = "auto"
    return info


def missing_commits(repo: Path, x: str, to: str, logged: set[str], allowed: list[str], frozen: list[str],
                    changelog: str = CHANGELOG_DOC) -> list[CommitInfo]:
    fx = git(repo, "rev-parse", "--verify", f"{x}^{{commit}}").stdout.strip()
    ft = git(repo, "rev-parse", "--verify", f"{to}^{{commit}}").stdout.strip()
    shas = git(repo, "rev-list", "--reverse", f"{fx}..{ft}").stdout.split()
    out = []
    for sha in shas:
        if any(sha.startswith(tok) for tok in logged) or append_only(repo, sha, changelog):
            continue
        meta = git(repo, "show", "-s", "--format=%P%x00%cI%x00%s", sha).stdout.rstrip("\n").split("\x00")
        parents, date, subject = meta[0].split(), meta[1], meta[2]
        merge = len(parents) > 1
        if merge:
            paths = git(repo, "diff", "--name-only", f"{sha}^1", sha).stdout.split("\n")
        else:
            paths = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).stdout.split("\n")
        day = datetime.fromisoformat(date).astimezone(UTC).strftime("%Y-%m-%d")
        info = CommitInfo(sha=sha, date=day, subject=subject, paths=[p.strip() for p in paths if p.strip()], merge=merge)
        out.append(classify(info, allowed, frozen))
    return out


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def row_for(info: CommitInfo) -> str:
    shown = [f"`{p}`" for p in info.paths[:MAX_PATHS_IN_ROW]]
    if len(info.paths) > MAX_PATHS_IN_ROW:
        shown.append(f"(+{len(info.paths) - MAX_PATHS_IN_ROW} more)")
    return (f"| {info.date} | {info.sha[:12]} | {_cell(', '.join(shown))} | {_cell(info.subject)} | "
            f"{AUTO_VERIFICATION} | {AUTO_VERIFIED_BY} | {AUTO_FIRST_WEEK} |")


def insert_rows(text: str, rows: list[str]) -> str:
    """Insert ``rows`` after the last line of the change log's first table; nothing else changes."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("|")), None)
    if start is None:
        raise ChangelogError("the change log has no table")
    end = start
    while end + 1 < len(lines) and lines[end + 1].strip().startswith("|"):
        end += 1
    return "\n".join(lines[: end + 1] + rows + lines[end + 1 :])


def load_policy(repo: Path, prereg: Path | None) -> tuple[list[str], list[str]]:
    path = prereg or repo / "docs" / "status" / "livetest-preregistration-2026.md"
    data = core.load_preregistration(path)
    allowed = list((data.get("allowed_paths") or {}).keys())
    frozen = list(data.get("frozen_paths") or [])
    if not allowed:
        raise ChangelogError(f"{path} has no allowed_paths")
    return allowed, frozen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="list or append live-test change-log rows (D-115, D-120)")
    parser.add_argument("--from", dest="x", required=True, help="the frozen commit X")
    parser.add_argument("--to", default="HEAD")
    parser.add_argument("--repo", type=Path, default=core.REPO_ROOT)
    parser.add_argument("--preregistration", type=Path, default=None)
    parser.add_argument("--append", action="store_true", help="add rows for the commits outside the pinned build")
    parser.add_argument("--commit", action="store_true", help="with --append: commit the change log alone")
    parser.add_argument("--trailer", action="append", default=[], help="commit message trailer line")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    log_path = repo / CHANGELOG_DOC
    try:
        allowed, frozen = load_policy(repo, args.preregistration)
        text = log_path.read_text(encoding="utf-8")
        missing = missing_commits(repo, args.x, args.to, logged_commits(text), allowed, frozen)
    except (ChangelogError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    auto = [m for m in missing if m.status == "auto"]
    rest = [m for m in missing if m.status != "auto"]
    result: dict = {"from": args.x, "to": args.to, "missing": [m.to_dict() for m in missing], "appended": [],
                    "commit": None}
    if args.append and auto:
        if args.commit:
            staged = git(repo, "diff", "--cached", "--name-only").stdout.split()
            if staged:
                print(f"error: files are already staged ({staged[:5]}); --commit commits the change log alone",
                      file=sys.stderr)
                return 2
        log_path.write_text(insert_rows(text, [row_for(m) for m in auto]), encoding="utf-8", newline="\n")
        result["appended"] = [m.sha for m in auto]
        if args.commit:
            message = (f"Live-test change log: rows for {len(auto)} commit(s) outside the pinned build\n\n"
                       + "\n".join(f"- {m.sha[:12]} {m.subject}" for m in auto) + "\n"
                       + ("\n" + "\n".join(args.trailer) + "\n" if args.trailer else ""))
            git(repo, "add", "--", CHANGELOG_DOC)
            git(repo, "commit", "-q", "-m", message, "--", CHANGELOG_DOC)
            sha = git(repo, "rev-parse", "HEAD").stdout.strip()
            result["commit"] = {"sha": sha, "append_only_exempt": append_only(repo, sha)}
            if not result["commit"]["append_only_exempt"]:
                print("error: the change-log commit is not append-only; it is not exempt", file=sys.stderr)
                return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for m in missing:
            mark = "row added" if m.sha in result["appended"] else m.status
            print(f"{m.sha[:12]}  {m.date}  {mark:<15} {m.subject}")
            for why in m.why:
                print(f"    {why}")
        if not missing:
            print(f"every commit in {args.x}..{args.to} has a change-log entry")
        if result["commit"]:
            print(f"committed {result['commit']['sha'][:12]} (change log only, append-only: exempt)")
    remaining = [m for m in rest] + ([m for m in auto] if not args.append else [])
    return 1 if remaining else 0


if __name__ == "__main__":
    sys.exit(main())
