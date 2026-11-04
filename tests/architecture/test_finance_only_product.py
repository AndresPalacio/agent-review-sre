"""Executable boundary that keeps the distributable product finance-only."""

from __future__ import annotations

import importlib
import re
import subprocess
import tomllib
from pathlib import Path

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
    "src/fin_graph/evals/data/eks",
    "src/fin_graph/evals/data/rds_upstream",
    "src/fin_graph/graphs/investigation",
    "src/fin_graph/graphs/remediation",
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
    "fin_graph.optimization.harness.runner",
    "fin_graph.server.app",
)

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
    r"alertmanager|remediation|incidents?|sre"
    r")(?![a-z0-9])"
)

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".json",
    ".md",
    ".py",
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
    "tests/architecture/test_finance_only_product.py",
    "tests/safety/test_secrets.py",
}


def _tracked_text_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [
        ROOT / relative
        for relative in result.stdout.split("\0")
        if relative
        and (ROOT / relative).is_file()
        and (
            Path(relative).suffix.lower() in TEXT_SUFFIXES
            or Path(relative).name in {"NOTICE", "README"}
        )
    ]


def _is_allowed_financial_text(relative: str) -> bool:
    return (
        relative in ALLOWED_TEXT_PATHS
        or relative.startswith("src/fin_graph/evals/data/equities/")
    )


def test_operational_product_paths_are_absent() -> None:
    assert [relative for relative in OPERATIONAL_PATHS if (ROOT / relative).exists()] == []


def test_dependencies_contain_no_operational_sdks() -> None:
    project_file = ROOT / "pyproject.toml"
    tomllib.loads(project_file.read_text(encoding="utf-8"))
    normalized = project_file.read_text(encoding="utf-8").casefold()
    assert not [name for name in ("boto3", "botocore", "kubernetes") if name in normalized]


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


def test_financial_product_surfaces_remain_importable() -> None:
    for module in FINANCIAL_IMPORTS:
        assert importlib.import_module(module).__name__ == module


def test_tracked_product_text_has_no_operational_vocabulary() -> None:
    findings: dict[str, list[str]] = {}
    for path in _tracked_text_paths():
        relative = path.relative_to(ROOT).as_posix()
        if _is_allowed_financial_text(relative):
            continue
        text = path.read_text(encoding="utf-8")
        matches = sorted({match.group(0) for match in OPERATIONAL_TERM.finditer(text)})
        if matches:
            findings[relative] = matches
    assert findings == {}
