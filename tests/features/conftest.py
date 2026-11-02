from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from ffml.features.build import build_all
from ffml.features.data import FeatureContext

FEATURE_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "features"


@pytest.fixture(scope="session")
def fixture_ctx() -> FeatureContext:
    """KC and DET, 2022 plus 2023 weeks 1-6 (see tests/fixtures/features/make_fixture.py)."""
    return FeatureContext.from_dir(FEATURE_FIXTURES)


@pytest.fixture(scope="session")
def fixture_build(fixture_ctx: FeatureContext) -> dict[str, pl.DataFrame]:
    return build_all(fixture_ctx)


def week_cutoff(ctx: FeatureContext, season: int, week: int):
    cal = ctx.calendar.filter(pl.col("season") == season, pl.col("week") == week)
    return cal["cutoff_ts"][0]


@pytest.fixture(scope="session")
def cutoff_of(fixture_ctx: FeatureContext):
    """``cutoff_of(season, week)``: first kickoff of that week in the fixture schedule."""
    return lambda season, week: week_cutoff(fixture_ctx, season, week)
