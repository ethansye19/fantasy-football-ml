"""Run the evaluator audits: ``python -m ffml.eval.audits [--write NAME] [--release] [--no-suite]``.

Prints the markdown report; ``--write NAME`` also writes ``reports/NAME.md``.
Exit status is 1 when any audit fails.
"""

from __future__ import annotations

import argparse
import sys

from ffml.config import ensure_utf8
from ffml.eval.audits.holdout_audit import audit_holdout
from ffml.eval.audits.inputs_audit import audit_inputs
from ffml.eval.audits.leakage_audit import audit_leakage
from ffml.eval.audits.report import write_report


def main(argv: list[str] | None = None) -> int:
    ensure_utf8()
    parser = argparse.ArgumentParser(prog="python -m ffml.eval.audits", description=__doc__)
    parser.add_argument("--write", metavar="NAME", help="write reports/NAME.md")
    parser.add_argument("--release", action="store_true", help="allow exactly one holdout_eval session")
    parser.add_argument("--no-suite", action="store_true", help="skip running the leakage test suite")
    args = parser.parse_args(argv)
    reports = [
        audit_holdout(release_run_allowed=args.release),
        audit_leakage(run_suite=not args.no_suite),
        audit_inputs(),
    ]
    title = "Evaluator audit"
    if args.write:
        path = write_report(args.write, reports, title=title)
        print(f"wrote {path}")
    for r in reports:
        print(r.to_markdown(heading_level=2))
    return 1 if any(r.status == "FAIL" for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
