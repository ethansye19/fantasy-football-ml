import pytest
from simtoy import toy_dst_params, toy_player_params, toy_team_params


@pytest.fixture
def toy():
    """Two-team toy game: team params, player params, DST params."""
    return toy_team_params(), toy_player_params(), toy_dst_params()
