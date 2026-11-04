"""Approval gate and prediction-source resolution (D-097, D-098, D-099; docs/API.md section 3).

Projection-based features (weekly projections, start/sit, waivers, trades,
rest-of-season rankings) are switched off in the real app until a model passes
its pre-registered test.

production
    Serve only when **all** hold:

    1. the model registry has a live version (``ffml.models.registry.get_live``)
       with an evaluator report;
    2. ``data/predictions/LATEST`` points at a readable run whose manifest has
       ``approval_status == "approved"`` (top level or under ``extra``) and
       ``environment == "live"`` (the prediction mode; a missing key is accepted
       for older manifests), not ``"private_test"`` or ``"replay"``;
    3. that manifest's ``model_version`` equals the live version.

    Anything missing, unreadable or malformed means no serving. Nothing else
    (private store, replays, fixtures) is ever consulted.

private_test
    Serve, in this order: the private-test store's ``LATEST``
    (``Settings.private_predictions_dir``), a replay run (pinned with
    ``FFML_API_REPLAY_RUN`` or the newest replay in the public or private
    store), a fixture store (``FFML_API_FIXTURE_PREDICTIONS_DIR``). Every
    result is labeled "not approved", whatever a manifest says.

Manifests are read defensively: a missing ``approval_status`` counts as not
approved.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.settings import PRIVATE_TEST, PRODUCTION, ApiSettings

log = logging.getLogger(__name__)

APPROVED = "approved"
NOT_APPROVED = "not approved"
HEADER = "X-FFML-Approval"
HEADER_APPROVED = "approved"
HEADER_NOT_APPROVED = "not-approved"
HEADER_NONE = "none"

NO_APPROVED_MODEL_STATUS = "no_approved_model"
NO_APPROVED_MODEL_MESSAGE = (
    "No approved model. Projections are off until a model passes its pre-registered test."
)
NO_PRIVATE_SNAPSHOT_STATUS = "no_private_test_snapshot"
NO_PRIVATE_SNAPSHOT_MESSAGE = (
    "Private test environment: no private-test, replay or fixture predictions snapshot was "
    "found. Nothing is served."
)
NOT_APPROVED_NOTICE = (
    "Not approved: these numbers come from a model that has not passed its pre-registered "
    "test. Private test environment only; not a recommendation."
)

MANIFEST_NAME = "_manifest.json"
LATEST_NAME = "LATEST"
SIMS_FILE = "player_sims.parquet"
SUMMARY_FILE = "player_summary.parquet"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Manifest ``environment`` (prediction mode) of an approved live run.
LIVE_ENVIRONMENT = "live"

SOURCE_PUBLISHED = "published"
SOURCE_PRIVATE = "private_test"
SOURCE_REPLAY = "replay"
SOURCE_FIXTURE = "fixture"


class SnapshotError(ValueError):
    """A predictions pointer or manifest is missing or malformed."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PredictionSource:
    """One readable prediction run and where it came from."""

    kind: str
    root: Path
    path: Path
    season: int
    week: int
    run_id: str
    model_version: str
    manifest: dict[str, Any]

    @property
    def key(self) -> str:
        """Cache key: the run directory plus the manifest write time."""
        return f"{self.path.as_posix()}@{self.manifest.get('written_at', '')}"

    @property
    def mode(self) -> str | None:
        return manifest_field(self.manifest, "mode")

    @property
    def is_replay(self) -> bool:
        return self.mode == "replay" or self.run_id.endswith("-replay")

    def has_file(self, name: str) -> bool:
        return (self.path / name).is_file()

    def describe(self) -> dict[str, Any]:
        files = self.manifest.get("files")
        return {
            "source": self.kind,
            "run_id": self.run_id,
            "season": self.season,
            "week": self.week,
            "model_version": self.model_version,
            "written_at": self.manifest.get("written_at"),
            "n_sims": self.manifest.get("n_sims"),
            "is_replay": self.is_replay,
            "has_rest_of_season": isinstance(files, dict) and "ros_sims.parquet" in files,
        }


@dataclass(frozen=True)
class ApprovalState:
    """Result of one gate evaluation."""

    environment: str
    serving: bool
    approval_status: str
    reason: str
    message: str
    source: PredictionSource | None = None
    live_model_version: str | None = None
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def label(self) -> str:
        return self.approval_status

    @property
    def header_value(self) -> str:
        if not self.serving:
            return HEADER_NONE
        return HEADER_APPROVED if self.approval_status == APPROVED else HEADER_NOT_APPROVED

    def blocked_body(self) -> dict[str, Any]:
        """The HTTP 503 body of a projection route that cannot serve."""
        if self.environment == PRIVATE_TEST:
            return {
                "status": NO_PRIVATE_SNAPSHOT_STATUS,
                "message": NO_PRIVATE_SNAPSHOT_MESSAGE,
                "approval_status": NOT_APPROVED,
                "label": NOT_APPROVED,
            }
        return {"status": NO_APPROVED_MODEL_STATUS, "message": NO_APPROVED_MODEL_MESSAGE}

    def to_public(self) -> dict[str, Any]:
        """``GET /meta/approval`` body (docs/API.md)."""
        out: dict[str, Any] = {
            "environment": self.environment,
            "serving": self.serving,
            "approval_status": self.approval_status,
            "label": self.label,
            "reason": self.reason,
            "message": self.message,
            "checked_at": self.checked_at,
            "run": self.source.describe() if self.source is not None and self.serving else None,
        }
        if self.environment == PRODUCTION:
            out["live_model_version"] = self.live_model_version
        return out


# --------------------------------------------------------------------------- #
# Manifest helpers
# --------------------------------------------------------------------------- #


def manifest_field(manifest: dict[str, Any], key: str) -> Any:
    """A manifest value at the top level, else under ``extra`` (None when absent)."""
    if not isinstance(manifest, dict):
        return None
    if key in manifest:
        return manifest[key]
    extra = manifest.get("extra")
    if isinstance(extra, dict):
        return extra.get(key)
    return None


def manifest_is_approved(manifest: dict[str, Any]) -> bool:
    """True only for the exact string ``"approved"``; anything else is not approved."""
    value = manifest_field(manifest, "approval_status")
    return isinstance(value, str) and value.strip().lower() == APPROVED


def _read_json(path: Path, missing: str, malformed: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SnapshotError(missing, f"{path.name} not found") from exc
    except (OSError, ValueError) as exc:
        raise SnapshotError(malformed, f"{path.name} is unreadable") from exc
    if not isinstance(payload, dict):
        raise SnapshotError(malformed, f"{path.name} is not a JSON object")
    return payload


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def run_path(root: Path, season: int, week: int, run_id: str) -> Path:
    return root / f"season={season:04d}" / f"week={week:02d}" / f"run={run_id}"


def load_run(root: Path, season: Any, week: Any, run_id: Any, kind: str) -> PredictionSource:
    """Validate a ``(season, week, run_id)`` triple and read its manifest."""
    s, w = _as_int(season), _as_int(week)
    if s is None or w is None or not (1 <= w <= 22) or not (1990 <= s <= 2100):
        raise SnapshotError("pointer_malformed", "season or week is missing or invalid")
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise SnapshotError("pointer_malformed", "run_id is missing or invalid")
    path = run_path(root, s, w, run_id)
    manifest = _read_json(path / MANIFEST_NAME, "manifest_missing", "manifest_malformed")
    m_season, m_week = _as_int(manifest.get("season")), _as_int(manifest.get("week"))
    if m_season != s or m_week != w or manifest.get("run_id") != run_id:
        raise SnapshotError("manifest_mismatch", "manifest does not match its run directory")
    version = manifest.get("model_version")
    if not isinstance(version, str) or not version:
        raise SnapshotError("manifest_malformed", "manifest has no model_version")
    for name in (SIMS_FILE, SUMMARY_FILE):
        if not (path / name).is_file():
            raise SnapshotError("snapshot_incomplete", f"run is missing {name}")
    return PredictionSource(
        kind=kind,
        root=root,
        path=path,
        season=s,
        week=w,
        run_id=run_id,
        model_version=version,
        manifest=manifest,
    )


def read_pointer(root: Path) -> dict[str, Any]:
    return _read_json(root / LATEST_NAME, "no_latest", "latest_malformed")


def load_latest(root: Path, kind: str) -> PredictionSource:
    pointer = read_pointer(root)
    return load_run(root, pointer.get("season"), pointer.get("week"), pointer.get("run_id"), kind)


def find_replays(roots: list[Path]) -> list[PredictionSource]:
    """Every readable replay run under ``roots``, newest ``written_at`` first."""
    found: list[PredictionSource] = []
    for root in roots:
        if not root.is_dir():
            continue
        for manifest_path in root.glob(f"season=*/week=*/run=*/{MANIFEST_NAME}"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                src = load_run(
                    root,
                    manifest.get("season"),
                    manifest.get("week"),
                    manifest.get("run_id"),
                    SOURCE_REPLAY,
                )
            except (SnapshotError, OSError, ValueError):
                continue
            if src.is_replay:
                found.append(src)
    found.sort(key=lambda s: (str(s.manifest.get("written_at") or ""), s.run_id), reverse=True)
    return found


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #


def _production(settings: ApiSettings) -> ApprovalState:
    def blocked(reason: str, live: str | None = None) -> ApprovalState:
        return ApprovalState(
            environment=PRODUCTION,
            serving=False,
            approval_status=NOT_APPROVED,
            reason=reason,
            message=NO_APPROVED_MODEL_MESSAGE,
            live_model_version=live,
        )

    try:
        from ffml.models.registry import get_live

        live = get_live()
    except Exception as exc:  # noqa: BLE001 - any registry failure means no serving
        log.warning("registry unreadable: %r", exc)
        return blocked("registry_unreadable")
    if live is None:
        return blocked("no_live_model")
    if not isinstance(live, dict):
        return blocked("registry_malformed")
    version = live.get("version")
    if not isinstance(version, str) or not version:
        return blocked("registry_malformed")
    if live.get("status") != "live" or not live.get("evaluator_report"):
        return blocked("live_model_without_approval", version)
    root = settings.predictions_dir
    try:
        pointer = read_pointer(root)
        src = load_run(
            root, pointer.get("season"), pointer.get("week"), pointer.get("run_id"), SOURCE_PUBLISHED
        )
    except SnapshotError as exc:
        return blocked(exc.reason, version)
    except Exception as exc:  # noqa: BLE001
        log.warning("predictions pointer unreadable: %r", exc)
        return blocked("snapshot_unreadable", version)
    if not manifest_is_approved(src.manifest):
        return blocked("snapshot_not_approved", version)
    if "approval_status" in pointer and not manifest_is_approved(pointer):
        return blocked("pointer_not_approved", version)
    if src.model_version != version:
        return blocked("model_version_mismatch", version)
    if "model_version" in pointer and pointer.get("model_version") != version:
        return blocked("model_version_mismatch", version)
    # ``environment`` is the prediction mode written by ``ffml predict``: "live",
    # "private_test" or "replay". Only "live" serves; a missing key is accepted for
    # older manifests because every other condition must still hold.
    environment = manifest_field(src.manifest, "environment")
    if environment is not None and environment != LIVE_ENVIRONMENT:
        return blocked("snapshot_not_live", version)
    if "environment" in pointer and pointer.get("environment") != LIVE_ENVIRONMENT:
        return blocked("snapshot_not_live", version)
    if src.is_replay:
        return blocked("snapshot_is_replay", version)
    return ApprovalState(
        environment=PRODUCTION,
        serving=True,
        approval_status=APPROVED,
        reason="approved_live_model",
        message=(
            f"Serving the published run {src.run_id} of the live model {version}, approved by "
            f"its pre-registered test."
        ),
        source=src,
        live_model_version=version,
    )


def _pinned_replay(settings: ApiSettings) -> PredictionSource | None:
    spec = (settings.api_replay_run or "").strip()
    if not spec:
        return None
    parts = spec.split("/")
    if len(parts) != 3:
        log.warning("FFML_API_REPLAY_RUN must be <season>/<week>/<run_id>; got %r", spec)
        return None
    for root in (settings.private_predictions_dir, settings.predictions_dir):
        try:
            return load_run(root, parts[0], parts[1], parts[2], SOURCE_REPLAY)
        except SnapshotError:
            continue
    return None


def _private_test(settings: ApiSettings) -> ApprovalState:
    tried: list[str] = []
    candidates: list[tuple[str, Any]] = [
        ("private_test", lambda: load_latest(settings.private_predictions_dir, SOURCE_PRIVATE)),
        ("replay_pinned", lambda: _pinned_replay(settings)),
        (
            "replay",
            lambda: next(
                iter(find_replays([settings.private_predictions_dir, settings.predictions_dir])),
                None,
            ),
        ),
    ]
    if settings.api_fixture_predictions_dir is not None:
        fixture_root = Path(settings.api_fixture_predictions_dir)
        candidates.append(("fixture", lambda: load_latest(fixture_root, SOURCE_FIXTURE)))
    for name, loader in candidates:
        try:
            src = loader()
        except SnapshotError as exc:
            tried.append(f"{name}: {exc.reason}")
            continue
        except Exception as exc:  # noqa: BLE001
            tried.append(f"{name}: {type(exc).__name__}")
            continue
        if src is None:
            tried.append(f"{name}: none")
            continue
        return ApprovalState(
            environment=PRIVATE_TEST,
            serving=True,
            approval_status=NOT_APPROVED,
            reason=f"private_test_{src.kind}",
            message=NOT_APPROVED_NOTICE,
            source=src,
        )
    log.info("private_test: no snapshot (%s)", "; ".join(tried))
    return ApprovalState(
        environment=PRIVATE_TEST,
        serving=False,
        approval_status=NOT_APPROVED,
        reason="no_private_test_snapshot",
        message=NO_PRIVATE_SNAPSHOT_MESSAGE,
    )


def evaluate(settings: ApiSettings) -> ApprovalState:
    """Evaluate the gate now (no cache)."""
    if settings.is_private_test:
        return _private_test(settings)
    return _production(settings)


class ApprovalGate:
    """Thread-safe cache of :func:`evaluate` with a short TTL."""

    def __init__(self, settings: ApiSettings):
        self.settings = settings
        self._lock = threading.Lock()
        self._state: ApprovalState | None = None
        self._at = 0.0

    def state(self, *, refresh: bool = False) -> ApprovalState:
        ttl = float(self.settings.approval_ttl_seconds)
        with self._lock:
            now = time.monotonic()
            if refresh or self._state is None or ttl <= 0 or now - self._at > ttl:
                self._state = evaluate(self.settings)
                self._at = now
            return self._state

    def clear(self) -> None:
        with self._lock:
            self._state = None


__all__ = [
    "APPROVED",
    "HEADER",
    "NOT_APPROVED",
    "NOT_APPROVED_NOTICE",
    "NO_APPROVED_MODEL_MESSAGE",
    "NO_APPROVED_MODEL_STATUS",
    "ApprovalGate",
    "ApprovalState",
    "PredictionSource",
    "SnapshotError",
    "evaluate",
    "find_replays",
    "load_latest",
    "load_run",
    "manifest_field",
    "manifest_is_approved",
]
