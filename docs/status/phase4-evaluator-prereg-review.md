# Phase 4 / Independent Evaluator / review of the chain_v2 live-test pre-registration (EV-4.3, task 2)
Date: 2026-11-05
Scope: evaluator review of `docs/status/livetest-preregistration-2026.md` as
committed at 7899bb5 (file SHA-256 `8c3318605932d97b...`), before the freeze.
Checked: that the JSON block is machine-checkable, the pinned hashes
(LF-normalised for tracked text), `lines_sha256` and `rules_sha256`, the
window, the one-miss rule, the frozen and allowed paths, and that nothing in
the document depends on 2026 week 10 or later outcomes. I did not edit the
pre-registration; the fixes are for the ML Engineer and the orchestrator.

## Method

- `ffml.eval.report_holdout.load_prereg` reads the first fenced JSON block;
  `ffml.eval.livetest_score` (the scorer, same commit as this note) parses the
  `window`, `allowed_paths`, `frozen_paths`, `pins`, `calibration` and
  `rules` fields with the functions the final evaluation will use.
- Hashes recomputed from the working tree: bytes as they are and with CRLF
  normalised to LF.
- `lines_sha256` compared with `report_holdout.lines_spec_sha256()` at HEAD.
- `rules_sha256` reproduced by trial: it is the SHA-256 of the canonical JSON
  (sorted keys, separators `,` and `:`) of the `rules` block without the
  `rules_sha256` field.

## Findings

| id | weight | finding | suggested fix |
| --- | --- | --- | --- |
| F1 | blocking, expected | `commit` is `<frozen commit>`. Until it is filled, every week's "code commit" check fails and the final would be `INVALID (frozen commit)`. | Fill at the freeze with a commit that contains a2f2af0, 6e29122, b22973f and d24a43a, as the document requires; nothing else to do. |
| F2 | should fix before the freeze | `rules_sha256` (`d0f9d555...`) is not the holdout's lines hash and the document does not say how it is computed. It reproduces as the SHA-256 of the canonical JSON of the `rules` block without the field. `rules.lines_sha256` (`ea706e17...`) equals the holdout pre-registration's value and this code's `lines_spec_sha256()`. | Write the rule in the JSON (one field, e.g. `"rules_sha256_rule": "sha256 of the canonical JSON of this block without rules_sha256"`). The scorer verifies `rules_sha256` under that rule and `lines_sha256` against the code; both pass today. |
| F3 | should fix before the freeze | The path policy leaves common paths neither allowed nor frozen: `ffml/eval/` apart from the three frozen files (so the scorer `ffml/eval/livetest_score.py` and `ffml/eval/power.py`), `tests/`, `.github/`, `ffml/cli.py`, `ffml/config.py`, `pyproject.toml`, `requirements.lock.txt`. Under the stated commit relation, any commit touching one of them after the freeze makes every later build, refresh and scoring run invalid. | Decide each: freeze `requirements.lock.txt` and the dependency pins in `pyproject.toml` (numerics), `ffml/config.py` (settings) and the `livetest build-week` and `predict` wiring in `ffml/cli.py`; allow `tests/`, `.github/` and `ffml/eval/` minus the frozen files under the change-log rule. For the scorer itself the evaluator proposes freezing it with the three frozen eval files (it computes the verdict), with changes only through the change log and an owner ruling. List allowed paths one per entry (a JSON list) instead of `"api/, web/"` and `"other docs/, reports/"`; the scorer splits those strings, but a list is unambiguous. |
| F4 | should fix before the freeze | The comparators for L01-L12 and the D-077 reference set are not specified for the live window: which run produces `rolling_avg_4+cal`, `prior_season+cal` and `rolling_median_8+cal` for 2026 weeks 10-18 and 2027 weeks 1-9, under which calibration (the validation run used walk-forward calibration from 2021; the holdout and forward runs used frozen calibrators), and which raw models form `ref_rank` (chain_v2's raw rows, the five baselines, the challenger or not, plus the consensus in the matched population). | One sentence each, and a named harness configuration for the baselines (for example a baselines-only run with online calibration from 2021 through the scored week, written once after each week is final). The scorer takes `--baselines-run <run>` and uses the raw models present as the reference, as `report_holdout` does; the pre-registration should fix that set. |
| F5 | note | The consensus for 2027 weeks depends on the restricted archive having 2027 scrapes. A week without a valid scrape cannot enter L13-L16. | State the rule: such weeks drop out of the start/sit lines only (the 2022-2024 week 18s did the same in validation), and the final report lists them. |
| F6 | verified | Pins: `selection.json` LF `75477b2b97bc7d9b...` and CRLF working copy `d6c1891828657b76...` both correct (the file carries CRLF in the working tree); feature manifest `b119f85cdfdc3546...` (no CRLF; both forms equal); `lines_sha256` equals `lines_spec_sha256()` at HEAD; window 18 weeks (2026 w10-18, 2027 w1-9); one-miss rule complete (first miss excluded, no extension, second miss INVALID, `MISSED.json`, late commitments outside the test). | The Week 9 rehearsal commitment (built at e43e349) recorded the CRLF form in `pins.selection_sha256`; after b22973f the LF form is expected. The scorer accepts either; the document should say which form `check_pins` writes so "pins equal this block" is exact. |
| F7 | note | `window.verdict_after` reads "2027 week 9 (or the last scored week of the window)". The parenthetical could be read as allowing an earlier verdict. | Say: the final is computed after 2027 week 9's games are final in `dim_game`, also when 2027 week 9 is the excluded miss. The scorer refuses the final before that. |
| F8 | note | When the schedule changes after a build (a game moved earlier), the document does not say which kickoff is the deadline. | Say: the commitment's `first_kickoff` (dim_game at build time) is the deadline; the scorer uses it and reports dim_game's current first kickoff beside it. |
| F9 | note | `player_summary.parquet` carries no `pos_min` / `pos_max`; the scorer attaches outcomes and PIT bounds with the harness functions on the latest processed data (position extremes of earlier seasons; for 2027 weeks those include 2026 outcomes). | State it under `scoring`; it is the holdout machinery's convention. |
| F10 | verified | No dependence on 2026 week 10 or later outcomes: the deadlines are schedule data, the history sources end at 2026 week 8 (and the week 9 rehearsal), the pins and hashes are of frozen files, the stored-run hashes predate the window. | None. |
| F11 | note | `frozen_paths` mixes paths with prose ("calibrator settings (...)", "seeds"). | Keep the prose in the table; the machine-checkable list should hold paths only (the scorer ignores entries with spaces). |
| F12 | verified | `one_commitment_per_week`: the scorer reads only `<week>/commitment.json`; a `late/` commitment is never scored; a week with `MISSED.json` is excluded. | None. |

## Evidence

- `python -c` at HEAD: `load_prereg` parses the block; `prereg_window` gives
  18 weeks from `(2026, 10)` to `(2027, 9)`; `allowed_prefixes` gives
  `api/, docs/, docs/status/livetest-changelog-2026.md,
  docs/status/livetest-preregistration-2026.md, ffml/data/, reports/,
  scripts/, web/` and the ten frozen paths; `path_allowed` is false for
  `ffml/eval/livetest_score.py`, `tests/eval/test_livetest_score.py`,
  `.github/workflows/ci.yml`, `ffml/cli.py`, `requirements.lock.txt` (F3).
- `rules_sha256`: canonical JSON of the rules block without the field gives
  `d0f9d555e9122d16...`, equal to the document; the indented form, the
  `lines` string, the `lines_sha256` text and the lines spec do not.
- Hashes: `ffml/models/chain/selection.json` bytes `d6c1891828657b76...`,
  LF `75477b2b97bc7d9b...`; `tests/fixtures/features/feature_manifest_0.2.0.json`
  `b119f85cdfdc3546...` in both forms; `lines_spec_sha256()` =
  `ea706e17578e6b5caa85eb9db3fbffa6758a6bc85dae2169a707b425b91de2ad`.

## Decisions proposed

- None beyond F2 to F4 and the F3 path rulings, which the orchestrator adopts
  or rejects; the Evaluator's own proposal is to freeze
  `ffml/eval/livetest_score.py` at the freeze commit.

## Next

- Re-check the document at the freeze commit (the `commit` field, F2 to F4)
  and record the result in this note.
