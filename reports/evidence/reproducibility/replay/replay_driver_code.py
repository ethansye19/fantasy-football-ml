"""Run replay_driver.py with ffml imported from spec["code_path"] (a worktree at the recorded code commit).

The interpreter runs isolated (-I), so nothing else is on sys.path; the editable finder of the pinned venv
is a meta-path finder appended after the standard path finder, so the worktree put first here wins.
"""
import json
import runpy
import sys
from pathlib import Path

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path.insert(0, spec["code_path"])
runpy.run_path(str(Path(__file__).with_name("replay_driver.py")), run_name="__main__")
