"""The only module coupling workflow topology to LangGraph."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from bm_workflow.definition import (
    Element,
    RouteNode,
    StepNode,
    WorkflowDefinition,
    _callable_name,
    step_manifest,
)
from bm_workflow.errors import DefinitionError, ExecutionError


class GraphState(TypedDict):
    data: dict[str, Any]
    route: str


@dataclass(frozen=True)
class CompiledWorkflow:
    definition: WorkflowDefinition
    definition_digest: str
    graph: Any
    node_count: int


def _manifest(nodes: tuple[Element, ...], seen: set[str]) -> list[dict[str, Any]]:
    result = []
    for node in nodes:
        if node.id in seen:
            raise DefinitionError("Duplicate node ID")
        seen.add(node.id)
        if len(seen) > 256:
            raise DefinitionError("A workflow supports at most 256 graph nodes")
        if isinstance(node, StepNode):
            result.append(step_manifest(node))
        else:
            result.append(
                {
                    "id": node.id,
                    "kind": "router",
                    "handler": _callable_name(node.by),
                    "artifact_version": node.artifact_version,
                    "routes": {
                        label: _manifest(branch, seen) for label, branch in node.routes.items()
                    },
                    "default": _manifest(node.default, seen) if node.default else None,
                }
            )
    return result


def _step_action(node: StepNode) -> Any:
    from bm_workflow.runtime import execute_step

    async def action(state: GraphState) -> dict[str, Any]:
        return {"data": await execute_step(node, state["data"])}

    return action


def _route_action(node: RouteNode) -> Any:
    from bm_workflow.runtime import call_handler, current_context

    async def action(state: GraphState) -> dict[str, str]:
        context = current_context()
        if context:
            context.check()
        try:
            selected = await call_handler(node.by, state["data"])
            if isinstance(selected, str) and selected in node.routes:
                return {"route": selected}
            if node.default is not None:
                return {"route": "__default__"}
            raise ExecutionError("invalid_route", node.id)
        except ExecutionError:
            raise
        except Exception as error:
            raise ExecutionError("node_failed", node.id, type(error).__name__) from None

    return action


def compile_workflow(definition: WorkflowDefinition) -> CompiledWorkflow:
    """Compile a validated acyclic tree of sequences; fan-out lives in compositions."""
    seen: set[str] = set()
    manifest = {
        "format": 1,
        "name": definition.name,
        "version": definition.version,
        "input": definition.input_schema.model_json_schema(),
        "output": definition.output_schema.model_json_schema(),
        "nodes": _manifest(definition.nodes, seen),
    }
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    graph = StateGraph(GraphState)

    def add_chain(nodes: tuple[Element, ...]) -> None:
        for index, node in enumerate(nodes):
            if isinstance(node, StepNode):
                graph.add_node(node.id, _step_action(node))
                graph.add_edge(node.id, nodes[index + 1].id if index + 1 < len(nodes) else END)
            else:
                graph.add_node(node.id, _route_action(node))
                targets: dict[Hashable, str] = {
                    label: branch[0].id for label, branch in node.routes.items()
                }
                for branch in node.routes.values():
                    add_chain(branch)
                if node.default:
                    targets["__default__"] = node.default[0].id
                    add_chain(node.default)
                graph.add_conditional_edges(node.id, lambda state: state["route"], targets)

    add_chain(definition.nodes)
    graph.add_edge(START, definition.nodes[0].id)
    return CompiledWorkflow(definition, digest, graph.compile(), len(seen))
