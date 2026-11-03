from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from fin_graph.cli.main import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def test_agents_list_is_catalog_backed_and_has_nineteen_personas() -> None:
    result = runner.invoke(app, ["agents", "list", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len([item for item in payload if item["id"].startswith("analysis.persona.")]) == 19
    assert len({item["id"] for item in payload}) == len(payload)


def test_agents_describe_returns_relationships_without_building_a_model() -> None:
    result = runner.invoke(app, ["agents", "describe", "analysis.persona_team", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["children"]) == 19
    assert payload["middleware"]


def test_agents_describe_unknown_id_is_actionable() -> None:
    result = runner.invoke(app, ["agents", "describe", "missing"])

    assert result.exit_code == 2
    assert "missing" in result.output


def test_specialized_agent_guides_have_one_consistent_navigation() -> None:
    expected = ["Entrada", "Estado", "Agentes", "Herramientas", "Salida", "Pruebas"]
    for package in ("analysis", "chat", "solver", "optimizer"):
        text = (ROOT / "src" / "fin_graph" / "agents" / package / "README.md").read_text(
            encoding="utf-8"
        )
        sections = [
            line.removeprefix("## ") for line in text.splitlines() if line.startswith("## ")
        ]
        assert sections == expected


def test_architecture_guides_name_product_routes_and_optional_optimization() -> None:
    root_guide = (ROOT / "src" / "fin_graph" / "agents" / "README.md").read_text(
        encoding="utf-8"
    )
    architecture = (ROOT / "docs" / "arquitectura-agentes.md").read_text(encoding="utf-8")
    optimization = (ROOT / "docs" / "better-harness.md").read_text(encoding="utf-8")

    for route in ("analysis", "chat", "solver", "optimizer"):
        assert route in root_guide.lower()
    assert "DeepAgents" in root_guide and "LangGraph" in root_guide
    assert "```mermaid" in architecture
    assert optimization.lower().startswith("# opcional")
    assert "optimization/fin.toml" in optimization
    assert "harness/fin.toml" not in optimization
