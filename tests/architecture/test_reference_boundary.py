from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

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
            violations.extend(
                f"{path.relative_to(ROOT).as_posix()}:{node.lineno}:{name}"
                for name in names
                if name.startswith(("Legacy", "Compatibility", "make_compat"))
            )
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


def _module_names(root: Path, package: str) -> frozenset[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join((package, *parts)).rstrip("."))
    return frozenset(names)


def _environment_aliases() -> frozenset[str]:
    files = (
        SRC / "config" / "settings.py",
        SRC / "adapters" / "reference_app" / "settings.py",
        SRC / "adapters" / "reference_app" / "schemas.py",
        SRC / "persistence" / "reference_app" / "postgres.py",
    )
    aliases: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if call_name in {"resolve_alias", "_environment_bool"}:
                arguments = node.args[1:3]
            elif call_name == "AliasChoices":
                arguments = node.args
            elif call_name == "getenv":
                arguments = node.args[:1]
            else:
                continue
            aliases.update(
                value.value
                for argument in arguments
                for value in ast.walk(argument)
                if isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]+", value.value)
            )
    return frozenset(aliases)


def _reference_routes(router: object, prefix: str = "") -> frozenset[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in getattr(router, "routes", ()):
        if isinstance(route, APIRoute):
            routes.update(
                (method, f"{prefix}{route.path}")
                for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
            )
            continue
        included = getattr(route, "original_router", None)
        context = getattr(route, "include_context", None)
        if included is not None and context is not None:
            routes.update(_reference_routes(included, f"{prefix}{context.prefix}"))
    return frozenset(routes)


def test_public_and_persisted_reference_identifiers_are_exactly_allowlisted() -> None:
    import fin_graph.adapters.reference_app.schemas as schemas
    from fin_graph.adapters.reference_app.http import reference_app_router
    from fin_graph.conformance.reference_app.public_identifiers import (
        REFERENCE_ENVIRONMENT_ALIASES,
        REFERENCE_HTTP_ROUTES,
        REFERENCE_MIGRATION_REVISIONS,
        REFERENCE_PAYLOAD_FIELDS,
        REFERENCE_PERSISTENT_FILENAMES,
        REFERENCE_PYTHON_SHIMS,
        REFERENCE_SQL_CONSTRAINTS,
        REFERENCE_SQL_TABLES,
    )
    from fin_graph.persistence.reference_app.sqlite import _metadata

    migration = importlib.import_module(
        "fin_graph.server.migrations.versions.0013_compatibility_store"
    )

    assert frozenset(_metadata.tables) == REFERENCE_SQL_TABLES
    assert (
        frozenset(
            constraint.name
            for table in _metadata.tables.values()
            for constraint in table.constraints
            if constraint.name
        )
        == REFERENCE_SQL_CONSTRAINTS
    )
    assert frozenset({migration.revision}) == REFERENCE_MIGRATION_REVISIONS

    factory_tree = ast.parse((SRC / "server" / "factory.py").read_text(encoding="utf-8"))
    assert (
        frozenset(
            node.value
            for node in ast.walk(factory_tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"compatibility\.(?:sqlite|key)", node.value)
        )
        == REFERENCE_PERSISTENT_FILENAMES
    )
    assert _environment_aliases() == REFERENCE_ENVIRONMENT_ALIASES
    assert (
        _module_names(ROOT / "src" / "src", "src")
        | _module_names(ROOT / "src" / "app" / "backend", "app.backend")
        == REFERENCE_PYTHON_SHIMS
    )
    assert _reference_routes(reference_app_router()) == REFERENCE_HTTP_ROUTES
    assert {
        name: tuple(value.model_fields)
        for name, value in vars(schemas).items()
        if inspect.isclass(value)
        and issubclass(value, BaseModel)
        and value.__module__ == schemas.__name__
    } == REFERENCE_PAYLOAD_FIELDS
