from __future__ import annotations

import json

from typer.testing import CliRunner

from fin_graph.cli.main import app

runner = CliRunner()


def test_compatibility_commands_are_first_class_cli_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "backtest", "models", "algorithms", "event-study", "app"):
        assert command in result.stdout


def test_models_is_machine_readable_json() -> None:
    result = runner.invoke(app, ["models", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "pead" in payload["alpha_models"]
    assert "buffett" in payload["alpha_models"]


def test_algorithms_is_machine_readable_json() -> None:
    result = runner.invoke(app, ["algorithms", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {item["key"] for item in payload["algorithms"]} == {
        "momentum",
        "rsi_mean_reversion",
        "sma_crossover",
    }


def test_run_accepts_reference_aliases_and_rejects_bad_dates() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--ticker",
            "AAPL",
            "--start-date",
            "not-a-date",
            "--analysts",
            "warren_buffett",
            "--initial-capital",
            "10000",
            "--model",
            "gpt-4.1",
        ],
    )

    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_compatibility_command_help_has_reference_flags() -> None:
    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    for flag in (
        "--tickers",
        "--ticker",
        "--analysts",
        "--analysts-all",
        "--model",
        "--start-date",
        "--end-date",
        "--initial-cash",
        "--initial-capital",
        "--margin-requirement",
        "--show-reasoning",
    ):
        assert flag in result.stdout
