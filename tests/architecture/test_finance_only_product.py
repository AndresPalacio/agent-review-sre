"""Executable boundary that keeps the distributable product finance-only."""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin_graph.cli.main import app
from fin_graph.domain.tools import EvidenceType

ROOT = Path(__file__).parents[2]

OPERATIONAL_PATHS = (
    "projects",
    "src/fin_graph/domain/alerts",
    "src/fin_graph/domain/diagnosis.py",
    "src/fin_graph/domain/investigation.py",
    "src/fin_graph/domain/remediation.py",
    "src/fin_graph/domain/remediation_templates.py",
    "src/fin_graph/domain/report.py",
    "src/fin_graph/domain/taxonomy.py",
    "src/fin_graph/domain/verification.py",
    "src/fin_graph/cli/eval_tools.py",
    "src/fin_graph/evals/data/eks",
    "src/fin_graph/evals/data/rds_upstream",
    "src/fin_graph/evals/comparison.py",
    "src/fin_graph/evals/fixtures.py",
    "src/fin_graph/evals/recording.py",
    "src/fin_graph/evals/runner.py",
    "src/fin_graph/evals/scenarios.py",
    "src/fin_graph/evals/scoring.py",
    "src/fin_graph/evals/trace.py",
    "src/fin_graph/evals/trajectory.py",
    "src/fin_graph/graphs/investigation",
    "src/fin_graph/graphs/remediation",
    "src/fin_graph/integrations/alertmanager",
    "src/fin_graph/integrations/aws",
    "src/fin_graph/integrations/cloudwatch",
    "src/fin_graph/integrations/datadog",
    "src/fin_graph/integrations/eks",
    "src/fin_graph/integrations/github",
    "src/fin_graph/integrations/grafana",
    "src/fin_graph/integrations/kubernetes",
    "src/fin_graph/integrations/loki",
    "src/fin_graph/integrations/newrelic",
    "src/fin_graph/integrations/opgenie",
    "src/fin_graph/integrations/pagerduty",
    "src/fin_graph/integrations/prometheus",
    "src/fin_graph/integrations/rds",
    "src/fin_graph/integrations/sentry",
    "src/fin_graph/integrations/tempo",
    "src/fin_graph/integrations/wave2",
    "src/fin_graph/memory/service.py",
    "src/fin_graph/runbooks",
    "web/src/InvestigationContent.css",
    "web/src/InvestigationContent.test.tsx",
    "web/src/InvestigationContent.tsx",
)

FINANCIAL_IMPORTS = (
    "fin_graph.agents.catalog",
    "fin_graph.agents.analysis.persona_team",
    "fin_graph.runtime.analysis",
    "fin_graph.trading.order_desk",
    "fin_graph.optimization.harness",
    "fin_graph.server.app",
)

OPTIONAL_FINANCIAL_IMPORTS = ("fin_graph.optimization.harness.runner",)

FINANCIAL_EVIDENCE_TYPES = {
    "prices",
    "financials",
    "news",
    "insider",
    "valuation_model",
    "indicators",
    "positions",
}

OPERATIONAL_TERM = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"aws|cloudwatch|eks|rds|kubernetes|pagerduty|datadog|grafana|prometheus|"
    r"alertmanager|remediation|remediaci[oó]n|incidents?|incidentes?|"
    r"causa\s+ra[ií]z|sre"
    r")(?![a-z0-9])"
)

TEXT_SUFFIXES = {
    ".bat",
    ".css",
    ".example",
    ".html",
    ".ini",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".ps1",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

ALLOWED_TEXT_PATHS = {
    "docs/superpowers/plans/2026-11-03-finance-only-cleanup.md",
    "docs/superpowers/specs/2026-11-03-finance-only-cleanup-design.md",
    "src/fin_graph/safety/secrets.py",
    "src/fin_graph/skills/finance/edgar-sec-filings/SKILL.md",
}

PRODUCT_TEXT_ROOTS = (
    ".github",
    ".run",
    "app",
    "docs",
    "evals",
    "examples",
    "optimization",
    "packages/workflow/examples",
    "packages/workflow/src",
    "scripts",
    "src",
    "web/src",
)

PRODUCT_TEXT_FILES = (
    ".env.example",
    "Dockerfile",
    "NOTICE",
    "README.md",
    "conftest.py",
    "docker-compose.yml",
    "hatch_build.py",
    "langgraph.json",
    "mcp-servers.example.json",
    "packages/workflow/README.md",
    "packages/workflow/pyproject.toml",
    "pyproject.toml",
    "web/index.html",
    "web/package.json",
)

DEPENDENCY_LOCKS = ("uv.lock", "packages/workflow/uv.lock", "web/package-lock.json")


def _product_text_paths() -> list[Path]:
    paths = [ROOT / relative for relative in PRODUCT_TEXT_FILES]
    for relative in PRODUCT_TEXT_ROOTS:
        root = ROOT / relative
        paths.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and ".test." not in path.name
            and (
                path.suffix.lower() in TEXT_SUFFIXES
                or path.name in {"Dockerfile", "NOTICE", "README"}
            )
        )
    return sorted(set(paths))


def _is_allowed_financial_text(relative: str) -> bool:
    return (
        relative in ALLOWED_TEXT_PATHS
        or (
            relative.startswith("src/fin_graph/evals/data/equities/")
            and relative.endswith("/news.json")
        )
    )


def test_operational_product_paths_are_absent() -> None:
    assert [relative for relative in OPERATIONAL_PATHS if (ROOT / relative).exists()] == []


def test_dependencies_contain_no_operational_sdks() -> None:
    project_file = ROOT / "pyproject.toml"
    tomllib.loads(project_file.read_text(encoding="utf-8"))
    normalized = project_file.read_text(encoding="utf-8").casefold()
    assert not [name for name in ("boto3", "botocore", "kubernetes") if name in normalized]

    for relative in DEPENDENCY_LOCKS:
        lock = (ROOT / relative).read_text(encoding="utf-8").casefold()
        assert not [name for name in ("boto3", "botocore", "kubernetes") if name in lock]


def test_evidence_contract_is_exactly_financial() -> None:
    assert {item.value for item in EvidenceType} == FINANCIAL_EVIDENCE_TYPES


def test_cli_exposes_finance_without_operational_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout.casefold()
    assert "investigate" not in output
    assert "remediation" not in output
    for command in ("agents", "analyze", "ask", "chat", "portfolio", "backtest", "serve"):
        assert command in output


def test_http_routes_expose_no_operational_endpoints() -> None:
    from fin_graph.server.app import create_app

    paths = {path.casefold() for path in create_app().openapi()["paths"]}
    forbidden_fragments = (
        "/alerts",
        "/diagnosis",
        "/incidents",
        "/investigate",
        "/remediation",
        "/runbooks",
    )
    assert not [
        path for path in paths if any(fragment in path for fragment in forbidden_fragments)
    ]


def test_financial_product_surfaces_remain_importable() -> None:
    for module in FINANCIAL_IMPORTS:
        assert importlib.import_module(module).__name__ == module


def test_optional_harness_surface_is_importable_when_installed() -> None:
    pytest.importorskip("better_harness")
    for module in OPTIONAL_FINANCIAL_IMPORTS:
        assert importlib.import_module(module).__name__ == module


def test_product_text_boundary_covers_configuration_scripts_and_build_files() -> None:
    covered = {path.relative_to(ROOT).as_posix() for path in _product_text_paths()}
    for relative in (
        ".env.example",
        "Dockerfile",
        "app/run.sh",
        "scripts/build_equity_scenarios.py",
    ):
        assert relative in covered


def test_product_text_has_no_operational_vocabulary() -> None:
    findings: dict[str, list[str]] = {}
    for path in _product_text_paths():
        relative = path.relative_to(ROOT).as_posix()
        if _is_allowed_financial_text(relative):
            continue
        text = path.read_text(encoding="utf-8")
        matches = sorted({match.group(0) for match in OPERATIONAL_TERM.finditer(text)})
        if matches:
            findings[relative] = matches
    assert findings == {}
