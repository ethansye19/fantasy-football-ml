# Live-test change log (companion to docs/status/livetest-preregistration-2026.md)

Every change to the repository that a build, a data refresh or the scorer
uses during the chain_v2 live test (2026 weeks 11-18 and 2027 weeks 1-10, D-127)
is listed here before its first use. Allowed paths and the verification each
change needs are defined in the pre-registration ("Frozen code policy").
Any path not listed as allowed in the pre-registration is frozen.
A change on a frozen path is not logged here; it invalidates the test
unless the owner rules otherwise, and the ruling is recorded in
docs/DECISIONS.md.

One entry per commit, newest last:

| Date (UTC) | Commit | Paths | Reason | Verification | Verified by | First week that uses it |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
| 2026-11-11 | 765fb1a8e55a | `docs/DECISIONS.md`, `docs/status/livetest-preregistration-2026.md` | Pre-registration: frozen commit must include the D-121 to D-129 commits; decision log D-129 (X = 4799913) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-11 | fac6fcbdb188 | `docs/DECISIONS.md`, `reports/evidence/rehearsal/MANIFEST.md`, `reports/evidence/rehearsal/run6_X4799913_partial.jsonl` | Rehearsal evidence: archive run 6's partial results with hashes; decision log D-130 (rerun all ten at X, no suspending, Week 11 start confirmed) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-11 | b2900b0da641 | `README.md`, `api/services/meta_content.py`, `docs/LIMITATIONS.md`, `docs/RUNBOOK.md`, `docs/status/livetest-changelog-2026.md` | App status text and docs: D-127 window (2026 w11-18 + 2027 w1-10), verdict about 2027-11-19, 58% good-model pass rate | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | 34a6644ae325 | `docs/DECISIONS.md`, `reports/evidence/rehearsal/MANIFEST.md`, `reports/evidence/rehearsal/run7_X4799913.json`, `reports/evidence/rehearsal/run7_X4799913_partial.jsonl`, `reports/evidence/rehearsal/run7_X4799913_pytest.txt`, `tests/rehearsal/test_livetest_final_rehearsal.py` | Rehearsal: exact check-name match in the pass-world test (run 7 assertion defect); archive run 7 evidence; decision log D-131 | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | 40065aaf87a4 | `docs/status/phase5-qa-rehearsal.md`, `reports/livetest_final_rehearsal.md` | Rehearsal report and status note: the final rehearsal at X = 4799913 (run 7) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | 80c1650518c7 | `docs/status/phase4-evaluator-prefreeze-review.md` | Freeze review part 2 (provisional): run 7 at X 4799913 passes; the run 7 failure was a test defect | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | eeb5c6ca3b56 | `docs/DECISIONS.md`, `reports/evidence/rehearsal/MANIFEST.md`, `reports/evidence/rehearsal/run8_X4799913_partial.jsonl`, `tests/rehearsal/_rehearsal_world.py`, `tests/rehearsal/test_livetest_final_rehearsal.py` | Rehearsal: default candidate X = 4799913 (Evaluator finding 6); archive run 8's partial results; decision log D-132 (run 8 stopped for low memory) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | cfcfcd6dce53 | `docs/DECISIONS.md`, `docs/status/app-2026-season.md`, `tests/api/test_meta_season.py`, `tests/api/test_season.py` | Tests: rename tests/api/test_season.py to test_meta_season.py (a basename clash stopped CI collection since 2026-11-10); decision log D-133 (restart recovery, run 9, Week 10 rehearsal) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | f43d69fc0712 | `docs/DECISIONS.md`, `tests/models/test_chain_ablation.py` | Tests: the ablation identity test pins one LightGBM thread for itself (D-108 fallback; CI runners have fewer cores than num_threads); decision log D-133 | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | ba67ea12c2b1 | `docs/DECISIONS.md`, `reports/evidence/reproducibility/2026w10_build_reproducibility.md` | Evidence: the weekly build at X is reproducible at a fixed build time; projections depend on the build time through depth chart age; decision log D-134 | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | 41e2dc0d0ba8 | `docs/status/phase5-qa-rehearsal.md`, `reports/evidence/rehearsal/MANIFEST.md`, `reports/evidence/rehearsal/run9_X4799913.json`, `reports/evidence/rehearsal/run9_X4799913_partial.jsonl`, `reports/evidence/rehearsal/run9_X4799913_pytest.txt`, `reports/livetest_final_rehearsal.md` | Rehearsal evidence: run 9 at X = 4799913 (base cfcfcd6) gives 44 passed, 1 skipped and run 7's verdicts and lines; report and status note filled in | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | 1b3de7a2f86b | `docs/DECISIONS.md`, `docs/status/phase4-evaluator-prefreeze-review.md` | Freeze review part 2 final: run 9 meets the bar, Freeze at X = 4799913: pass (findings F9-F11, none blocking); decision log D-135 (freeze approved) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |
| 2026-11-12 | 09c2702e7985 | `docs/status/livetest-preregistration-2026.md` | Freeze: fill the pre-registration's commit field with X = 47999132bba4b8ed1da1cc443f5517867900b0fb (D-120); pre-registration LF SHA-256 at this commit a6db14d811506facfcb9dafef7f5281c476d141eb8e53ea2c1c3da7e2637319e (F9) | paths outside the pinned build; no effect on commitments | orchestrator | n/a |

Verification for `ffml/data/` entries: the recomputed feature rows
(`player_week`, `team_week`, `dst_week`) for every already-committed week
are byte-identical to the stored ones; record the snapshot ids compared, the
row counts and the result. Entries under `scripts/`, `api/`, `web/` and
docs record "no effect on commitments".
