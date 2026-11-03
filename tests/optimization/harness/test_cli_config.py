from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin_graph.cli.main import app

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "optimization" / "fin.toml"
runner = CliRunner()


def test_validate_accepts_positional_and_config_option() -> None:
    positional = runner.invoke(app, ["harness", "validate", str(CONFIG)])
    option = runner.invoke(app, ["harness", "validate", "--config", str(CONFIG)])

    assert positional.exit_code == 0, positional.output
    assert option.exit_code == 0, option.output


def test_validate_rejects_two_config_paths() -> None:
    result = runner.invoke(
        app, ["harness", "validate", str(CONFIG), "--config", str(CONFIG)]
    )

    assert result.exit_code == 2
    assert "config" in result.output.lower()


def test_validate_without_checkout_config_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["harness", "validate"])

    assert result.exit_code == 2
    assert "--config" in result.output
