"""Immutable declarations and implementation bindings, independent of LangGraph."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel

from bm_workflow.errors import DefinitionError

type Handler = Callable[[Any], Any]
type Model = type[BaseModel]


def validate_model(schema: Model, value: Any) -> BaseModel:
    """Revalidate nested models and accept aliases or canonical field names."""
    return schema.model_validate(_validation_data(value), by_name=True)


def _validation_data(value: Any, depth: int = 0) -> Any:
    if depth > 256:
        raise ValueError("Input nesting exceeds the validation limit")
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", round_trip=True, warnings=False)
    if isinstance(value, Mapping):
        return {key: _validation_data(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_validation_data(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_validation_data(item, depth + 1) for item in value)
    return deepcopy(value)


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", value):
        raise DefinitionError("Identifiers must start with a letter and contain 1-128 characters")


def _version(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", value):
        raise DefinitionError("An explicit version of 1-64 characters is required")


def _schema(value: Model) -> None:
    if not isinstance(value, type) or not issubclass(value, BaseModel):
        raise DefinitionError("Contracts must be Pydantic model classes")
    if getattr(value, "__pydantic_root_model__", False):
        raise DefinitionError("Workflow state contracts must be object models, not RootModel")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class ExecutionPolicy:
    max_attempts: int = 1
    safe_to_retry: bool = False
    retry_delay: float = 0.0
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be an integer between 1 and 10")
        if self.max_attempts > 1 and not self.safe_to_retry:
            raise ValueError("Retries require safe_to_retry=True")
        if not math.isfinite(self.retry_delay) or self.retry_delay < 0:
            raise ValueError("retry_delay must be finite and nonnegative")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")


@dataclass(frozen=True)
class StepNode:
    id: str
    handler: Handler = field(repr=False)
    input_schema: Model | None = None
    output_schema: Model | None = None
    artifact_version: str = "1"
    kind: Literal["deterministic", "external", "composition"] = "deterministic"
    policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    typed: bool = False

    def __post_init__(self) -> None:
        _identifier(self.id)
        _version(self.artifact_version)
        if not callable(self.handler):
            raise DefinitionError("Step handler must be callable")
        if self.kind not in ("deterministic", "external", "composition"):
            raise DefinitionError("Unknown node kind")
        if not isinstance(self.policy, ExecutionPolicy):
            raise DefinitionError("Invalid execution policy")
        for schema in (self.input_schema, self.output_schema):
            if schema is not None:
                _schema(schema)
        if self.typed and (self.input_schema is None or self.output_schema is None):
            raise DefinitionError("Typed steps require input and output contracts")
        snapshot = json.loads(json.dumps(_plain(self.metadata), allow_nan=False, sort_keys=True))
        object.__setattr__(self, "metadata", _freeze(snapshot))


def step(id: str, handler: Handler, **kwargs: Any) -> StepNode:
    return StepNode(id, handler, **kwargs)


def typed_step(
    id: str,
    handler: Handler,
    *,
    input_schema: Model,
    output_schema: Model,
    artifact_version: str,
    **kwargs: Any,
) -> StepNode:
    return StepNode(
        id,
        handler,
        input_schema=input_schema,
        output_schema=output_schema,
        artifact_version=artifact_version,
        typed=True,
        **kwargs,
    )


def _callable_name(handler: Handler) -> str:
    return (
        f"{getattr(handler, '__module__', type(handler).__module__)}:"
        f"{getattr(handler, '__qualname__', type(handler).__qualname__)}"
    )


def step_manifest(node: StepNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind,
        "handler": _callable_name(node.handler),
        "artifact_version": node.artifact_version,
        "typed": node.typed,
        "input": node.input_schema.model_json_schema() if node.input_schema else None,
        "output": node.output_schema.model_json_schema() if node.output_schema else None,
        "policy": asdict(node.policy),
        "metadata": _plain(node.metadata),
    }


@dataclass(frozen=True)
class RouteNode:
    id: str
    by: Handler = field(repr=False)
    routes: Mapping[str, tuple[StepNode | RouteNode, ...]]
    default: tuple[StepNode | RouteNode, ...] | None = None
    artifact_version: str = "1"

    def __post_init__(self) -> None:
        _identifier(self.id)
        _version(self.artifact_version)
        if not callable(self.by) or not self.routes:
            raise DefinitionError("Routes need a callable and nonempty branches")
        branches = {}
        for label, nodes in self.routes.items():
            if not isinstance(label, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", label
            ):
                raise DefinitionError("Invalid route label")
            branches[label] = _chain(nodes)
        object.__setattr__(self, "routes", MappingProxyType(branches))
        if self.default is not None:
            object.__setattr__(self, "default", _chain(self.default))


type Element = StepNode | RouteNode


def _chain(nodes: Sequence[Element]) -> tuple[Element, ...]:
    result = tuple(nodes)
    if not result:
        raise DefinitionError("Flows and branches cannot be empty")
    for index, node in enumerate(result):
        if not isinstance(node, (StepNode, RouteNode)):
            raise DefinitionError("Flows accept StepNode or RouteNode")
        if isinstance(node, RouteNode) and index != len(result) - 1:
            raise DefinitionError("A route must be the last element of its flow")
    return result


def route(
    id: str,
    by: Handler,
    routes: Mapping[str, Sequence[Element]],
    *,
    default: Sequence[Element] | None = None,
    artifact_version: str = "1",
) -> RouteNode:
    return RouteNode(
        id,
        by,
        {label: tuple(nodes) for label, nodes in routes.items()},
        tuple(default) if default is not None else None,
        artifact_version,
    )


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: str
    input_schema: Model
    output_schema: Model
    nodes: tuple[Element, ...]

    def __post_init__(self) -> None:
        _identifier(self.name)
        _version(self.version)
        _schema(self.input_schema)
        _schema(self.output_schema)
        object.__setattr__(self, "nodes", _chain(self.nodes))


@dataclass(frozen=True)
class WorkflowBuilder:
    name: str
    version: str
    input_schema: Model
    output_schema: Model

    def flow(self, *elements: Element) -> WorkflowDefinition:
        return WorkflowDefinition(
            self.name, self.version, self.input_schema, self.output_schema, elements
        )


def workflow(*, name: str, version: str, input: Model, output: Model) -> WorkflowBuilder:
    return WorkflowBuilder(name, version, input, output)
