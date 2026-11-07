"""Create the code worktree pinned at Y, with its own venv (D-120).

    python scripts/livetest_setup_worktree.py --commit <Y> [--frozen-commit <X>] [--path ../ffml-livetest-code]
        [--base-python "py -3.11"] [--dry-run]

Y is the append-only change-log commit made after the commit that fills the
pre-registration's ``commit`` field with X; it is not X itself (D-120, the
Evaluator's F1). The worktree must sit at Y so that its copy of the
pre-registration carries the filled commit. After the pin check, the script
verifies that Y is X or a descendant of X whose diff from X touches only
allowed paths; X comes from ``--frozen-commit`` or the main checkout's
pre-registration. The final always runs from the main checkout, never from
this worktree.

Steps (each is skipped when already done, so the script can be re-run):

1. resolve ``--commit`` in this repository (fetching ``origin`` if needed);
2. ``git worktree add --detach <path> <commit>``; an existing worktree must
   already be at that commit (a pinned worktree is never moved);
3. ``<base python> -m venv <path>/.venv`` (Python 3.11; ``.venv/`` is ignored
   by git, so the worktree stays clean);
4. ``pip install -r <path>/requirements.lock.txt`` (the lock of the pinned
   commit) and ``pip install --no-deps -e <path>``;
5. verify with the same check the runner uses (HEAD, clean tree, ``ffml``
   imported from the worktree, ``pip freeze`` equal to the lock).

The worktree shares data and artifacts with the main checkout through
``FFML_DATA_DIR``, ``FFML_ARTIFACTS_DIR`` and ``FFML_REPORTS_DIR``, which the
runner sets for every subprocess. Nothing here writes to ``data/``.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import livetest_core as core  # noqa: E402


def sh(cmd: list[str], *, cwd: Path | None = None, dry: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    print("+ " + " ".join(shlex.quote(str(c)) for c in cmd), flush=True)
    if dry:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, text=True,
                          capture_output=True, encoding="utf-8", errors="replace",
                          env=core.clean_child_env())
    if proc.stdout.strip():
        print(proc.stdout.strip()[-3000:])
    if check and proc.returncode != 0:
        print(proc.stderr.strip()[-3000:], file=sys.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {cmd[:4]}")
    return proc


def default_base_python() -> list[str]:
    if shutil.which("py"):
        return ["py", "-3.11"]
    return [getattr(sys, "_base_executable", sys.executable)]


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", required=True,
                        help="Y: the append-only change-log commit after the fill commit (D-120), not X")
    parser.add_argument("--frozen-commit", default=None,
                        help="X (default: the commit field of the main checkout's pre-registration)")
    parser.add_argument("--path", type=Path, default=core.DEFAULT_CODE_WORKTREE)
    parser.add_argument("--repo", type=Path, default=core.REPO_ROOT, help="the main checkout")
    parser.add_argument("--base-python", default=None, help='interpreter for the venv, e.g. "py -3.11"')
    parser.add_argument("--dry-run", action="store_true", help="print the commands only")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    wt = args.path.resolve()
    dry = args.dry_run
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{args.commit}^{{commit}}"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sh(["git", "-C", repo, "fetch", "--quiet", "origin"], dry=dry, check=False)
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                               f"{args.commit}^{{commit}}"], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"commit {args.commit} not found in {repo}")
    sha = proc.stdout.strip()
    print(f"pinned commit: {sha}")

    if wt.exists():
        head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if head != sha:
            raise SystemExit(
                f"{wt} exists at {head or 'no commit'}, not {sha}. A pinned worktree is never moved; "
                f"remove it with `git worktree remove {wt}` first if the pre-registration changed."
            )
        print(f"worktree exists at {sha}")
    else:
        sh(["git", "-C", repo, "worktree", "add", "--detach", wt, sha], dry=dry)

    venv = wt / ".venv"
    py = venv_python(venv)
    if not py.exists():
        base = shlex.split(args.base_python) if args.base_python else default_base_python()
        sh([*base, "-m", "venv", venv], dry=dry)
    else:
        print(f"venv exists: {py}")
    version = sh([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], dry=dry)
    if not dry and version.stdout.strip() != "3.11":
        raise SystemExit(f"venv python is {version.stdout.strip()}, expected 3.11 (D-001)")
    sh([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r",
        wt / "requirements.lock.txt"], dry=dry)
    sh([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps", "-e", wt], dry=dry)
    if dry:
        print("dry run: nothing was created")
        return 0
    result = core.verify_code_pin(wt, sha, py, main_repo=repo, check_env=True)
    print(json.dumps(result.to_dict(), indent=2))
    prereg = core.load_preregistration(repo / core.PREREGISTRATION_DOC)
    x = args.frozen_commit or core.frozen_commit_from(prereg)
    if x is None:
        print("WARNING: the pre-registration's commit field is not filled; the descendant check (X..Y) is skipped")
    else:
        relation = core.verify_descends_from_frozen(
            wt, x, sha, (prereg.get("allowed_paths") or {}).keys(), prereg.get("frozen_paths") or [])
        print(json.dumps(relation, indent=2))
    print("\nRecord the pin for the runner (once the test design is signed off):")
    print(f'  .venv\\Scripts\\python scripts\\livetest_week.py init --season 2026 --weeks 10-18 '
          f'--code-worktree "{wt}" --python "{py}" --expected-commit {sha}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
