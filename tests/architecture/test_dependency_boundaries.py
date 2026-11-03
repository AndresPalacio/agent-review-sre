from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "fin_graph"
PRODUCTION_ROOTS = tuple(
    SRC / package
    for package in (
        "agents",
        "cli",
        "domain",
        "evals",
        "integrations",
        "llm",
        "persistence",
        "runtime",
        "safety",
        "server",
        "trading",
        "workflows",
    )
)


def _forbidden_imports(roots: Sequence[Path], *, allowed: set[str]) -> list[str]:
    violations: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(SRC).as_posix()
            if relative in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for name in names:
                    if name == "better_harness" or name.startswith("better_harness."):
                        violations.append(f"{relative}:{node.lineno}:{name}")
                    if name == "fin_graph.optimization" or name.startswith(
                        "fin_graph.optimization."
                    ):
                        violations.append(f"{relative}:{node.lineno}:{name}")
    return violations


def test_production_has_no_optimization_or_better_harness_imports() -> None:
    assert _forbidden_imports(PRODUCTION_ROOTS, allowed={"cli/harness.py"}) == []


def test_optional_harness_has_one_explicit_package_boundary() -> None:
    assert (SRC / "optimization" / "harness").is_dir()
    assert not (SRC / "harness").exists()
