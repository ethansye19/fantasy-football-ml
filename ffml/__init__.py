"""ffml: football-first fantasy projections pipeline.

Subpackages:
- data: ingestion, snapshot store, Sleeper client, validation
- features: point-in-time feature builders
- models: baselines, stage models, calibration
- sim: Monte Carlo stat-line simulation
- scoring: league scoring rules engine
- eval: metrics, audits, backtest reporting
"""

from ffml.config import ensure_utf8

ensure_utf8()

__version__ = "0.1.0"
