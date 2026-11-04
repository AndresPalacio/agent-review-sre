"""Public documentation is executable and describes only the shipped finance product."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from dotenv import dotenv_values
from typer.testing import CliRunner

from fin_graph.cli.main import app

ROOT = Path(__file__).parents[2]
DOCS = (
    "docs/arquitectura-agentes.md",
    "docs/better-harness.md",
    "docs/compatibility/ai-hedge-fund.md",
    "docs/compatibility/configuration-matrix.md",
    "docs/compatibility/api-manifest.md",
    "docs/operations/local-app.md",
    "docs/operations/remote-deployment.md",
)
PRIMARY_DOCS = ("README.md", "docs/arquitectura-agentes.md", "docs/better-harness.md")
REMOVED_DOC_REFERENCES = (
    "progress/",
    "arquitecture-review/",
    "integrations-wave2",
    "phase8",
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_required_product_documents_exist_and_have_no_phase_status() -> None:
    for relative in DOCS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert len(text) > 200
        assert "Fase " not in text
        assert "TODO" not in text


def test_primary_documents_do_not_reference_removed_history() -> None:
    for relative in PRIMARY_DOCS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        lowered = text.casefold()
        assert not [value for value in REMOVED_DOC_REFERENCES if value in lowered]


def test_all_retained_documentation_links_resolve() -> None:
    documents = [
        ROOT / "README.md",
        ROOT / "examples" / "README.md",
        ROOT / "packages" / "workflow" / "README.md",
        *(ROOT / "docs").rglob("*.md"),
    ]
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            resolved = (document.parent / unquote(target)).resolve()
            relative = document.relative_to(ROOT)
            assert resolved.exists(), f"{relative} links missing path {raw_target}"


def test_readme_describes_the_finished_financial_product() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "plataforma financiera multiagente" in lowered
    assert "paper trading" in lowered
    assert "no ejecuta operaciones reales" in lowered
    assert "migrando" not in lowered
    assert "uv run fin-graph app" in text


def test_example_environment_contains_the_local_financial_defaults() -> None:
    values = dotenv_values(ROOT / ".env.example", encoding="utf-8")
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
