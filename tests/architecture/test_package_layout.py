from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "fin_graph.agents.common.factory",
        "fin_graph.agents.common.subagents",
        "fin_graph.agents.common.middleware.guardrails",
        "fin_graph.agents.common.testing.scripted_model",
    ],
)
def test_target_module_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module
