"""QA-4.1 compliance scan (a): in production no route returns projection data without a
live, approved registry entry (D-097, D-099, D-100, D-101; docs/API.md section 3.2).

The fuzzer starts from a temporary production setup that *does* serve (live
registry entry with an approval report, an approved live run behind
``LATEST``) and breaks it in one or more ways: malformed or missing
registries, registry entries that are not live, malformed ``LATEST``
pointers (path traversal included), manifests with wrong labels, wrong
environment, replays, mismatched versions or missing files. Every broken
world must answer every projection route with the fixed 503 body and
``X-FFML-Approval: none``, and ``/meta/approval`` must say it is not serving.
A file audit checks that the gate never reads outside the temporary tree.

Known gaps are pinned as strict xfails with their finding ids from
docs/SECURITY.md (remove the marker in the commit that fixes the finding).
"""

from __future__ import annotations

import json
import random
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from api_factory import APPROVED_LABELS, live_entry, write_registry, write_snapshot
from api_helpers import NO_MODEL_BODY, all_routes, call
from qa_support import file_audit, under

ROUTES = all_routes()
VERSION = "chain_v2-test"
META = ("/meta/health", "/meta/approval", "/meta/model-card", "/meta/run-info")
MISSING = object()


class Raw(str):
    """File content written verbatim (not JSON-encoded)."""


class World:
    """A production setup that serves, whose JSON files and run files can be broken."""

    def __init__(self, dirs: dict[str, Path]):
        self.dirs = dirs
        self.root = dirs["data"].parent
        self.report = dirs["reports"] / f"evaluator_release_{VERSION}.md"
        self.report.write_text("# Release note\n\nVerdict: APPROVED\n", encoding="utf-8")
        self.registry_path = write_registry(dirs["artifacts"], [self.live()])
        self.run = write_snapshot(dirs["predictions"], model_version=VERSION, labels=APPROVED_LABELS)
        self.run_id = self.run.name.removeprefix("run=")
        self.latest_path = dirs["predictions"] / "LATEST"
        self.manifest_path = self.run / "_manifest.json"
        self.base = {
            "registry": json.loads(self.registry_path.read_text(encoding="utf-8")),
            "latest": json.loads(self.latest_path.read_text(encoding="utf-8")),
            "manifest": json.loads(self.manifest_path.read_text(encoding="utf-8")),
        }
        self.run_files = {p.name: p.read_bytes() for p in self.run.iterdir() if p.is_file()}

    def live(self) -> dict[str, Any]:
        entry = live_entry(VERSION)
        entry["evaluator_report"] = str(self.report)  # absolute path under the reports dir
        entry["history"] = [{"status": s} for s in ("candidate", "approved", "live")]
        return entry

    def state(self) -> dict[str, Any]:
        return {k: json.loads(json.dumps(v)) for k, v in self.base.items()} | {"actions": []}

    def apply(self, state: dict[str, Any]) -> None:
        self.reset()
        for key, path in (
            ("registry", self.registry_path),
            ("latest", self.latest_path),
            ("manifest", self.manifest_path),
        ):
            value = state[key]
            if value is MISSING:
                path.unlink()
            elif isinstance(value, Raw):
                path.write_text(str(value), encoding="utf-8")
            else:
                path.write_text(json.dumps(value), encoding="utf-8")
        for action in state["actions"]:
            action(self)

    def reset(self) -> None:
        for extra in self.run.parent.iterdir():
            if extra != self.run:
                shutil.rmtree(extra)
        self.run.mkdir(parents=True, exist_ok=True)
        for child in self.run.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            elif child.name not in self.run_files:
                child.unlink()
        for name, data in self.run_files.items():
            (self.run / name).write_bytes(data)
        self.registry_path.write_text(json.dumps(self.base["registry"]), encoding="utf-8")
        self.latest_path.write_text(json.dumps(self.base["latest"]), encoding="utf-8")
        self.manifest_path.write_text(json.dumps(self.base["manifest"]), encoding="utf-8")


# --------------------------------------------------------------------------- mutations
# Every mutation alone breaks a serving condition. Mutations in one slot touch the same
# fields, so a combination drawn from distinct slots never repairs another mutation; a
# field edit on a file that another mutation already replaced or removed is a no-op.

Mutation = tuple[str, str, Callable[[dict[str, Any]], None]]


def _json_obj(value: Any) -> bool:
    return isinstance(value, dict) and not isinstance(value, Raw)


def _first_entry(s: dict[str, Any]) -> dict[str, Any] | None:
    reg = s["registry"]
    models = reg.get("models") if _json_obj(reg) else None
    if isinstance(models, list) and models and isinstance(models[0], dict):
        return models[0]
    return None


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def f(s: dict[str, Any]) -> None:
        s[key] = value

    return f


def _entry(field: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def f(s: dict[str, Any]) -> None:
        entry = _first_entry(s)
        if entry is None:
            return
        if value is MISSING:
            entry.pop(field, None)
        else:
            entry[field] = value

    return f


def _field(key: str, field: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def f(s: dict[str, Any]) -> None:
        if not _json_obj(s[key]):
            return
        if value is MISSING:
            s[key].pop(field, None)
            if isinstance(s[key].get("extra"), dict):
                s[key]["extra"].pop(field, None)
        else:
            s[key][field] = value

    return f


def _action(fn: Callable[[World], None]) -> Callable[[dict[str, Any]], None]:
    def f(s: dict[str, Any]) -> None:
        s["actions"].append(fn)

    return f


def _two_live(s: dict[str, Any]) -> None:
    entry = _first_entry(s)
    if entry is not None:
        s["registry"]["models"].append(dict(entry, version="chain_v2-other"))


def _replay_run_dir(world: World) -> None:
    """Rename the run to ``...-replay`` and make the pointer and manifest agree."""
    try:
        manifest = json.loads(world.manifest_path.read_text(encoding="utf-8"))
        latest = json.loads(world.latest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not (isinstance(manifest, dict) and isinstance(latest, dict)):
        return
    new_id = world.run_id + "-replay"
    target = world.run.parent / f"run={new_id}"
    shutil.copytree(world.run, target)
    manifest["run_id"] = new_id
    (target / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    latest["run_id"] = new_id
    world.latest_path.write_text(json.dumps(latest), encoding="utf-8")


def _remove(name: str) -> Callable[[World], None]:
    return lambda world: (world.run / name).unlink(missing_ok=True)


def _as_directory(name: str) -> Callable[[World], None]:
    def f(world: World) -> None:
        (world.run / name).unlink(missing_ok=True)
        (world.run / name).mkdir(exist_ok=True)

    return f


#: registry ``models`` values that are not lists of objects
BAD_MODELS: tuple[Any, ...] = (None, {}, "live", [None], [1], [[]], ["live"])


def _registry_mutations() -> list[Mutation]:
    out: list[Mutation] = [("registry", "absent", _set("registry", MISSING))]
    for i, text in enumerate(
        ["", "null", "[]", "{}", "{not json", "\ufeff" + json.dumps({"format": "ffml.registry/1"}),
         '{"format": "ffml.registry/1"}', '{"models": []}', "true", '"live"']
    ):
        out.append(("registry", f"raw{i}", _set("registry", Raw(text))))
    for value in BAD_MODELS:
        out.append(("registry", f"models={value!r}", _field("registry", "models", value)))
    out.append(("registry", "format=2", _field("registry", "format", "ffml.registry/2")))
    out.append(("registry", "format missing", _field("registry", "format", MISSING)))
    for status in ("candidate", "approved", "rejected", "retired", "Live", "LIVE", " live",
                   "live ", "", None, 1, True, ["live"], MISSING):
        out.append(("registry", f"status={status!r}", _entry("status", status)))
    for report in (None, "", 0, False, [], MISSING):
        out.append(("registry", f"report={report!r}", _entry("evaluator_report", report)))
    for version in (None, "", 123, [VERSION], {}, MISSING):
        out.append(("registry", f"version={version!r}", _entry("version", version)))
    out.append(("registry", "two live entries", _two_live))
    return out


def _version_mutations() -> list[Mutation]:
    out: list[Mutation] = [
        ("version", "registry version differs", _entry("version", "chain_v2-other"))
    ]
    for value in ("chain_v1", VERSION.upper(), f" {VERSION}", f"{VERSION} ", "", None, 123,
                  [VERSION], MISSING):
        out.append(("version", f"manifest model_version={value!r}",
                    _field("manifest", "model_version", value)))
    return out


def _latest_mutations(run_id: str) -> list[Mutation]:
    out: list[Mutation] = [("latest", "absent", _set("latest", MISSING))]
    for i, text in enumerate(["", "garbage", "null", "[]", "1", '"x"', "{}",
                              "\ufeff" + json.dumps({"season": 2024})]):
        out.append(("latest", f"raw{i}", _set("latest", Raw(text))))
    for value in (MISSING, None, "", "2024x", -1, 0, 1989, 2101, 2024.5, True, [2024], "20 24"):
        out.append(("latest", f"season={value!r}", _field("latest", "season", value)))
    for value in (MISSING, None, 0, 23, -1, 10.5, True, "10.0", "1e1", "11"):
        out.append(("latest", f"week={value!r}", _field("latest", "week", value)))
    for value in (MISSING, None, "", "..", ".", f"../{run_id}", "../../../etc", "/etc/passwd",
                  "C:\\Windows\\win.ini", "a/b", "a\\b", "x" * 200, f"{run_id}\x00x",
                  f"\u00fc{run_id}", f" {run_id}", f"{run_id} ", "does-not-exist", 123,
                  f"run={run_id}"):
        out.append(("latest", f"run_id={value!r}", _field("latest", "run_id", value)))
    return out


def _pointer_mutations() -> list[Mutation]:
    out: list[Mutation] = []
    for value in ("not approved", "", None, False, "pending", ["approved"]):
        out.append(("pointer", f"approval_status={value!r}",
                    _field("latest", "approval_status", value)))
    for value in ("replay", "private_test", "", None, "LIVE", "production"):
        out.append(("pointer", f"environment={value!r}", _field("latest", "environment", value)))
    for value in ("chain_v1", "", None):
        out.append(("pointer", f"model_version={value!r}",
                    _field("latest", "model_version", value)))
    return out


def _label_mutations() -> list[Mutation]:
    out: list[Mutation] = [
        ("label", "approval_status missing", _field("manifest", "approval_status", MISSING))
    ]
    for value in (None, "", "not approved", "unapproved", "approved?", "approved_pending",
                  "pre-approved", "approved, pending review", False, True, 1, ["approved"],
                  {"approved": True}):
        out.append(("label", f"approval_status={value!r}",
                    _field("manifest", "approval_status", value)))

    def top_not_extra_approved(s: dict[str, Any]) -> None:
        if _json_obj(s["manifest"]):
            s["manifest"]["approval_status"] = "not approved"
            extra = s["manifest"].get("extra") or {}
            s["manifest"]["extra"] = {**extra, "approval_status": "approved"}

    out.append(("label", "top not approved, extra approved", top_not_extra_approved))
    return out


def _environment_mutations() -> list[Mutation]:
    out: list[Mutation] = []
    for value in ("replay", "private_test", "", "LIVE", " live", "test", "production", 0, False,
                  ["live"], {"live": 1}):
        out.append(("environment", f"environment={value!r}",
                    _field("manifest", "environment", value)))

    def extra_replay(s: dict[str, Any]) -> None:
        if _json_obj(s["manifest"]):
            s["manifest"].pop("environment", None)
            extra = s["manifest"].get("extra") or {}
            s["manifest"]["extra"] = {**extra, "environment": "replay"}

    out.append(("environment", "extra environment replay", extra_replay))
    out.append(("environment", "mode replay", _field("manifest", "mode", "replay")))
    out.append(("environment", "run id ends -replay", _action(_replay_run_dir)))
    return out


def _shape_mutations() -> list[Mutation]:
    out: list[Mutation] = [("shape", "manifest absent", _set("manifest", MISSING))]
    for i, text in enumerate(["", "[1, 2", "null", "[]", '"x"', "{}",
                              "\ufeff" + json.dumps({"season": 2024})]):
        out.append(("shape", f"manifest raw{i}", _set("manifest", Raw(text))))
    for field, value in (("season", 2023), ("week", 11), ("run_id", "other"), ("season", "x"),
                         ("week", None), ("run_id", None)):
        out.append(("shape", f"manifest {field}={value!r}", _field("manifest", field, value)))
    for name in ("player_sims.parquet", "player_summary.parquet"):
        out.append(("files", f"{name} removed", _action(_remove(name))))
        out.append(("files", f"{name} is a directory", _action(_as_directory(name))))
    return out


def all_mutations(run_id: str) -> list[Mutation]:
    return (
        _registry_mutations() + _version_mutations() + _latest_mutations(run_id)
        + _pointer_mutations() + _label_mutations() + _environment_mutations()
        + _shape_mutations()
    )


# --------------------------------------------------------------------------- checks


def _assert_serves(client) -> None:
    r = client.get("/projections")
    assert r.status_code == 200, r.text
    assert r.headers["X-FFML-Approval"] == "approved"
    assert client.get("/meta/approval").json()["serving"] is True


def _gate_problems(client, label: str) -> list[str]:
    """Every projection route refuses with the fixed body; /meta/approval agrees."""
    problems = []
    for method, template in ROUTES:
        try:
            r = call(client, method, template)
        except Exception as exc:  # noqa: BLE001 - TestClient re-raises server errors
            problems.append(f"{label}: {method} {template} raised {exc!r}")
            continue
        if r.status_code != 503 or r.headers.get("X-FFML-Approval") != "none":
            problems.append(f"{label}: {method} {template} -> {r.status_code} {r.text[:120]}")
        elif r.json() != NO_MODEL_BODY:
            problems.append(f"{label}: {method} {template} body {r.text[:120]}")
    meta = client.get("/meta/approval").json()
    if meta.get("serving") is not False or meta.get("run") is not None:
        problems.append(f"{label}: /meta/approval {meta}")
    if meta.get("approval_status") != "not approved":
        problems.append(f"{label}: /meta/approval approval_status {meta.get('approval_status')!r}")
    return problems


def _meta_problems(client, label: str) -> list[str]:
    """The never-gated meta routes keep answering 200 whatever the files contain."""
    problems = []
    for path in META:
        try:
            r = client.get(path)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: {path} raised {exc!r}")
            continue
        if r.status_code != 200:
            problems.append(f"{label}: {path} -> {r.status_code} {r.text[:120]}")
    return problems


@pytest.fixture
def world(api_dirs) -> World:
    return World(api_dirs)


def test_control_world_serves(make_client, world):
    """The fuzz harness is only meaningful if the unbroken world serves."""
    client = make_client()
    _assert_serves(client)
    world.apply(world.state())
    _assert_serves(client)


def test_every_single_mutation_blocks_every_projection_route(make_client, world):
    client = make_client()
    mutations = all_mutations(world.run_id)
    assert len(mutations) > 150
    problems: list[str] = []
    for slot, name, mutate in mutations:
        state = world.state()
        mutate(state)
        world.apply(state)
        problems += _gate_problems(client, f"[{slot}] {name}")
    world.apply(world.state())
    _assert_serves(client)  # the harness still works after the sweep
    assert problems == []


def test_pointer_mutations_never_read_outside_the_stores(make_client, world):
    """Path traversal in LATEST (``../``, absolute paths, NUL bytes) reaches no file."""
    client = make_client()
    pointer = [m for m in all_mutations(world.run_id) if m[0] in ("latest", "pointer")]
    with file_audit() as touched:
        for _slot, _name, mutate in pointer:
            state = world.state()
            mutate(state)
            world.apply(state)
            client.get("/projections")
            client.get("/meta/approval")
    gate_files = [p for p in touched
                  if Path(p).name in ("LATEST", "_manifest.json", "REGISTRY.json") or "run=" in p]
    assert gate_files, "the audit saw no gate reads"
    outside = sorted({p for p in gate_files if not under(p, world.root)})
    assert outside == [], outside
    assert not [p for p in touched if "passwd" in p or "win.ini" in p.lower()]


def test_random_combinations_block_every_projection_route(make_client, world):
    client = make_client()
    by_slot: dict[str, list[Mutation]] = {}
    for m in all_mutations(world.run_id):
        by_slot.setdefault(m[0], []).append(m)
    rng = random.Random(20261104)
    problems: list[str] = []
    for i in range(120):
        slots = rng.sample(sorted(by_slot), rng.randint(2, 3))
        picked = [rng.choice(by_slot[s]) for s in slots]
        state = world.state()
        for _slot, _name, mutate in picked:
            mutate(state)
        world.apply(state)
        problems += _gate_problems(client, f"combo {i}: " + " + ".join(n for _, n, _ in picked))
    assert problems == []


def test_garbage_bytes_never_serve(make_client, world):
    client = make_client()
    rng = random.Random(7)
    problems: list[str] = []
    for i in range(60):
        target = rng.choice([world.registry_path, world.latest_path, world.manifest_path])
        world.reset()
        target.write_bytes(bytes(rng.randrange(256) for _ in range(rng.randint(0, 300))))
        problems += _gate_problems(client, f"garbage {i} in {target.name}")
        problems += _meta_problems(client, f"garbage {i} in {target.name}")
    assert problems == []


def _meta_cases() -> list[Any]:
    cases = []
    for slot, name, mutate in all_mutations("x"):
        if slot != "registry":
            continue
        cases.append(pytest.param(name, mutate, id=name))
    return cases


@pytest.mark.parametrize("name,mutate", _meta_cases())
def test_meta_routes_survive_malformed_registries(make_client, world, name, mutate):
    state = world.state()
    mutate(state)
    world.apply(state)
    assert _meta_problems(make_client(), name) == []


# --------------------------------------------------------------------------- known gaps (xfail)

@pytest.mark.parametrize(
    "case", ["report_missing", "report_not_approved", "report_outside_reports"]
)
def test_live_entry_needs_an_approval_verdict_on_disk(make_client, world, case):
    state = world.state()
    entry = state["registry"]["models"][0]
    if case == "report_missing":
        entry["evaluator_report"] = str(world.dirs["reports"] / "no_such_report.md")
    elif case == "report_not_approved":
        bad = world.dirs["reports"] / "evaluator_release_rejected.md"
        bad.write_text("# Release note\n\nVerdict: NOT APPROVED\n", encoding="utf-8")
        entry["evaluator_report"] = str(bad)
    else:
        outside = world.root / "elsewhere.md"
        outside.write_text("Verdict: APPROVED\n", encoding="utf-8")
        entry["evaluator_report"] = str(outside)
    world.apply(state)
    assert _gate_problems(make_client(), case) == []


@pytest.mark.parametrize(
    "field,value",
    [("approval_status", "Approved"), ("approval_status", " approved "), ("environment", None)],
)
def test_labels_must_match_exactly(make_client, world, field, value):
    state = world.state()
    state["manifest"][field] = value
    world.apply(state)
    assert _gate_problems(make_client(), f"{field}={value!r}") == []
