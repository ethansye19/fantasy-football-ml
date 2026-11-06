# Live-test pass-rate reconciliation (orchestrator, 2026-11-05; for Evaluator verification)

Question from the owner: the first report said about 77% (63% corrected) for a good model over 18 weeks; the
pre-registration says 55.7%. Which is right, and what changed?

## Runs (R0 = the 22 lines; 5,000 replicates each; `scripts` in the session scratchpad; outputs in data/reports/)

| Window | Games (mean) | A (uncorrected) | A_vc (corrected) | No-edge (N0_vc) | L01 / L05 / L09 under A_vc |
| --- | --- | --- | --- | --- | --- |
| iii_full_1_18: weeks 1-18, full slates (reference) | 271.7 | 0.766 | 0.620 | 0 of 5,000 | 0.837 / 0.933 / 0.933 |
| ext18_2026counts: 18 weeks: 2026 weeks 10-18 (2026 game counts) plus 2027 weeks 1-9 (full slates) | 269.7 | 0.722 | 0.557 | 0 of 5,000 | 0.781 / 0.892 / 0.881 |
| ext18_full_slates: 2026 w10-18 + 2027 w1-9, full slates everywhere | 271.6 | 0.722 | 0.568 | 0 of 5,000 | 0.779 / 0.906 / 0.889 |

## Isolation (4,000 replicates each; full slates; only the week order, the slate-draw seed index and the bootstrap seed vary)

| Run | Week order | Slate-draw seed index | Bootstrap seed | A | A_vc |
| --- | --- | --- | --- | --- | --- |
| E0a | 1..18 | 2 | 0 | 0.763 | 0.618 |
| E0b | 1..18 | 3 | 0 | 0.720 | 0.564 |
| E1 | 1..18 | 3 | 0 | 0.758 | 0.610 |
| E2 | 1..18 | 2 | 0 | 0.736 | 0.573 |
| E3 | 1..18 | 3 | 1 | 0.763 | 0.608 |
| E4 | 1..18 | 2 | 1 | 0.746 | 0.587 |
| E5 | 1..18 | 3 | 2 | 0.756 | 0.610 |
| E6 | 1..18 | 2 | 2 | 0.750 | 0.591 |

## Reading

- The first report's 18-week figure (0.767 / 0.628) reproduces under the current code for its own window (weeks 1-18, calendar order): 0.766 / 0.620. Nothing in the code path changed.
- The fallback window (2026 w10-18 then 2027 w1-9) gives 0.722 / 0.557. Game counts are equal (271.7 vs 269.7) and full slates in that order give 0.568, so thinning is not the cause.
- With bootstrap seed 0 the week order alone moves A_vc by about 5 points (E1 vs E2: 0.610 vs 0.573, same slate seed). With bootstrap seeds 1 and 2 the two orders give 0.587-0.610. The frozen 500-draw bootstrap is a fixed weight matrix; its pairing with calendar positions is the cause.
- The test scores clusters in calendar order with the frozen bootstrap, so 0.557 is the planning number. No rule is changed on this evidence (owner instruction); the sensitivity is disclosed in the pre-registration.
- No-edge model: 0 passes in 5,000 replicates in every run; reported as an estimate of 0 with a 95% upper bound of 0.0006, never as exactly zero.
