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
        "fin_graph.agents.analysis.catalog",
        "fin_graph.agents.analysis.personas",
        "fin_graph.agents.analysis.persona_prompts",
        "fin_graph.agents.analysis.persona_team",
        "fin_graph.agents.chat.supervisor",
        "fin_graph.agents.solver.control",
        "fin_graph.agents.solver.supervisor",
        "fin_graph.agents.solver.testing",
        "fin_graph.agents.optimizer.proposer",
        "fin_graph.workflows.analysis.graph",
        "fin_graph.workflows.analysis.services",
        "fin_graph.workflows.analysis.state",
        "fin_graph.workflows.analysis.strategy.registration",
        "fin_graph.workflows.orders.graph",
        "fin_graph.workflows.market_watch.graph",
        "fin_graph.trading.order_desk",
        "fin_graph.runtime.analysis",
        "fin_graph.runtime.observation",
        "fin_graph.runtime.profile",
        "fin_graph.runtime.trace",
    ],
)
def test_target_module_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module
