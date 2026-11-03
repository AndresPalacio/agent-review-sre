"""Typed, inert flow definitions accepted by the compatibility compiler."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints

from fin_graph.compat.contracts import JsonObject

NodeId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class FlowNode(BaseModel):
    """One allowlisted flow operation; data is configuration, never executable code."""

    model_config = ConfigDict(extra="allow")

    id: NodeId
    kind: str = Field(
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("kind", "type"),
    )
    data: JsonObject = Field(default_factory=dict)
    parameters: JsonObject = Field(default_factory=dict)
    position: JsonObject | None = None

    @property
    def model_name(self) -> str | None:
        value = self.data.get("model_name", self.parameters.get("model_name"))
        return value if isinstance(value, str) else None

    @property
    def model_provider(self) -> str | None:
        value = self.data.get("model_provider", self.parameters.get("model_provider"))
        return value if isinstance(value, str) else None


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: NodeId
    source: NodeId
    target: NodeId
    type: str | None = None
    data: JsonObject = Field(default_factory=dict)


class FlowGraph(BaseModel):
    """A frontend graph, kept separate from its presentation viewport."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[FlowNode]
    edges: list[FlowEdge]
    viewport: JsonObject | None = None
    data: JsonObject | None = None
    is_template: bool = False
    tags: list[str] | None = None


def json_object(value: Any) -> JsonObject:
    """Narrow an execution value to the persisted JSON-object contract."""

    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("flow value must be a JSON object")
    return value
