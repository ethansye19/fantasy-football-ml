# Phase 4 / ML Engineer / approval gate
Date: 2026-11-04
Scope: close the gap where releasing 2025 would let `ffml predict` fall back
to the newest chain artifact (unapproved chain_v1) and publish unlabeled
projections (D-098 item 5, D-099). Approval gate only. No model was trained
or built, and nothing was released, rejected or approved in the real registry.

Task list (from the brief):
1. Prediction modes in `run_predict` and `ffml predict`: live (registry live
   version only, `NoApprovedModel` before any read, CLI exit 2), private test
   (candidates only, private store, "not approved" labels), replay (labels
   only). Keep the holdout guard call unchanged.
2. Registry: `rejected` status and `reject(version, evaluator_report)`.
3. `release_holdout(model_version, evaluator_report, *, approved, note="")`,
   new docstrings, and a grep for anything that treats `is_released()` as
   approval.
4. Tests on temporary directories and a temporary registry, the fast suite,
   `ruff check`, and this note.

## Done

### Registry: `ffml/models/registry.py` (commit 95729c2)
- `STATUSES` gains `rejected`. New constant
  `REJECT_VERDICT_LINE = "Verdict: NOT APPROVED"`.
- `reject(version, evaluator_report, *, registry=None)` mirrors `approve`:
  - candidates only;
  - the report must be under `reports/` and contain the exact line
    `Verdict: NOT APPROVED`;
  - records `evaluator_report` and `rejected_at`, and appends `rejected` to
    `history`.
- `check_report(report, *, verdict_line=VERDICT_LINE)`: the verdict line is
  now a keyword argument. The default is unchanged, so `approve` behaves as
  before.
- A rejected entry is terminal:
  - `approve`, `set_live` and `update_calibration` already refuse anything
    that is not a candidate (or not approved);
  - `retire` now also refuses a rejected entry, so the verdict stays
    visible as the entry's status.

### Holdout release: `ffml/holdout.py` (commit 8542982)
- New signature:
  `release_holdout(model_version, evaluator_report, *, approved: bool, note: str = "")`.
  - `approved` is keyword-only and required.
  - A value that is not a bool raises `TypeError`, so `approved="no"`
    cannot pass.
- The payload gains `approved` and `note`.
- The module, function and `is_released` docstrings now say: a release
  means the holdout season has been evaluated once and is available as
  training data and history (D-058, D-098). It is not an approval, which is
  recorded only in the registry.
- The lock logic and `assert_seasons_allowed` are unchanged.

### Private store and labels: `ffml/config.py`, `ffml/sim/snapshot.py` (commit 2e66f4f)
- New property `Settings.private_predictions_dir`, equal to
  `data_dir / "predictions_private"`.
- `write_predictions_snapshot` writes the keys in `LABEL_KEYS`
  (`environment`, `approval_status`, `label`), whenever `meta` carries them:
  - at the **top level** of `_manifest.json`, not repeated under `extra`;
  - in the `LATEST` pointer of the store it writes to.
- Without those keys, the manifest and `LATEST` look exactly as before.

### Prediction modes: `ffml/sim/predict.py`, `ffml/cli.py` (commit 06ef6a1)

All refusal and gate checks run first in `run_predict`, before
`current_state`, the holdout guard, `ChainModel.load` or any write. The
`assert_seasons_allowed(history, "predict_replay" if replay else "predict")`
call itself is unchanged.

**Live (the default)**
- The model is `registry.get_live()` and nothing else.
  - `model_dir`, `model`, `model_version` and `chain_root` are refused with
    `PredictionRefused` (a `ValueError`).
  - There is no newest-artifact fallback.
- No live version raises `NoApprovedModel`, a subclass of
  `LivePredictionBlocked`. Its message is exactly:
  "no approved model: live projections are off until a model passes its
  pre-registered test (D-097, D-099)".
  - A live entry without an `evaluator_report`, which only a hand edit can
    produce, counts as no approved model.
- Manifest: `environment: "live"`, `approval_status: "approved"`,
  `model_version` = the registry version. The public `LATEST` moves as
  before, and also carries the two labels.
- `model_source = {"kind": "registry_live", "version", "path", "registry_status", ...}`.

**Private test**
- Python: `private_test=True, model_version=<v>`.
- `<v>` must be a registry `candidate`. Refused with `PredictionRefused`:
  `rejected`, `retired`, unknown, `approved` and `live` (the last two with a
  pointer to live mode). Also refused: a missing `model_version`,
  `replay=True`, a `model` object, `chain_root`, and a `model_dir` that is
  not the entry's path.
- It always writes to `Settings.private_predictions_dir`. A `root` that is
  anything else, including the public store, is refused.
- It moves only the private `LATEST`. The run id ends in `-private`.
- Manifest and private `LATEST`: `environment: "private_test"`,
  `approval_status: "not approved"`, `label: "not approved"`, and
  `model_version`.
- `player_summary` and `ros_summary` (when written) get an
  `approval_status` column with the value "not approved". `player_sims` is
  unchanged.
- `model_source.kind = "registry_candidate"`.
- Rows are built live, as of now, through the same pipeline as live mode.

**Live and private test**
- If the artifact's `train_manifest.json` names a different
  `model_version` than the registry entry, the run is refused.

**Replay**
- Model resolution is unchanged: `resolve_model_dir`, then the registry's
  live version, then the newest artifact.
- Rows, simulation and store are unchanged.
- The manifest gains `environment: "replay"` and `approval_status`.
  - The status is "approved" only when the replayed artifact path is the
    registry's live entry path.
  - It is "not approved" otherwise, including a `model` object.
- `update_latest=True` is now refused, so replays can never move `LATEST`.
  Before, they could only if asked explicitly.
- `model_version` is refused.

**General**
- `meta["mode"]` equals the environment (`live`, `private_test` or
  `replay`). `PredictResult` gains `environment` and `approval_status`.
- `blocked_message` (the holdout block) no longer says that the session
  "approves a model and calls release_holdout". It now says "releases it
  ... a release is not an approval".
- CLI `ffml predict` gains `--private-test`, `--model-version` and
  `--model-dir` (the last only together with `--private-test` and the
  matching registry path). Exit code 2 for:
  - `NoApprovedModel`: prints exactly the message above;
  - the holdout block: as before;
  - `PredictionRefused`: prints `prediction refused: ...`.
- Nothing that affects numbers changed: model logic, features, calibrators,
  seeds (`week_seed` and offsets) and the simulation are untouched. The
  summary column is added after summarization.

### Tests
- `tests/sim/test_predict.py`, rewritten for the modes. Shared pieces:
  - `tests/sim/conftest.py`: session `synthetic_model_dir`, `registry_env`
    (temporary data, artifacts, registry and reports; holdout locked) and a
    `cli` fixture (Typer runner that restores the logging handlers);
  - `tests/sim/regtoy.py`: helpers for a temporary registry.
- Live with no live model, for registries that are empty or hold only a
  candidate, rejected, retired or approved-not-live entry:
  - raises `NoApprovedModel` with the exact message, both with and without
    season/week;
  - a fake release marker is in place, so the guard would log any access;
    the artifacts tree, registry included, is byte-identical afterwards and
    there is no `holdout_access.log`;
  - nothing is written under the data dir;
  - `current_state` is patched to fail if it is called.
  - I checked that this test can fail: with a live model, the same setup
    writes an access-log line.
- A hand-edited live entry without a report raises `NoApprovedModel`.
- CLI: exit code 2 and the message (whitespace-normalized), with and
  without season/week, and nothing is written. Refusals exit 2. The
  `--private-test`, `--model-version`, `--model-dir` and `--no-ros` options
  reach `run_predict`.
- Live with a live model:
  - a full synthetic run (2023 week 5 in progress) writes manifest
    `approved`/`live`, `model_version` = the registry version and
    `model_source.kind = registry_live`;
  - the public `LATEST` moves and is labeled; the private store is not
    created;
  - there is no `approval_status` column in public summaries;
  - the overrides `model_dir`, `model`, `model_version` and `chain_root` are
    refused;
  - an artifact built as another version is refused.
- Private test of a candidate:
  - writes only under `predictions_private`;
  - a pre-existing public `LATEST`, and the whole public store, are
    byte-identical afterwards;
  - manifest labels and private `LATEST` labels are present;
  - the `approval_status` column is in `player_summary` and `ros_summary`.
- Private test refuses rejected, retired, approved, live and unknown
  versions, plus the argument checks above. A matching `model_dir` passes
  the gate.
- Replay:
  - a candidate's replay is labeled "not approved" and `LATEST` is not
    created;
  - `update_latest=True` and `model_version` are refused;
  - a replay of the live version's artifact, by `model_dir` or through the
    registry, is "approved", and a `model` object is "not approved".
- The slow 2024 replay test now patches `get_live`, so it never reads the
  real registry, and checks the replay labels. It was not run, because it is
  slow-marked.
- `tests/sim/test_snapshot.py`: labels are at the top level of the manifest
  and `LATEST`, and the old shape is kept without labels.
- `tests/models/test_registry.py`:
  - `reject` requires a report under `reports/` with the exact line, and an
    approval report or an inline mention cannot reject;
  - status, history and `list_models("rejected")` are correct;
  - a rejected entry cannot be approved, set live, have its calibration
    updated, be rejected again or be retired;
  - an approved entry cannot be rejected.
- `tests/test_holdout.py`:
  - the payload has `approved` and `note`, and `note` defaults to "";
  - a release does not create a registry;
  - calling without `approved`, or with it positional, is a `TypeError`;
  - a non-bool `approved` is a `TypeError`;
  - existing calls were updated.
- None of these tests touch the real registry, `artifacts/holdout_release.json`
  or `artifacts/holdout_access.log`. Settings are monkeypatched to temporary
  directories.

## Is `is_released()` treated as approval anywhere?

No. `grep -rn "is_released|release_info|holdout_release|is_unlocked|release_holdout"`
over the repo, excluding `.venv`, `.git` and `data`:

| Where | Use | Approval? |
| --- | --- | --- |
| `ffml/holdout.py` `is_unlocked()` | data lock for `allowed_seasons` | no, data access only |
| `ffml/holdout.py` `assert_seasons_allowed()` | allows holdout reads after release; writes `released=true` in the access log | no |
| `ffml/holdout.py` `release_holdout()` | refuses a second release | no |
| `tests/test_holdout.py` | tests of the above | no |
| `api/` (Backend Engineer, untracked, work in progress) | no hit; it gates on `registry.get_live()` plus manifest `approval_status`, `environment` and `model_version` | no |

- Nothing in `ffml/sim`, `ffml/models`, `ffml/eval`, `ffml/features`,
  `ffml/scoring` or `scripts/` reads the release marker. The modules that
  import `ffml.holdout` use only `assert_seasons_allowed` (and `train.py`
  uses `_git_sha`).
- Text that described the release as following an approval:
  - the old `ffml/holdout.py` docstring: fixed;
  - `blocked_message` in `ffml/sim/predict.py`: fixed;
  - `docs/DECISIONS.md` D-058 ("nothing reads 2025 until the Independent
    Evaluator's single holdout session ... approves a model. The evaluator
    then calls `release_holdout(model_version, report)`"): not mine to edit;
    see the proposal below;
  - `docs/status/gate-3.md`: historical, left as is.

## Evidence
- Whole fast suite, `PYTHONUTF8=1 FFML_OFFLINE=1 .venv/Scripts/pytest -q -m "not slow"`
  (no exclusions, with the Backend Engineer's committed `tests/api` and the
  working tree as it stood):
  `523 passed, 2 skipped, 6 deselected, 1 warning in 587.86s (0:09:47)`.
  - The warning is Starlette's httpx deprecation, from `tests/api`.
- The same suite with `--ignore=tests/api`:
  `457 passed, 2 skipped, 6 deselected in 295.88s`.
  - The `--ignore` was needed for one earlier run only: the then-untracked
    `tests/api` failed at collection with `No module named 'api.settings'`.
- Baseline before any change (same command):
  `432 passed, 2 skipped, 6 deselected in 177.54s`, so +25 tests here.
- `FFML_OFFLINE=1` skips the two network tests, the same 2 skips as the
  baseline.
- Targeted: `pytest -q -m "not slow" tests/sim tests/models/test_registry.py tests/test_holdout.py`:
  72 passed, 1 deselected in about 13s.
- `ruff check` on every path I touched: All checks passed!
  - `ruff check .` fails only on F821 (undefined `_meta`) at
    `ffml/eval/power.py:1151`. That file is the Evaluator's untracked work
    in progress (ffml/eval is outside my scope), and I did not touch it.
  - `ruff check . --extend-exclude ffml/eval/power.py`: All checks passed!
- `ruff format --check` flags lines in `ffml/cli.py` and `ffml/holdout.py`
  that I did not touch. I left them to keep the diff minimal. My new and
  rewritten test files are formatted.

## Not done / blocked
- No model was trained or built (chain_v2 comes in a later message).
- `reject` was not called on the real registry, and `release_holdout` was
  not called. Both are for the Independent Evaluator.
- The slow 2024 replay test was not run (memory budget; slow-marked).

## Decisions proposed
- **D-100 (proposal).** Approval gate for predictions:
  - Live `ffml predict` requires `registry.get_live()` with an evaluator
    report, and refuses overrides.
  - Without one it raises `NoApprovedModel` and exits 2 before reading
    anything.
  - Private tests run only registry candidates, write only to
    `Settings.private_predictions_dir`, and are labeled "not approved"
    (manifest, private `LATEST`, summary column).
  - Replays are labeled and never move `LATEST`.
  - Manifest label keys are top-level: `environment`, `approval_status`,
    `label`.
  - `rejected` is a terminal registry status set by `reject()` from a
    `Verdict: NOT APPROVED` report.
- **D-058 note (proposal).** Amend D-058 so it no longer reads as "release
  after approval". After D-098, a release follows the single holdout session
  whatever its verdict. It records `approved` (false for chain_v1) and a
  `note`, and approval lives only in the registry. Suggested
  Evaluator call for chain_v1:
  `release_holdout("chain_v1-primary-c2024w19-20261102T222634Z", "reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md", approved=False, note="released as training data and history for chain_v2 (D-098); not an approval")`.
  Then
  `registry.reject("chain_v1-primary-c2024w19-20261102T222634Z", "reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md")`.
  The report has the exact line `Verdict: NOT APPROVED` on line 3.

## Next
- The Evaluator releases 2025 (`approved=False`) and rejects chain_v1 in the
  registry.
- The Backend Engineer: the keys and paths match `api/approval.py` as it
  stands (top-level `approval_status`/`environment`, `LATEST` labels,
  `Settings.private_predictions_dir`, live `model_version` = the registry
  version).
- chain_v2 build (later brief): register it as a candidate, then exercise it
  with `ffml predict --private-test --model-version <v2>`.
