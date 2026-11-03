"""Compile validated visual finance flows without dynamic imports or evaluation."""

from __future__ import annotations

import inspect
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from fin_graph.compat.contracts import JsonObject, JsonValue
from fin_graph.flows.models import FlowGraph, FlowNode

_KINDS = {
    "input": "input",
    "start": "input",
    "portfolio-start-node": "input",
    "stock-analyzer-node": "input",
    "analyst": "analyst",
    "agent-node": "analyst",
    "join": "join",
    "portfolio-manager-node": "join",
    "output": "output",
    "investment-report-node": "output",
    "json-output-node": "output",
}


class FlowCompileError(ValueError):
    """The stored graph cannot be executed safely or deterministically."""


def _merge_results(
    current: Mapping[str, JsonValue] | None, new: Mapping[str, JsonValue] | None
) -> dict[str, JsonValue]:
    return {**(current or {}), **(new or {})}


class FlowState(TypedDict, total=False):
    request: JsonObject
    node_results: Annotated[dict[str, JsonValue], _merge_results]
    result: JsonObject


def _kind(node: FlowNode) -> str:
    try:
        return _KINDS[node.kind]
    except KeyError as exc:
        raise FlowCompileError(f"unsupported node kind: {node.kind}") from exc


def _validated(
    value: FlowGraph,
) -> tuple[dict[str, FlowNode], dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    nodes: dict[str, FlowNode] = {}
    for node in value.nodes:
        if node.id in nodes:
            raise FlowCompileError(f"duplicate node: {node.id}")
        _kind(node)
        nodes[node.id] = node
    if not nodes:
        raise FlowCompileError("flow must contain nodes")

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    edge_ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for edge in value.edges:
        if edge.id in edge_ids or (edge.source, edge.target) in pairs:
            raise FlowCompileError(f"duplicate edge: {edge.id}")
        edge_ids.add(edge.id)
        pairs.add((edge.source, edge.target))
        if edge.source not in nodes or edge.target not in nodes:
            raise FlowCompileError(f"edge references unknown node: {edge.id}")
        incoming[edge.target].append(edge.source)
        outgoing[edge.source].append(edge.target)

    indegree = {node_id: len(incoming[node_id]) for node_id in nodes}
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    while queue:
        node_id = queue.popleft()
        visited.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(visited) != len(nodes):
        raise FlowCompileError("flow contains a cycle")

    inputs = {node_id for node_id, node in nodes.items() if _kind(node) == "input"}
    outputs = {node_id for node_id, node in nodes.items() if _kind(node) == "output"}
    if not inputs or not outputs:
        raise FlowCompileError("flow requires input and output nodes")
    if len(outputs) != 1:
        raise FlowCompileError("flow requires exactly one output node")
    if any(incoming[node_id] for node_id in inputs):
        raise FlowCompileError("input nodes cannot have predecessors")
    if any(outgoing[node_id] for node_id in outputs):
        raise FlowCompileError("output nodes cannot have successors")

    reachable = set(inputs)
    for node_id in visited:
        if node_id in reachable:
            reachable.update(outgoing[node_id])
    reaching_output = set(outputs)
    for node_id in reversed(visited):
        if node_id in reaching_output:
            reaching_output.update(incoming[node_id])
    orphaned = set(nodes) - (reachable & reaching_output)
    if orphaned:
        raise FlowCompileError(f"orphan node: {sorted(orphaned)[0]}")

    return (
        nodes,
        {key: tuple(values) for key, values in incoming.items()},
        {key: tuple(values) for key, values in outgoing.items()},
    )


def compile_flow(value: FlowGraph, services: Any) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile only the fixed input/analyst/join/output vocabulary."""

    nodes, incoming, outgoing = _validated(value)
    builder = StateGraph(FlowState)

    for node_id, node in nodes.items():
        kind = _kind(node)

        async def execute(
            state: FlowState, *, selected: FlowNode = node, role: str = kind
        ) -> dict[str, Any]:
            request = state.get("request", {})
            results = state.get("node_results", {})
            inputs = {source: results[source] for source in incoming[selected.id]}
            if role == "input":
                value: JsonValue = request
            elif role == "analyst":
                runner = getattr(services, "execute", None)
                if runner is None:
                    value = {"inputs": inputs, "parameters": selected.parameters}
                else:
                    value = runner(selected, inputs, request)
                    if inspect.isawaitable(value):
                        value = await value
            elif role == "join":
                value = {key: inputs[key] for key in sorted(inputs)}
            else:
                return {"result": {key: inputs[key] for key in sorted(inputs)}}
            return {"node_results": {selected.id: value}}

        builder.add_node(node_id, execute)

    for node_id in nodes:
        if not incoming[node_id]:
            builder.add_edge(START, node_id)
        for target in outgoing[node_id]:
            builder.add_edge(node_id, target)
        if not outgoing[node_id]:
            builder.add_edge(node_id, END)
    return builder.compile()
