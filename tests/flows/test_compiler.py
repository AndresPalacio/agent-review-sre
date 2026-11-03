from __future__ import annotations

from typing import Any

import pytest

from fin_graph.flows.compiler import FlowCompileError, compile_flow
from fin_graph.flows.models import FlowGraph
from fin_graph.flows.service import FlowServices


def graph(**overrides: Any) -> FlowGraph:
    value = {
        "nodes": [
            {"id": "input", "kind": "input"},
            {
                "id": "value",
                "kind": "analyst",
                "data": {"agent_id": "valuation", "model_name": "model-a"},
            },
            {
                "id": "technical",
                "kind": "analyst",
                "data": {"agent_id": "technicals", "model_name": "model-b"},
            },
            {"id": "join", "kind": "join"},
            {"id": "output", "kind": "output"},
        ],
        "edges": [
            {"id": "a", "source": "input", "target": "value"},
            {"id": "b", "source": "input", "target": "technical"},
            {"id": "c", "source": "value", "target": "join"},
            {"id": "d", "source": "technical", "target": "join"},
            {"id": "e", "source": "join", "target": "output"},
        ],
    }
    value.update(overrides)
    return FlowGraph.model_validate(value)


async def test_compiler_runs_fan_out_join_and_propagates_node_configuration() -> None:
    seen: list[tuple[str, str | None, dict[str, object]]] = []

    async def execute(node: Any, inputs: dict[str, object], request: dict[str, object]) -> object:
        seen.append((node.id, node.model_name, request))
        return {"node": node.id, "inputs": sorted(inputs)}

    compiled = compile_flow(graph(), FlowServices(execute=execute))
    result = await compiled.ainvoke({"request": {"tickers": ["AAPL"]}})

    assert {item[0] for item in seen} == {"value", "technical"}
    assert {item[1] for item in seen} == {"model-a", "model-b"}
    assert result["result"] == {
        "join": {
            "technical": {"node": "technical", "inputs": ["input"]},
            "value": {"node": "value", "inputs": ["input"]},
        }
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["nodes"].append({"id": "value", "kind": "analyst"}),
            "duplicate node",
        ),
        (
            lambda value: value["edges"].append(
                {"id": "cycle", "source": "join", "target": "value"}
            ),
            "cycle",
        ),
        (
            lambda value: value["nodes"].append({"id": "orphan", "kind": "analyst"}),
            "orphan",
        ),
        (
            lambda value: value["edges"].append(
                {"id": "unknown", "source": "missing", "target": "output"}
            ),
            "unknown node",
        ),
        (
            lambda value: value["nodes"].append({"id": "shell", "kind": "shell"}),
            "unsupported node kind",
        ),
    ],
)
def test_compiler_rejects_unsafe_or_invalid_graphs(mutate: Any, message: str) -> None:
    value = graph().model_dump(mode="json")
    mutate(value)
    with pytest.raises(FlowCompileError, match=message):
        compile_flow(FlowGraph.model_validate(value), FlowServices())
