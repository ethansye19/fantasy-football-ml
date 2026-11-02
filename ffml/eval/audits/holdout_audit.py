"""Holdout audit (EV-3.2, Gate 3 "holdout touched exactly once").

Checks, against the repository at ``root``:

1. The guard's access log ``<artifacts>/holdout_access.log`` (written by
   :func:`ffml.holdout.assert_seasons_allowed` when the unlock flag is set).
   Every entry is listed with its purpose, seasons and git sha. Before the
   release run any entry fails the audit; with ``release_run_allowed`` the
   log must hold exactly one access session, all with purpose
   ``holdout_eval``. The guard writes one line per call, so lines with the
   same purpose, git sha and user less than ``session_gap_hours`` apart are
   grouped into one session.
2. Literal holdout-season numbers in ``ffml/features``, ``ffml/models``,
   ``ffml/sim``, ``ffml/scoring`` and ``ffml/eval`` (the holdout season comes
   from settings, never from code): numeric or plain string literals fail;
   docstrings and comments warn.
3. Assignments of the unlock setting or environment variable anywhere
   outside the tests and the guard itself (``ffml/holdout.py`` and the
   setting's definition in ``ffml/config.py``), found from the Python syntax
   tree plus a text scan of scripts and workflow files.
4. The ``.env`` file, if present, must not set the unlock flag, and the
   audit's own environment must not have it set.
"""

from __future__ import annotations

import ast
import io
import os
import re
import tokenize
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ffml.config import PROJECT_ROOT, get_settings
from ffml.eval.audits.report import AuditReport

# Built by concatenation so this module's own source never matches its patterns.
UNLOCK_ENV = "FFML_HOLDOUT_" + "UNLOCK"
UNLOCK_ATTR = "holdout_" + "unlock"
LOG_NAME = "holdout_access.log"
LITERAL_DIRS: tuple[str, ...] = (
    "ffml/features", "ffml/models", "ffml/sim", "ffml/scoring", "ffml/eval",
)
UNLOCK_PY_DIRS: tuple[str, ...] = ("ffml", "scripts", "api")
UNLOCK_TEXT_DIRS: tuple[str, ...] = ("scripts", ".github")
UNLOCK_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".sh", ".ps1", ".bat", ".cmd", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env", ".txt", ""}
)
#: files that may assign the unlock: the guard and the setting's definition
GUARD_FILES: frozenset[str] = frozenset({"ffml/holdout.py", "ffml/config.py"})
_TRUE = {"1", "true", "t", "yes", "y", "on"}
_ENV_SETTERS = {"setenv", "putenv", "setdefault", "update", "__setitem__"}
_TEXT_UNLOCK_RE = re.compile(re.escape(UNLOCK_ENV) + r"""['"]?\s*[:=]""")
_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".git", ".pytest_cache", ".ruff_cache"}


# --------------------------------------------------------------------------- #
# Access log
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HoldoutAccess:
    """One line of the guard's access log."""

    timestamp: datetime
    purpose: str
    seasons: str
    git: str
    user: str
    line_no: int


def parse_access_log(path: Path) -> tuple[list[HoldoutAccess], list[int]]:
    """Parse the guard's tab-separated log; returns entries and malformed line numbers."""
    entries: list[HoldoutAccess] = []
    bad: list[int] = []
    if not path.exists():
        return entries, bad
    for no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        try:
            ts = datetime.fromisoformat(parts[0])
        except ValueError:
            bad.append(no)
            continue
        if "purpose" not in fields or "seasons" not in fields:
            bad.append(no)
            continue
        entries.append(
            HoldoutAccess(
                timestamp=ts,
                purpose=fields["purpose"],
                seasons=fields["seasons"],
                git=fields.get("git", "unknown"),
                user=fields.get("user", "?"),
                line_no=no,
            )
        )
    return entries, bad


def access_sessions(entries: Iterable[HoldoutAccess], gap_hours: float = 2.0) -> list[list[HoldoutAccess]]:
    """Group log lines into sessions (same purpose, git sha and user, close in time)."""
    sessions: list[list[HoldoutAccess]] = []
    for entry in sorted(entries, key=lambda e: e.timestamp):
        last = sessions[-1][-1] if sessions else None
        if (
            last is not None
            and (entry.purpose, entry.git, entry.user) == (last.purpose, last.git, last.user)
            and entry.timestamp - last.timestamp <= timedelta(hours=gap_hours)
        ):
            sessions[-1].append(entry)
        else:
            sessions.append([entry])
    return sessions


def _check_log(report: AuditReport, log_path: Path, release_run_allowed: bool, gap_hours: float, root: Path) -> None:
    rel = _rel(log_path, root)
    if not log_path.exists():
        report.add("holdout access log", "PASS", f"{rel} absent: no holdout access recorded")
        return
    entries, bad = parse_access_log(log_path)
    sessions = access_sessions(entries, gap_hours)
    evidence = [
        f"line {e.line_no}: {e.timestamp.isoformat()} purpose={e.purpose} seasons={e.seasons} "
        f"git={e.git} user={e.user}"
        for e in entries
    ]
    evidence += [f"line {n}: malformed entry" for n in bad]
    summary = f"{rel}: {len(entries)} access line(s) in {len(sessions)} session(s)"
    if bad:
        report.add("holdout access log", "FAIL", f"{summary}; {len(bad)} malformed line(s)", evidence)
        return
    if not entries:
        report.add("holdout access log", "PASS", f"{rel} present but empty", evidence)
        return
    if any(e.git == "unknown" for e in entries):
        report.add("holdout access provenance", "WARN", "some access lines have no git sha")
    if not release_run_allowed:
        report.add(
            "holdout access log", "FAIL",
            f"{summary}; the holdout must stay untouched before the evaluator's release run",
            evidence,
        )
        return
    purposes = {e.purpose for e in entries}
    if len(sessions) == 1 and purposes == {"holdout_eval"}:
        report.add("holdout access log", "PASS", f"{summary}; one release session (holdout_eval)", evidence)
    else:
        report.add(
            "holdout access log", "FAIL",
            f"{summary}; expected exactly one session with purpose holdout_eval, purposes {sorted(purposes)}",
            evidence,
        )


# --------------------------------------------------------------------------- #
# Source scans
# --------------------------------------------------------------------------- #


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_files(root: Path, dirs: Iterable[str], suffixes: Iterable[str] | None = None) -> list[Path]:
    wanted = set(suffixes) if suffixes is not None else None
    out: list[Path] = []
    for d in dirs:
        base = root / d
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            if wanted is not None and path.suffix not in wanted:
                continue
            out.append(path)
    return out


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    return parts[0] == "tests" or "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def scan_literals(path: Path, value: int) -> list[tuple[int, str]]:
    """``(line, kind)`` for each occurrence of ``value``; kind is code, string, docstring or comment."""
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(?<!\d){value}(?!\d)")
    found: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [(exc.lineno or 0, "unparseable")]
    docstrings = _docstring_ids(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, bool):
            continue
        if isinstance(node.value, int | float) and node.value == value:
            found.append((node.lineno, "code"))
        elif isinstance(node.value, str):
            kind = "docstring" if id(node) in docstrings else "string"
            for match in pattern.finditer(node.value):
                # line of the mention inside a multi-line string (exact unless escapes span lines)
                found.append((node.lineno + node.value.count(chr(10), 0, match.start()), kind))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and pattern.search(tok.string):
                found.append((tok.start[0], "comment"))
    except (tokenize.TokenError, SyntaxError):
        pass
    return sorted(set(found))


def _contains_flag(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Constant) and n.value in (UNLOCK_ENV, UNLOCK_ATTR) for n in ast.walk(node)
    )


def _target_is_unlock(target: ast.AST) -> bool:
    if isinstance(target, ast.Name):
        return target.id == UNLOCK_ATTR
    if isinstance(target, ast.Attribute):
        return target.attr == UNLOCK_ATTR
    if isinstance(target, ast.Subscript):
        return _contains_flag(target.slice)
    if isinstance(target, ast.Tuple | ast.List):
        return any(_target_is_unlock(t) for t in target.elts)
    return False


def scan_unlock_assignments(path: Path) -> list[tuple[int, str]]:
    """``(line, description)`` for every assignment of the unlock setting or variable."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [(exc.lineno or 0, "unparseable file")]
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(_target_is_unlock(t) for t in node.targets):
                found.append((node.lineno, "assignment"))
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            if _target_is_unlock(node.target):
                found.append((node.lineno, "assignment"))
        elif isinstance(node, ast.keyword) and node.arg == UNLOCK_ATTR:
            found.append((getattr(node, "lineno", 0) or 0, "keyword argument"))
        elif isinstance(node, ast.Dict):
            if any(k is not None and _contains_flag(k) for k in node.keys):
                found.append((node.lineno, "mapping key"))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            args = [*node.args, *[k.value for k in node.keywords]]
            if name in _ENV_SETTERS | {"setattr"} and any(_contains_flag(a) for a in args):
                found.append((node.lineno, f"{name}() call"))
    return sorted(set(found))


def scan_text_unlock(path: Path) -> list[int]:
    """Line numbers in a non-Python file that set the unlock variable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return [no for no, line in enumerate(text.splitlines(), start=1) if _TEXT_UNLOCK_RE.search(line)]


def _check_literals(report: AuditReport, root: Path, value: int) -> None:
    fails: list[str] = []
    warns: list[str] = []
    files = [p for p in _iter_files(root, LITERAL_DIRS, {".py"}) if not _is_test_path(_rel(p, root))]
    for path in files:
        rel = _rel(path, root)
        for line, kind in scan_literals(path, value):
            entry = f"{rel}:{line} ({kind})"
            (warns if kind in ("docstring", "comment") else fails).append(entry)
    scope = f"{len(files)} files under {', '.join(LITERAL_DIRS)}"
    if fails:
        report.add(
            "literal holdout season", "FAIL",
            f"{len(fails)} literal {value} in code ({scope}); use settings.holdout_season",
            fails + warns,
        )
    elif warns:
        report.add(
            "literal holdout season", "WARN",
            f"{value} appears only in docstrings or comments ({len(warns)}; {scope})", warns,
        )
    else:
        report.add("literal holdout season", "PASS", f"no literal {value} ({scope})")


def _check_unlock_assignments(report: AuditReport, root: Path) -> None:
    findings: list[str] = []
    py_files = _iter_files(root, UNLOCK_PY_DIRS, {".py"})
    py_files += [p for p in sorted(root.glob("*.py")) if p.is_file()]
    checked = 0
    for path in py_files:
        rel = _rel(path, root)
        if rel in GUARD_FILES or _is_test_path(rel):
            continue
        checked += 1
        findings += [f"{rel}:{line} {what}" for line, what in scan_unlock_assignments(path)]
    text_files = [
        p for p in _iter_files(root, UNLOCK_TEXT_DIRS) if p.suffix in UNLOCK_TEXT_SUFFIXES
    ]
    text_files += [
        p for p in sorted(root.iterdir())
        if p.is_file() and p.suffix in UNLOCK_TEXT_SUFFIXES - {""} and not p.name.startswith(".env")
    ]
    for path in text_files:
        rel = _rel(path, root)
        if _is_test_path(rel):
            continue
        checked += 1
        findings += [f"{rel}:{line} sets {UNLOCK_ENV}" for line in scan_text_unlock(path)]
    if findings:
        report.add(
            "unlock assignments", "FAIL",
            f"{len(findings)} assignment(s) of the holdout unlock outside tests and the guard",
            findings,
        )
    else:
        report.add(
            "unlock assignments", "PASS",
            f"none outside tests and the guard ({checked} files scanned)",
        )


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal ``.env`` parser (``KEY=VALUE``, optional ``export``, quotes stripped)."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip("'\"")
        values[key.strip()] = value
    return values


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUE


def _check_env(report: AuditReport, root: Path, environ: Mapping[str, str]) -> None:
    env_path = root / ".env"
    if env_path.exists():
        values = {k.upper(): v for k, v in parse_env_file(env_path).items()}
        if UNLOCK_ENV in values and _truthy(values[UNLOCK_ENV]):
            report.add(".env unlock", "FAIL", f".env sets {UNLOCK_ENV}={values[UNLOCK_ENV]}")
        elif UNLOCK_ENV in values:
            report.add(".env unlock", "WARN", f".env mentions {UNLOCK_ENV} with a false value; remove it")
        else:
            report.add(".env unlock", "PASS", f".env present and does not set {UNLOCK_ENV}")
    else:
        report.add(".env unlock", "PASS", ".env absent")
    if _truthy(environ.get(UNLOCK_ENV)):
        report.add("process environment", "FAIL", f"{UNLOCK_ENV} is set in the audit's environment")
    else:
        report.add("process environment", "PASS", f"{UNLOCK_ENV} not set")


def audit_holdout(
    root: Path = PROJECT_ROOT,
    *,
    artifacts_dir: Path | None = None,
    release_run_allowed: bool = False,
    session_gap_hours: float = 2.0,
    holdout_season: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> AuditReport:
    """Run the holdout audit on the repository at ``root`` (see module docstring)."""
    root = Path(root)
    settings = get_settings()
    season = int(holdout_season if holdout_season is not None else settings.holdout_season)
    if artifacts_dir is None:
        artifacts_dir = settings.artifacts_dir if root.resolve() == PROJECT_ROOT.resolve() else root / "artifacts"
    report = AuditReport(title=f"Holdout audit (season {season})")
    _check_log(report, Path(artifacts_dir) / LOG_NAME, release_run_allowed, session_gap_hours, root)
    _check_literals(report, root, season)
    _check_unlock_assignments(report, root)
    _check_env(report, root, os.environ if environ is None else environ)
    return report


__all__ = [
    "GUARD_FILES",
    "HoldoutAccess",
    "LITERAL_DIRS",
    "access_sessions",
    "audit_holdout",
    "parse_access_log",
    "parse_env_file",
    "scan_literals",
    "scan_text_unlock",
    "scan_unlock_assignments",
]
