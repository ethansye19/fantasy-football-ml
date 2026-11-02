"""Structural model chain: football outcomes first, fantasy points last (MODELING.md 3-5)."""

from ffml.models.chain.model import ChainModel, load_selection, resolve_choices
from ffml.models.chain.stages import STAGES, StageSpec

__all__ = ["STAGES", "ChainModel", "StageSpec", "load_selection", "resolve_choices"]
