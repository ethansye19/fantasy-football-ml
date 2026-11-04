"""Command-line entry point: ``ffml <command>``.

Commands are thin wrappers around pipeline modules so that the scheduler, CI,
and humans all run the same code paths.
"""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.logging import RichHandler

from ffml.config import get_settings

app = typer.Typer(
    name="ffml",
    help="Football-first fantasy projections pipeline.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _setup_logging(level: str | None = None) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=(level or settings.log_level).upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
        force=True,
    )


def _parse_seasons(spec: str) -> list[int]:
    """Parse '2016-2026' or '2024,2025' or '2026' into a sorted list of seasons."""
    seasons: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            seasons.update(range(int(lo), int(hi) + 1))
        else:
            seasons.add(int(part))
    return sorted(seasons)


@app.callback()
def main(log_level: str = typer.Option(None, "--log-level", help="DEBUG, INFO, WARNING")) -> None:
    _setup_logging(log_level)


@app.command()
def info() -> None:
    """Show resolved settings and the current season/week."""
    settings = get_settings()
    console.print({"data_dir": str(settings.data_dir), "artifacts_dir": str(settings.artifacts_dir)})
    try:
        from ffml.season import describe_current_state

        console.print(describe_current_state())
    except ImportError:
        console.print("[yellow]season resolver not implemented yet[/yellow]")


@app.command()
def ingest(
    seasons: str = typer.Option("2016-2026", help="Season range, e.g. 2016-2026 or 2025,2026"),
    datasets: str = typer.Option("all", help="Comma-separated dataset names or 'all'"),
) -> None:
    """Download nflverse datasets into the raw snapshot store."""
    from ffml.data.ingest import run_ingest

    run_ingest(seasons=_parse_seasons(seasons), datasets=None if datasets == "all" else datasets.split(","))


@app.command()
def validate(
    seasons: str = typer.Option("2016-2026"),
    report: str = typer.Option("", help="Optional path for the markdown report"),
) -> None:
    """Run the data validation suite against the latest snapshots; exit non-zero on failure."""
    from ffml.data.validate import run_validation

    ok = run_validation(seasons=_parse_seasons(seasons), report_path=report or None)
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def process(
    seasons: str = typer.Option("2016-2026"),
    tables: str = typer.Option("all", help="Comma-separated processed table names or 'all'"),
) -> None:
    """Build processed tables (dim_*, fact_*) from the latest raw snapshots."""
    from ffml.data.process import run_process

    run_process(
        seasons=_parse_seasons(seasons),
        tables=None if tables == "all" else [t.strip() for t in tables.split(",") if t.strip()],
    )


@app.command()
def warehouse() -> None:
    """Rebuild the DuckDB warehouse views over the latest snapshots."""
    from ffml.data.warehouse import rebuild_warehouse

    path = rebuild_warehouse()
    console.print(f"warehouse written to {path}")


@app.command()
def dictionary() -> None:
    """Regenerate docs/DATA_DICTIONARY.md from the latest processed snapshots."""
    from ffml.data.dictionary import write_data_dictionary

    path = write_data_dictionary()
    console.print(f"data dictionary written to {path}")


@app.command()
def features(seasons: str = typer.Option("2016-2026")) -> None:
    """Build point-in-time feature sets."""
    from ffml.features.build import run_features

    run_features(seasons=_parse_seasons(seasons))


@app.command()
def train(config: str = typer.Option("default", help="Training configuration name")) -> None:
    """Train stage models with the walk-forward protocol."""
    from ffml.models.train import run_train

    run_train(config_name=config)


@app.command()
def backtest(
    config: str = typer.Option("default", help="Config name in ffml/eval/backtest_configs.py"),
    run_id: str = typer.Option(
        None, "--run-id", help="Resume or name a run (default $FFML_BACKTEST_RUN_ID or <config>_<date>)"
    ),
    n_jobs: int = typer.Option(None, "--n-jobs", help="Worker processes, one model per worker"),
) -> None:
    """Run walk-forward backtests (resumable) and write data/backtests/<run_id>/."""
    from ffml.eval.harness import run_backtest

    path = run_backtest(config_name=config, run_id=run_id, n_jobs=n_jobs)
    console.print(f"backtest written to {path}")


@app.command()
def predict(
    season: int = typer.Option(None),
    week: int = typer.Option(None),
    ros: bool = typer.Option(True, help="Also produce rest-of-season weeks"),
    private_test: bool = typer.Option(
        False,
        "--private-test",
        help="Private test of a registry candidate, labeled 'not approved', written only to "
        "the private predictions store (D-099)",
    ),
    model_version: str = typer.Option(
        None, "--model-version", help="Registry candidate version (with --private-test)"
    ),
    model_dir: str = typer.Option(
        None,
        "--model-dir",
        help="Artifact directory; only with --private-test and the registry path of "
        "--model-version",
    ),
) -> None:
    """Produce the predictions snapshot (simulated stat lines) for a week.

    Live (default) uses only the registry's live, approved version and exits
    with code 2 when there is none (D-097, D-099).
    """
    from ffml.sim.predict import (
        LivePredictionBlocked,
        NoApprovedModel,
        PredictionRefused,
        run_predict,
    )

    try:
        run_predict(
            season=season,
            week=week,
            rest_of_season=ros,
            model_dir=model_dir,
            private_test=private_test,
            model_version=model_version,
        )
    except NoApprovedModel as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from None
    except LivePredictionBlocked as exc:
        console.print(f"[yellow]prediction blocked:[/yellow] {exc}")
        raise typer.Exit(code=2) from None
    except PredictionRefused as exc:
        typer.echo(f"prediction refused: {exc}")
        raise typer.Exit(code=2) from None


@app.command()
def refresh(step: str = typer.Option("all", help="all | data | injuries | retrain-check | predict")) -> None:
    """Scheduled refresh entry point used by the scheduler and CI."""
    from ffml.refresh import run_refresh

    run_refresh(step=step)


if __name__ == "__main__":
    app()
