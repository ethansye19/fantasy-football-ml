# Evaluator: 2025 released as training data; chain_v1 rejected (EV-4.1)

Independent Evaluator, 2026-11-04. Done under D-098 (owner) and D-100, with the amended D-058. The release makes 2025 training data and history only; it approves nothing. Approval exists only in the model registry, and this candidate is now rejected there. The holdout session it rests on ended NOT APPROVED (`reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md`, commit `824162d`; addendum `0d673db`).

No backtest was run, no 2025 outcome was read, and `approve` and `set_live` were not called.

## Pre-checks (all passed, 22:42 UTC)

- `artifacts/holdout_release.json` did not exist.
- Registry entry `chain_v1-primary-c2024w19-20261102T222634Z` had status `candidate` (`evaluator_report` null).
- `git status` showed no changes under `ffml/holdout.py`, `ffml/models/registry.py` or `ffml/sim/predict.py`.
- No `FFML_*` variable was set in the shell.

## Calls (UTC, 2026-11-04)

| time | call | unlock flag | result |
| --- | --- | --- | --- |
| 22:43:08 | `ffml.holdout.release_holdout("chain_v1-primary-c2024w19-20261102T222634Z", "reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md", approved=False, note="released as training data and history for chain_v2 (D-098); not an approval; holdout session verdict NOT APPROVED")` | `FFML_HOLDOUT_UNLOCK=true` on that one Python process only | wrote `artifacts/holdout_release.json` |
| 22:43:15 | `ffml.models.registry.reject("chain_v1-primary-c2024w19-20261102T222634Z", "reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md")` | not set | status `rejected` |

## Release marker (`artifacts/holdout_release.json`)

```json
{
  "season": 2025,
  "released_at": "2026-11-04T22:43:08.791955+00:00",
  "model_version": "chain_v1-primary-c2024w19-20261102T222634Z",
  "evaluator_report": "reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md",
  "approved": false,
  "note": "released as training data and history for chain_v2 (D-098); not an approval; holdout session verdict NOT APPROVED",
  "git": "052b6e1"
}
```

`git` is the HEAD at release time (`052b6e1`, a commit by another agent after `d86a40c`).

## Registry entry (`artifacts/models/REGISTRY.json`, reloaded from disk after the call)

```json
{
  "name": "chain_v1",
  "version": "chain_v1-primary-c2024w19-20261102T222634Z",
  "path": "artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z",
  "status": "rejected",
  "created_at": "2026-11-03T02:55:27.642594+00:00",
  "git": "3455c29",
  "backtest_run_id": "default_20261102",
  "evaluator_report": "reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md",
  "history": [
    {"status": "candidate", "at": "2026-11-03T02:55:27.670917+00:00"},
    {"status": "rejected", "at": "2026-11-04T22:43:15.741095+00:00"}
  ],
  "calibration": {
    "path": "artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration.json",
    "sha256": "3c77725c4e6cf21cd2fc26acaeeea2b3aff22991087614bf35dcd9da280bcd39",
    "calibration_version": "v3-tie-aware-quantile"
  },
  "calibration_history": [
    {
      "path": "artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration.json",
      "sha256": "860af8926e75b93e781eb94a0c6055206b8fdf78b9e539f072eb80293a21da19",
      "calibration_version": "v2-per-position-isotonic",
      "replaced_at": "2026-11-04T04:14:28.442672+00:00",
      "archived_as": "artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration_v2.json"
    }
  ],
  "rejected_at": "2026-11-04T22:43:15.741095+00:00"
}
```

This is the only entry in the registry. `registry.get_live()` returns `None`.

## Verification (no unlock variable set; `holdout_unlock` False)

| check | result |
| --- | --- |
| release marker | `approved: false`, `note` present (above) |
| registry entry | `status: rejected`, `evaluator_report` and `rejected_at` set (above) |
| `ffml predict` (live, no arguments), 22:43:34 | exit 2: `no approved model: live projections are off until a model passes its pre-registered test (D-097, D-099)` |
| `ffml predict --private-test --model-version chain_v1-primary-c2024w19-20261102T222634Z`, 22:43:35 | exit 2: `prediction refused: private test refused: only candidates can be tested; 'chain_v1-primary-c2024w19-20261102T222634Z' is rejected` |
| `artifacts/holdout_access.log` | 85 lines before the release, 85 after both predict calls: no new line |
| `data/predictions` and `data/predictions_private` | file listing (6 files, sizes and timestamps) identical before and after; `data/predictions_private` does not exist |
