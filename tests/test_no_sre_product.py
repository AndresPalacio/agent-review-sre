"""The distributable product contains finance only, never the former SRE domain."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from fin_graph.cli.main import app

ROOT = Path(__file__).parents[1]
FORBIDDEN_PATHS = (
    "src/fin_graph/domain/alerts",
    "src/fin_graph/domain/diagnosis.py",
    "src/fin_graph/domain/investigation.py",
    "src/fin_graph/domain/remediation.py",
    "src/fin_graph/domain/remediation_templates.py",
    "src/fin_graph/domain/report.py",
    "src/fin_graph/domain/taxonomy.py",
    "src/fin_graph/domain/verification.py",
    "src/fin_graph/graphs/investigation",
    "src/fin_graph/graphs/remediation",
    "src/fin_graph/evals/data/eks",
    "src/fin_graph/evals/data/rds_postgres",
    "src/fin_graph/evals/fixtures.py",
    "src/fin_graph/evals/recording.py",
    "src/fin_graph/evals/runner.py",
    "src/fin_graph/evals/scenarios.py",
    "src/fin_graph/evals/scoring.py",
    "src/fin_graph/evals/trace.py",
    "src/fin_graph/evals/trajectory.py",
    "src/fin_graph/evals/comparison.py",
    "src/fin_graph/cli/eval_tools.py",
    "src/fin_graph/memory/service.py",
    "src/fin_graph/runbooks",
)
FORBIDDEN_INTEGRATIONS = (
    "alertmanager",
    "aws",
    "datadog",
    "github",
    "grafana",
    "kubernetes",
    "loki",
    "newrelic",
    "prometheus",
    "sentry",
    "tempo",
    "wave2",
)
RETAINED_IMPORTS = (
    "fin_graph.cli.main",
    "fin_graph.delivery.service",
    "fin_graph.gateway.slack",
    "fin_graph.graphs.analysis.graph",
    "fin_graph.harness.runtime",
    "fin_graph.integrations.broker.paper.runtime",
    "fin_graph.integrations.financial_datasets.integration",
    "fin_graph.integrations.mcp.runtime",
    "fin_graph.integrations.yahoo.integration",
    "fin_graph.scheduler.service",
    "fin_graph.server.app",
)


def test_obsolete_sre_source_is_absent() -> None:
    forbidden = [*FORBIDDEN_PATHS]
    forbidden.extend(f"src/fin_graph/integrations/{name}" for name in FORBIDDEN_INTEGRATIONS)

    def contains_product_file(relative: str) -> bool:
        path = ROOT / relative
        return path.is_file() or (
            path.is_dir()
            and any(item.is_file() for item in path.rglob("*") if "__pycache__" not in item.parts)
        )

    assert [path for path in forbidden if contains_product_file(path)] == []


def test_cli_exposes_finance_without_sre_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "investigate" not in result.stdout.lower()
    assert "sre:" not in result.stdout.lower()
    for command in ("analyze", "ask", "chat", "portfolio", "backtest", "serve"):
        assert command in result.stdout


def test_base_dependencies_drop_sre_only_sdks() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = "\n".join(project["dependencies"]).lower()
    assert "kubernetes" not in dependencies
    assert "boto3" not in dependencies


def test_financial_product_surfaces_remain_importable() -> None:
    for module in RETAINED_IMPORTS:
        importlib.import_module(module)
