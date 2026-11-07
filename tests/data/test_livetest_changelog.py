"""Tests for scripts/livetest_changelog.py (D-115, D-120) on a temporary repository."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import livetest_changelog as cl  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

CHANGELOG = """# Live-test change log

One entry per commit, newest last:

| Date (UTC) | Commit | Paths | Reason | Verification | Verified by | First week that uses it |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |

Verification for `ffml/data/` entries: byte-identical feature rows.
"""

PREREG = """# Pre-registration

```json
{
  "commit": "<frozen commit>",
  "frozen_paths": ["ffml/models/", "pyproject.toml", "tests/fixtures/features/feature_manifest_0.2.0.json"],
  "allowed_paths": {"ffml/data/": "", "scripts/": "", "tests/": "", "docs/": "", "web/": "", "api/": "",
                    "reports/": "", "README.md": "", ".github/": ""}
}
```
"""


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        git("add", rel, cwd=repo)
    git("commit", "--quiet", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for name, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@e"}.items():
        monkeypatch.setenv(name, value)
    r = tmp_path / "repo"
    r.mkdir()
    git("init", "--quiet", "-b", "main", cwd=r)
    git("config", "core.autocrlf", "false", cwd=r)
    x = commit(r, "X: the frozen commit", {cl.CHANGELOG_DOC: CHANGELOG,
                                           "docs/status/livetest-preregistration-2026.md": PREREG,
                                           "ffml/models/m.py": "A = 1\n"})
    shas = {
        "web": commit(r, "UI: a | piped subject", {"web/app/page.tsx": "x\n"}),
        "docs": commit(r, "Docs and a report", {"docs/RUNBOOK.md": "r\n", "reports/r.md": "r\n"}),
        "data": commit(r, "Ingest fix", {"ffml/data/ingest.py": "x\n"}),
        "script": commit(r, "Runner tweak", {"scripts/livetest_week.py": "x\n", "docs/x.md": "x\n"}),
        "frozen": commit(r, "Manifest edit", {"tests/fixtures/features/feature_manifest_0.2.0.json": "{}\n"}),
        "unlisted": commit(r, "Prompt edit", {"PROMPT_1.md": "p\n"}),
    }
    text = CHANGELOG.replace(
        "| | | | | | | |\n", "| | | | | | | |\n| 2026-11-07 | " + shas["web"][:9] + " | web | hand row | ok | me | n/a |\n")
    shas["hand_row"] = commit(r, "Change log: a hand-written row", {cl.CHANGELOG_DOC: text})
    return r, x, shas


def test_lists_missing_commits_and_classifies_them(repo, capsys):
    r, x, shas = repo
    allowed, frozen = cl.load_policy(r, None)
    missing = {m.sha: m for m in cl.missing_commits(r, x, "HEAD", cl.logged_commits(
        (r / cl.CHANGELOG_DOC).read_text(encoding="utf-8")), allowed, frozen)}
    assert shas["web"] not in missing  # has a hand-written row
    assert shas["hand_row"] not in missing  # append-only change-log commit: exempt
    assert missing[shas["docs"]].status == "auto"
    assert missing[shas["data"]].status == "needs_hand_row" and missing[shas["script"]].status == "needs_hand_row"
    assert missing[shas["frozen"]].status == "needs_ruling" and missing[shas["unlisted"]].status == "needs_ruling"
    assert cl.main(["--from", x, "--repo", str(r)]) == 1
    out = capsys.readouterr().out
    assert "needs_hand_row" in out and "pinned code" in out and "frozen by the pre-registration" in out


def test_append_adds_rows_only_for_commits_outside_the_pinned_build(repo, capsys):
    r, x, shas = repo
    log = r / cl.CHANGELOG_DOC
    before = log.read_text(encoding="utf-8").splitlines()
    assert cl.main(["--from", x, "--repo", str(r), "--append", "--commit", "--json", "--trailer", "Co-Authored-By: t"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["appended"] == [shas["docs"]]
    assert result["commit"]["append_only_exempt"] is True
    after = log.read_text(encoding="utf-8").splitlines()
    it = iter(after)
    assert all(line in it for line in before)  # every original line kept, in order: nothing edited
    row = next(line for line in after if shas["docs"][:12] in line)
    assert row == (f"| {result['missing'][0]['date']} | {shas['docs'][:12]} | `docs/RUNBOOK.md`, `reports/r.md` | "
                   f"Docs and a report | {cl.AUTO_VERIFICATION} | orchestrator | n/a |")
    assert after.index(row) < after.index("Verification for `ffml/data/` entries: byte-identical feature rows.")
    head = git("rev-parse", "HEAD", cwd=r)
    assert git("diff-tree", "--no-commit-id", "--name-only", "-r", head, cwd=r) == cl.CHANGELOG_DOC
    assert "Co-Authored-By: t" in git("log", "-1", "--format=%B", cwd=r)
    # second run: the rowed commit and the helper's own commit are no longer missing
    allowed, frozen = cl.load_policy(r, None)
    left = {m.sha for m in cl.missing_commits(r, x, "HEAD", cl.logged_commits(log.read_text(encoding="utf-8")),
                                             allowed, frozen)}
    assert left == {shas["data"], shas["script"], shas["frozen"], shas["unlisted"]}


def test_pipe_in_subject_is_escaped_and_staged_files_refuse_commit(repo):
    r, x, shas = repo
    info = cl.CommitInfo(sha="a" * 40, date="2026-11-07", subject="a | b", paths=["web/x"], merge=False)
    assert "| a / b |" in cl.row_for(info)
    (r / "README.md").write_text("staged\n")
    git("add", "README.md", cwd=r)
    assert cl.main(["--from", x, "--repo", str(r), "--append", "--commit"]) == 2


def test_parser_and_exemption_match_the_scorer(repo):
    score = pytest.importorskip("ffml.eval.livetest_score")
    r, x, shas = repo
    text = (r / cl.CHANGELOG_DOC).read_text(encoding="utf-8")
    assert cl.logged_commits(text) == score.changelog_commits(text)
    for sha in list(shas.values()) + [x]:
        assert cl.append_only(r, sha) == score._changelog_append_only(sha, r), sha
