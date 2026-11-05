# Gate 3 (Models): FAILED

Date: 2026-11-04 (UTC). Decision: D-097. Earlier evidence:
docs/status/gate-3-pre-holdout.md. Pre-registration:
docs/status/holdout-preregistration.md.

## Result

The one-time 2025 holdout session evaluated candidate
`chain_v1-primary-c2024w19-20261102T222634Z` (`chain_v1+cal`, frozen
calibration).

- Verdict: **NOT APPROVED**.
- Forward check (2026 weeks 1-7): **PASS**.
- Report: `reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md`
  (commit 824162d), with an addendum covering the D-093 rerun reason and a
  table-header erratum.

| Checklist item | Evidence | Result |
| --- | --- | --- |
| Leakage suite green | evaluator audits gate3, gate3b, gate3c | pass |
| Holdout evaluated exactly once | access log: 85 lines, all `holdout_eval`, git 294aac0, 17:07:08 to 18:37:59 UTC, longest gap 18.4 min; one D-093 infrastructure rerun before any metric was read | pass |
| Pre-registration integrity | 21 integrity checks for 2025 and 20 for the forward check, all PASS (commit, calibrator hashes, snapshots, seeds, lines hash, calibration mode, access log) | pass |
| Accuracy lines L01-L12 (beats three baselines, QB-TE, MAE and pinball) | 12 of 12 PASS on 2025 and on 2026 weeks 1-7 | pass |
| Start/sit lines L13-L16 (not worse than consensus) | 4 of 4 PASS on both | pass |
| Coverage lines L17-L22 (tie-aware, [0.75, 0.85]) | 2025: QB **0.739 FAIL**, RB 0.774, WR 0.793, TE 0.826, K 0.798, DST 0.800. 2026 weeks 1-7: QB 0.774, RB 0.782, WR 0.783, TE 0.807, K 0.776, DST 0.827 | **fail** |
| Approved version for Week 9 | none: `release_holdout` and `approve` not called; `ffml predict` stays blocked | **fail** |

## What the 2025 numbers say

- **QB ranges are too narrow.** Outcomes fall below q10 13.5% of the time
  and above q90 11.6% of the time, against 10% each. Coverage is 0.693 raw
  and 0.739 after the frozen calibrator; it was 0.779 in validation.
- **Against FantasyPros consensus** (same player-weeks, calibrated MAE,
  chain minus consensus): the chain is worse at QB, +0.263 [+0.099, +0.418],
  and worse at RB, +0.086 [+0.000, +0.172]. There is no detectable
  difference at WR (-0.014), TE (-0.025), K (+0.050) or DST (-0.021). The
  chain does not beat the consensus anywhere on MAE.
- **Start/sit against consensus:** ties at QB (+0.001), RB (-0.007) and WR
  (-0.018); better at TE, +0.025 [+0.002, +0.047].
- **Against the transparent baselines:** the chain beats rolling_avg_4,
  prior_season and rolling_median_8 on MAE and every pinball loss at QB, RB,
  WR and TE. Matched-row PPR MAE, chain against rolling_median_8: QB
  6.13/7.02, RB 4.30/4.54, WR 3.94/4.24, TE 2.86/3.16, K 3.72/3.87, DST
  4.33/4.83.
- **Boom/bust:** slopes are within the rule at RB, WR and TE. QB is now low
  confidence (boom 0.56, bust 0.77), alongside the K and DST flags (D-089).
- **Thursday split:** no detectable difference between early-game and
  matched player-weeks (pooled +0.04 [-0.19, +0.23]).

## What happens next

Phase 4 does not start, and no projections are published as approved. The
owner is asked how the model track continues and whether app work may begin
with projections switched off (D-097, open question).

## Erratum (2026-11-05): line endings and documented hashes

`core.autocrlf=true` on the development machine checks tracked text files
out with CRLF, so a SHA-256 of a working-copy file differs from the SHA-256
of the same file as git and CI store it (LF). The pre-registration's
documented `selection.json` SHA-256 `d6c1891828657b76...` is the CRLF form;
the LF form is `75477b2b97bc7d9b...`. The holdout integrity checks hashed
only git-ignored artifact files (FROZEN.json and the calibrators), which
have no line-ending conversion, so the holdout verdict is unaffected. From
chain_v2 on, pins of tracked text files are computed over LF-normalized
bytes so they are identical on every platform.
