from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "fin_graph"
CORE_PACKAGES = (
    "agents",
    "workflows",
    "runtime",
    "trading",
    "domain",
    "safety",
    "integrations",
    "llm",
)
TARGET_MODULES = (
    "fin_graph.adapters.reference_app.mapping",
    "fin_graph.adapters.reference_app.python_api",
    "fin_graph.adapters.reference_app.runtime",
    "fin_graph.adapters.reference_app.settings",
    "fin_graph.adapters.reference_app.http",
    "fin_graph.persistence.reference_app",
    "fin_graph.conformance.contracts",
    "fin_graph.conformance.reference_app.snapshot",
)


def _trees(root: Path = SRC) -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


@pytest.mark.parametrize("module", TARGET_MODULES)
def test_reference_boundary_modules_are_explicit(module: str) -> None:
    assert importlib.import_module(module).__name__ == module


def test_old_internal_reference_packages_are_absent() -> None:
    assert not (SRC / "compat").exists()
    assert not (SRC / "server" / "compat").exists()
    assert not any(path.name == "ai_hedge_fund" for path in SRC.rglob("ai_hedge_fund"))


def test_live_python_identifiers_use_responsibility_names() -> None:
    violations: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            for name in names:
                if name.startswith(("Legacy", "Compatibility", "make_compat")):
                    violations.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}:{name}")
    assert violations == []


def test_core_packages_do_not_import_reference_adapter() -> None:
    violations: list[str] = []
    for package in CORE_PACKAGES:
        for path, tree in _trees(SRC / package):
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if "fin_graph.adapters.reference_app" in module:
                    violations.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert violations == []


def test_bounded_and_normalized_names_replace_ambiguous_compact() -> None:
    names: set[str] = set()
    for _, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)

    assert "_bounded_evidence_payload" in names
    assert "normalized_hex" in names
    assert "normalized_iban" in names
    assert "compact" not in names
