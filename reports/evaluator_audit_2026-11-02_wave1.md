# Evaluator audit

Overall: **INCOMPLETE**

Some checks were skipped because their inputs do not exist yet; a gate needs every check run.

- Holdout audit (season 2025): **PASS**
- Leakage audit: **SKIP**

## Holdout audit (season 2025)

Overall: **PASS** (PASS 4, WARN 1). Run at 2026-11-02T20:19:16+00:00 on the working tree at git `e5f0b5d`.

| check | status | detail |
| --- | --- | --- |
| holdout access log | PASS | artifacts/holdout_access.log absent: no holdout access recorded |
| literal holdout season | WARN | 2025 appears only in docstrings or comments (3; 29 files under ffml/features, ffml/models, ffml/sim, ffml/scoring, ffml/eval) |
| unlock assignments | PASS | none outside tests and the guard (51 files scanned) |
| .env unlock | PASS | .env absent |
| process environment | PASS | FFML_HOLDOUT_UNLOCK not set |

### literal holdout season (WARN)

- ffml/features/access.py:4 (docstring)
- ffml/features/context.py:187 (docstring)
- ffml/models/baselines.py:378 (docstring)

## Leakage audit

Overall: **SKIP** (SKIP 3). Run at 2026-11-02T20:19:16+00:00 on the working tree at git `e5f0b5d`.

| check | status | detail |
| --- | --- | --- |
| leakage suite | SKIP | tests/features/test_leakage.py not present yet (Feature Engineer, FE-3.2) |
| timestamp audit | SKIP | no player_week feature dataset under data/features yet (Feature Engineer, FE-3.1) |
| shuffle probe | SKIP | no player_week feature dataset under data/features yet (Feature Engineer, FE-3.1) |
