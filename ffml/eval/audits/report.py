"""Audit report container and markdown writer (Independent Evaluator).

Reports hold aggregates only: statuses, counts, file paths and line numbers.
They never contain restricted consensus rows (D-007) or feature rows.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ffml.config import PROJECT_ROOT, get_settings

Status = Literal["PASS", "FAIL", "WARN", "SKIP"]
STATUSES: tuple[str, ...] = ("PASS", "FAIL", "WARN", "SKIP")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAX_EVIDENCE = 50


def git_sha(root: Path = PROJECT_ROOT) -> str:
    """Short HEAD sha of ``root`` or ``"unknown"``."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@dataclass
class AuditItem:
    """One check: a status, a one-line detail, and optional evidence lines."""

    name: str
    status: Status
    detail: str
    evidence: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {self.status!r}")


@dataclass
class AuditReport:
    """A titled list of :class:`AuditItem` with an overall status.

    Overall status: FAIL if any item fails; SKIP if every item was skipped
    (or there are none); otherwise PASS (warnings are listed but do not fail).
    """

    title: str
    items: list[AuditItem] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).replace(microsecond=0).isoformat()
    )
    git: str = field(default_factory=git_sha)

    def add(self, name: str, status: Status, detail: str, evidence: list[str] | None = None) -> AuditItem:
        item = AuditItem(name, status, detail, list(evidence or []))
        self.items.append(item)
        return item

    @property
    def status(self) -> Status:
        statuses = {i.status for i in self.items}
        if "FAIL" in statuses:
            return "FAIL"
        if not statuses or statuses == {"SKIP"}:
            return "SKIP"
        return "PASS"

    def counts(self) -> dict[str, int]:
        return {s: sum(1 for i in self.items if i.status == s) for s in STATUSES}

    def findings(self) -> list[AuditItem]:
        """Items that need attention (FAIL and WARN)."""
        return [i for i in self.items if i.status in ("FAIL", "WARN")]

    def to_markdown(self, heading_level: int = 1) -> str:
        h = "#" * max(1, heading_level)
        counts = ", ".join(f"{k} {v}" for k, v in self.counts().items() if v)
        lines = [
            f"{h} {self.title}",
            "",
            f"Overall: **{self.status}** ({counts or 'no checks'}). "
            f"Run at {self.created_at} on the working tree at git `{self.git}`.",
            "",
            "| check | status | detail |",
            "| --- | --- | --- |",
        ]
        for item in self.items:
            detail = item.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {item.name} | {item.status} | {detail} |")
        with_evidence = [i for i in self.items if i.evidence]
        for item in with_evidence:
            lines += ["", f"{h}# {item.name} ({item.status})", ""]
            shown = item.evidence[:_MAX_EVIDENCE]
            lines += [f"- {e}" for e in shown]
            if len(item.evidence) > _MAX_EVIDENCE:
                lines.append(f"- ... and {len(item.evidence) - _MAX_EVIDENCE} more")
        return "\n".join(lines) + "\n"

    def write_report(self, name: str, reports_dir: Path | None = None) -> Path:
        """Write this report to ``<reports_dir>/<name>.md`` (default ``reports/``)."""
        return write_report(name, [self], reports_dir=reports_dir)


def combined_status(reports: list[AuditReport]) -> str:
    """FAIL if any check failed, INCOMPLETE if any check was skipped, else PASS."""
    statuses = {i.status for r in reports for i in r.items}
    if "FAIL" in statuses:
        return "FAIL"
    if "SKIP" in statuses or not statuses:
        return "INCOMPLETE"
    return "PASS"


def _report_path(name: str, reports_dir: Path | None) -> Path:
    if not _NAME_RE.match(name) or ".." in name:
        raise ValueError(f"report name must be a plain file name, got {name!r}")
    base = Path(reports_dir) if reports_dir is not None else get_settings().reports_dir
    filename = name if name.endswith(".md") else f"{name}.md"
    path = (base / filename).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"report path escapes {base}")
    return path


def write_report(name: str, reports: list[AuditReport], reports_dir: Path | None = None, title: str | None = None) -> Path:
    """Write one or more reports into a single markdown file under ``reports/``.

    Returns the path written. With several reports a combined header lists
    each report's overall status.
    """
    if not reports:
        raise ValueError("no reports to write")
    path = _report_path(name, reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(reports) == 1 and title is None:
        text = reports[0].to_markdown()
    else:
        overall = combined_status(reports)
        lines = [f"# {title or 'Evaluator audit'}", "", f"Overall: **{overall}**", ""]
        if overall == "INCOMPLETE":
            lines += ["Some checks were skipped because their inputs do not exist yet; "
                      "a gate needs every check run.", ""]
        lines += [f"- {r.title}: **{r.status}**" for r in reports]
        text = "\n".join(lines) + "\n\n" + "\n".join(r.to_markdown(heading_level=2) for r in reports)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


__all__ = ["AuditItem", "AuditReport", "STATUSES", "Status", "combined_status", "git_sha", "write_report"]
