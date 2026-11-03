"""Inspect the production runtime or explicitly run optional optimization."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console

from fin_graph.safety.secrets import redact_secrets

app = typer.Typer(
    help="Inspect the active analysis harness or optimize it with frozen equity evals.",
    no_args_is_help=True,
)
console = Console()


@app.command("runtime")
def runtime_info() -> None:
    """Show the installed variant and effective analysis budgets without an LLM call."""
    from fin_graph.runtime.profile import resolve_analysis_profile

    try:
        console.print(json.dumps(resolve_analysis_profile().metadata()), markup=False)
    except (OSError, ValueError) as exc:
        _error(exc)


def _error(exc: Exception) -> None:
    if isinstance(exc, ImportError):
        console.print(
            "Install the optional harness dependencies: uv sync --extra harness", markup=False
        )
    else:
        console.print(redact_secrets(f"{type(exc).__name__}: {exc}"), markup=False)
    raise typer.Exit(2)


def _config_path(
    argument: Path | None,
    option: Path | None,
    *,
    cwd: Path | None = None,
) -> Path:
    """Resolve one explicit config or the checkout-only optimization default."""
    if argument is not None and option is not None:
        raise ValueError("pass the optimization config once: positionally or with --config")
    selected = argument if argument is not None else option
    if selected is not None:
        return selected
    default = (cwd or Path.cwd()) / "optimization" / "fin.toml"
    if default.is_file():
        return default
    raise ValueError("optimization config not found; pass its path with --config")


@app.command()
def validate(
    argument: Annotated[Path | None, typer.Argument(help="Optional config path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Optimization config path.")
    ] = None,
) -> None:
    """Check upstream configuration, corpus identity, splits and editable surfaces."""
    try:
        config_path = _config_path(argument, config)
        from fin_graph.optimization.harness.configuration import load_config

        loaded = load_config(config_path)
        console.print(
            json.dumps(
                {
                    "valid": True,
                    "cases": len(loaded.dataset.cases),
                    "splits": {
                        s: len(loaded.dataset.select(s)) for s in ("train", "holdout", "scorecard")
                    },
                    "fingerprint": loaded.dataset.fingerprint,
                    "surfaces": {
                        name: surface.kind for name, surface in loaded.experiment.surfaces.items()
                    },
                }
            ),
            markup=False,
        )
    except (ImportError, OSError, ValueError, KeyError) as exc:
        _error(exc)


@app.command()
def run(
    argument: Annotated[Path | None, typer.Argument(help="Optional config path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Optimization config path.")
    ] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    proposer_model: Annotated[str | None, typer.Option("--proposer-model")] = None,
    max_iterations: Annotated[int | None, typer.Option("--max-iterations", min=0, max=20)] = None,
    repeat: Annotated[int | None, typer.Option("--repeat", min=1, max=20)] = None,
) -> None:
    """Run baseline and optimize; use --max-iterations 0 for baseline only."""
    try:
        config_path = _config_path(argument, config)
        _run_experiment(
            config_path,
            output_dir,
            model,
            proposer_model,
            max_iterations,
            repeat,
        )
    except (ImportError, OSError, ValueError, KeyError) as exc:
        _error(exc)


def _run_experiment(
    config: Path,
    output_dir: Path | None,
    model: str | None,
    proposer_model: str | None,
    max_iterations: int | None,
    repeat: int | None,
) -> None:
    destination = output_dir or Path(".harness-runs") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    )
    from fin_graph.optimization.harness.experiment import run_experiment

    result = asyncio.run(
        run_experiment(
            config,
            destination,
            model=model,
            proposer_model=proposer_model,
            max_iterations=max_iterations,
            repeat=repeat,
            progress=lambda message: console.print(message, markup=False),
        )
    )
    console.print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "kind",
                    "quality_eligible",
                    "promotable",
                )
            }
        ),
        markup=False,
    )
    if result.get("error"):
        console.print(result["error"], markup=False)
    console.print(f"Artifacts: {destination.resolve()}", markup=False)
    if result["status"] != "completed":
        raise typer.Exit(1)


@app.command()
def smoke(
    argument: Annotated[Path | None, typer.Argument(help="Optional config path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Optimization config path.")
    ] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
) -> None:
    """Exercise all 16 equity cases with local fixtures; no model quality is measured."""
    try:
        _run_experiment(_config_path(argument, config), output_dir, "scripted", None, 0, 1)
    except (ImportError, OSError, ValueError, KeyError) as exc:
        _error(exc)


@app.command("inspect")
def inspect_command(directory: Annotated[Path, typer.Argument()]) -> None:
    """Show qualification, split metrics, failures and provenance of a run."""
    try:
        from fin_graph.optimization.harness.artifacts import inspect_run

        console.print(json.dumps(inspect_run(directory), indent=2), markup=False)
    except (ImportError, OSError, ValueError, KeyError) as exc:
        _error(exc)


@app.command("export")
def export_command(
    directory: Annotated[Path, typer.Argument()],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Export source files and a diff for review; does not adopt or deploy them."""
    try:
        from fin_graph.optimization.harness.artifacts import export_variant

        console.print(json.dumps(export_variant(directory, output_dir), indent=2), markup=False)
    except (ImportError, OSError, ValueError, KeyError) as exc:
        _error(exc)


@app.command("contract")
def contract_command(scenario: Annotated[str, typer.Argument()]) -> None:
    """Show the fixed tool, argument, agent and ordering requirements of an equity case."""
    try:
        from fin_graph.evals.market.scenarios import load_equity_case
        from fin_graph.evals.market.trajectory import contract_for

        value = contract_for(load_equity_case(scenario))
        console.print(value.model_dump_json(indent=2), markup=False)
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        _error(exc)
