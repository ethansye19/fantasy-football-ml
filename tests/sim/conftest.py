import json

import pytest
from regtoy import SYNTHETIC_VERSION
from simtoy import toy_dst_params, toy_player_params, toy_team_params


@pytest.fixture
def toy():
    """Two-team toy game: team params, player params, DST params."""
    return toy_team_params(), toy_player_params(), toy_dst_params()


@pytest.fixture(scope="session")
def synthetic_model_dir(tmp_path_factory):
    """A small chain fitted on synthetic frames at cutoff (2017, 10), saved with a version."""
    from ffml.models.chain import ChainModel
    from ffml.models.chain.data import ChainData
    from ffml.models.chain.synthetic import synthetic_frames

    data = ChainData(frames=synthetic_frames())
    model = ChainModel(stage_choice="structural", n_sims=200, seed=4, data=data).fit((2017, 10))
    path = tmp_path_factory.mktemp("chain") / "chain_v1-synthetic"
    model.save(path)
    (path / "train_manifest.json").write_text(
        json.dumps({"model_version": SYNTHETIC_VERSION}), encoding="utf-8"
    )
    return path, data


@pytest.fixture
def registry_env(tmp_settings, tmp_path, monkeypatch):
    """Temporary data, artifacts (registry, access log) and reports dirs; holdout locked."""
    from ffml.config import get_settings, reset_settings_cache

    monkeypatch.setenv("FFML_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.delenv("FFML_HOLDOUT_UNLOCK", raising=False)
    reset_settings_cache()
    settings = get_settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    yield settings
    reset_settings_cache()


@pytest.fixture
def cli():
    """Run ``ffml`` through Typer's test runner; the root logging setup is restored afterwards."""
    import logging

    from typer.testing import CliRunner

    from ffml.cli import app

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    runner = CliRunner()
    yield lambda args: runner.invoke(app, args)
    root.handlers[:] = handlers
    root.setLevel(level)
