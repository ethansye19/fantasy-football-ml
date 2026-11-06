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

## Evaluator verification (2026-11-05)

Verified by the Independent Evaluator with an independent run
(`reconcile_verify.py` in the session scratchpad; output
`data/reports/live_test_power_reconcile_verify.json`): 3,000 replicates per
order, full slates, scenario A_vc, rule set R0, the **same slate stream for
both orders** (seed index 2), and three bootstrap treatments on each
replicate.

| Treatment | order 1..18 | order 10..18, 1..9 | gap (1..18 minus 10..9) | z |
| --- | --- | --- | --- | --- |
| frozen 500 draws, seed 0 (the binding rule) | 0.620 (SE 0.009) | 0.583 (SE 0.009) | +0.037 | 3.0 |
| 500 draws, a fresh bootstrap seed per replicate | 0.595 (SE 0.009) | 0.611 (SE 0.009) | -0.015 | -1.2 |
| 5,000 draws, seed 0 (the report-only sensitivity check) | 0.608 (SE 0.009) | 0.611 (SE 0.009) | -0.003 | -0.3 |

**Finding confirmed.** With the frozen weight matrix the two orders differ by
about four points on the same replicates; with the weights no longer fixed
(a fresh seed each replicate) or averaged out (5,000 draws) the orders agree
within Monte Carlo noise, at 0.59-0.61. The code path, the slate draws and
the thinning are the same in all three treatments, so none of them is the
cause; the gap is the fixed 500-draw, seed-0 weight matrix interacting with
which calendar position each week number occupies. The binding rule stays as
pre-registered (D-111); 0.557 remains the planning number for the test's own
order with 2026 game counts, and the 5,000-draw sensitivity check in the
final report is where this effect becomes visible if it matters on the real
window.

**Corrections to the isolation table above.** The run labels in
`live_test_power_reconcile2.json` show that E0b, E2, E3 and E5 used the
`10..18, 1..9` order (the table lists them as `1..18`); the pairs that share a
slate seed are E0a/E2 (index 2: 0.618 against 0.573) and E1/E0b (index 3:
0.610 against 0.564), not E1/E2. Corrected table:

| Run | Week order | Slate-draw seed index | Bootstrap seed | A | A_vc |
| --- | --- | --- | --- | --- | --- |
| E0a | 1..18 | 2 | 0 | 0.763 | 0.618 |
| E0b | 10..18, 1..9 | 3 | 0 | 0.720 | 0.564 |
| E1 | 1..18 | 3 | 0 | 0.758 | 0.610 |
| E2 | 10..18, 1..9 | 2 | 0 | 0.736 | 0.573 |
| E3 | 10..18, 1..9 | 3 | 1 | 0.763 | 0.608 |
| E4 | 1..18 | 2 | 1 | 0.746 | 0.587 |
| E5 | 10..18, 1..9 | 3 | 2 | 0.756 | 0.610 |
| E6 | 1..18 | 2 | 2 | 0.750 | 0.591 |

Read this way the evidence is stronger than the original reading: at
bootstrap seed 0 the `1..18` order is higher by 4-5 points for either slate
seed, and at seeds 1 and 2 the sign reverses (the `10..18, 1..9` order is
higher by about two points), which is what a seed-specific weight matrix
predicts and what an intrinsic order effect would not. The reading's
conclusion and the planning number are unchanged.

**`drop_n` (1476ac6).** Checked: `draw_window` removes `drop_n` picks drawn
without replacement after the slate and game draws (so the stream before the
drop equals the full window's), `drop_one` still means one, and
`MISS_WINDOWS` carries `ext18_two_miss` with its own seed index; test
`test_drop_n_windows_exclude_distinct_weeks` in `tests/eval/test_power.py`.
