"""Public documentation is executable and describes only the shipped finance product."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values
from typer.testing import CliRunner

from fin_graph.cli.main import app

ROOT = Path(__file__).parents[2]
DOCS = (
    "docs/compatibility/ai-hedge-fund.md",
    "docs/compatibility/configuration-matrix.md",
    "docs/compatibility/api-manifest.md",
    "docs/operations/local-app.md",
    "docs/operations/remote-deployment.md",
)
FORBIDDEN_ENV_PREFIXES = (
    "K8S_",
    "PROMETHEUS_",
    "MIMIR_",
    "ALERTMANAGER_",
    "GRAFANA_",
    "LOKI_",
    "TEMPO_",
    "DATADOG_",
    "NEWRELIC_",
    "AWS_",
    "SENTRY_",
    "GITHUB_",
    "PAGERDUTY_",
    "OPSGENIE_",
    "INCIDENT_IO_",
    "FIN_HARNESS_",
)


def test_required_product_documents_exist_and_have_no_phase_status() -> None:
    for relative in DOCS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert len(text) > 200
        assert "Fase " not in text
        assert "TODO" not in text


def test_readme_describes_the_finished_paper_only_product() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "ai hedge fund" in lowered
    assert "paper trading" in lowered
    assert "no ejecuta operaciones reales" in lowered
    assert "migrando" not in lowered
    assert "sre" not in lowered
    assert "pagerduty" not in lowered
    assert "uv run fin-graph app" in text


def test_example_environment_contains_no_removed_product_prefix() -> None:
    values = dotenv_values(ROOT / ".env.example", encoding="utf-8")
    assert not [name for name in values if name.startswith(FORBIDDEN_ENV_PREFIXES)]
    assert values["LLM_PROVIDER"] == "ollama"
    assert values["PERSISTENCE_BACKEND"] == "sqlite"
    assert values["FIN_GRAPH_ALLOW_INSECURE_LEGACY_API"] == "true"


def test_every_documented_cli_surface_has_working_help() -> None:
    commands = (
        [],
        ["app"],
        ["run"],
        ["backtest"],
        ["models"],
        ["algorithms"],
        ["event-study"],
        ["analyze"],
        ["ask"],
        ["chat"],
        ["portfolio"],
        ["orders"],
        ["schedules"],
        ["integrations"],
        ["eval"],
        ["harness"],
        ["agents"],
    )
    runner = CliRunner()
    for command in commands:
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0, (command, result.output)


def test_api_manifest_names_both_compatibility_and_native_routes() -> None:
    text = (ROOT / "docs/compatibility/api-manifest.md").read_text(encoding="utf-8")
    for route in (
        "/flows/",
        "/hedge-fund/run",
        "/v2/backtests",
        "/language-models/",
        "/v1/analyses",
        "/hedge-fund/backtest",
        "/v1/portfolio",
        "/v1/orders",
        "/v1/api-keys",
    ):
        assert route in text
