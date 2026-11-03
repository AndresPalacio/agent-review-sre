from __future__ import annotations

import ast
from pathlib import Path

from fin_graph.agents.catalog import agent_catalog

SRC = Path(__file__).resolve().parents[2] / "src" / "fin_graph"


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _deepagent_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "deepagents":
            aliases.update(
                item.asname or item.name
                for item in node.names
                if item.name == "create_deep_agent"
            )
    return aliases


def _create_deep_agent_call_paths(root: Path) -> set[str]:
    found: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _deepagent_aliases(tree)
        if aliases and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in aliases
            for node in ast.walk(tree)
        ):
            found.add(path.relative_to(root).as_posix())
    return found


def _create_deep_agent_factory_paths(root: Path) -> set[str]:
    factories: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _deepagent_aliases(tree)
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in aliases
                for child in ast.walk(node)
            ):
                module = ".".join(("fin_graph", *path.relative_to(root).with_suffix("").parts))
                factories.add(f"{module}:{node.name}")
    return factories


def _package_imports(root: Path) -> set[str]:
    imports: set[str] = set()
    for path in _python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                parts = name.split(".")
                if len(parts) >= 2 and parts[0] == "fin_graph":
                    imports.add(".".join(parts[:2]))
    return imports


def test_create_deep_agent_calls_live_only_under_agents() -> None:
    assert _create_deep_agent_call_paths(SRC) == {
        "agents/analysis/persona_team.py",
        "agents/chat/supervisor.py",
        "agents/solver/supervisor.py",
        "agents/optimizer/proposer.py",
    }


def test_agents_do_not_depend_on_workflows_runtime_or_work() -> None:
    assert _package_imports(SRC / "agents").isdisjoint(
        {"fin_graph.workflows", "fin_graph.runtime", "fin_graph.work"}
    )


def test_comments_do_not_count_as_deepagent_calls(tmp_path: Path) -> None:
    (tmp_path / "comment.py").write_text("# create_deep_agent()\n", encoding="utf-8")
    (tmp_path / "aliased.py").write_text(
        "from deepagents import create_deep_agent as build\nbuild()\n", encoding="utf-8"
    )

    assert _create_deep_agent_call_paths(tmp_path) == {"aliased.py"}


def test_every_deepagent_factory_is_catalogued() -> None:
    catalogued = {item.factory_path for item in agent_catalog()}

    assert _create_deep_agent_factory_paths(SRC) <= catalogued
