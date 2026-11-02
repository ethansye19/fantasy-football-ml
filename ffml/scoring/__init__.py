"""Scoring engine: stat-line vocabulary, league rules, and actual outcomes (D-003, D-048)."""

from ffml.scoring.rules import (
    PRESETS,
    SUPPORTED_KEYS,
    ScoringRules,
    UnsupportedScoringKeyWarning,
)
from ffml.scoring.statline import KEY_COLUMNS, POSITIONS, STAT_COLUMNS

__all__ = [
    "KEY_COLUMNS",
    "POSITIONS",
    "PRESETS",
    "STAT_COLUMNS",
    "SUPPORTED_KEYS",
    "ScoringRules",
    "UnsupportedScoringKeyWarning",
]
