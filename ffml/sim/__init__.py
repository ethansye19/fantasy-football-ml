"""Monte Carlo simulation: parameter contracts, engine, summaries, and snapshots."""

from ffml.sim.engine import SimulationResult, simulate, simulate_full
from ffml.sim.params import DstGameParams, PlayerGameParams, TeamGameParams, synthetic_slate
from ffml.sim.snapshot import read_latest_predictions, write_predictions_snapshot
from ffml.sim.summarize import BOOM_BUST_THRESHOLDS, prob_beats, summarize

__all__ = [
    "BOOM_BUST_THRESHOLDS",
    "DstGameParams",
    "PlayerGameParams",
    "SimulationResult",
    "TeamGameParams",
    "prob_beats",
    "read_latest_predictions",
    "simulate",
    "simulate_full",
    "summarize",
    "synthetic_slate",
    "write_predictions_snapshot",
]
