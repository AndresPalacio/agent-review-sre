"""Validated, read-only discovery catalog for every model-driven agent."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, cast

type AgentKind = Literal[
    "deepagents_supervisor",
    "deepagents_subagent",
]
type ToolPolicy = Literal["read_only", "paper_trade", "workspace"]

_AGENT_KINDS = frozenset[AgentKind]({"deepagents_supervisor", "deepagents_subagent"})
_TOOL_POLICIES = frozenset[ToolPolicy]({"read_only", "paper_trade", "workspace"})


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Stable navigation metadata pointing at one executable factory."""

    id: str
    display_name: str
    kind: AgentKind
    purpose: str
    factory_path: str
    children: tuple[str, ...] = ()
    tool_policy: ToolPolicy = "read_only"
    middleware: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    activation: str = ""
    surfaces: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready metadata without importing or building the factory."""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind,
            "purpose": self.purpose,
            "factory_path": self.factory_path,
            "children": list(self.children),
            "tool_policy": self.tool_policy,
            "middleware": list(self.middleware),
            "limits": list(self.limits),
            "activation": self.activation,
            "surfaces": list(self.surfaces),
        }


def resolve_factory(descriptor: AgentDescriptor) -> Callable[..., Any]:
    """Resolve a descriptor for validation and developer tooling, never runtime assembly."""

    module_name, separator, attribute = descriptor.factory_path.partition(":")
    if not separator or not module_name or not attribute:
        raise ImportError(
            f"invalid factory path for {descriptor.id!r}: {descriptor.factory_path!r}"
        )
    module = importlib.import_module(module_name)
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        raise ImportError(
            f"factory {attribute!r} is missing from {module_name!r} for {descriptor.id!r}"
        ) from exc
    if not callable(factory):
        raise ImportError(f"factory for {descriptor.id!r} is not callable")
    return cast(Callable[..., Any], factory)


def validate_agent_catalog(items: Sequence[AgentDescriptor]) -> tuple[AgentDescriptor, ...]:
    """Validate identifiers, relationships and factory paths before exposing the catalog."""

    catalog = tuple(items)
    ids = [item.id for item in catalog]
    duplicates = sorted({agent_id for agent_id in ids if ids.count(agent_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate agent ids: {', '.join(duplicates)}")
    known = set(ids)
    for item in catalog:
        if not item.id or not item.display_name or not item.purpose:
            raise ValueError("agent id, display_name and purpose must be non-empty")
        if item.kind not in _AGENT_KINDS:
            raise ValueError(f"unknown agent kind for {item.id!r}: {item.kind!r}")
        if item.tool_policy not in _TOOL_POLICIES:
            raise ValueError(f"unknown tool policy for {item.id!r}: {item.tool_policy!r}")
        missing = sorted(set(item.children) - known)
        if missing:
            raise ValueError(f"agent {item.id!r} references missing child: {', '.join(missing)}")
        resolve_factory(item)
    return catalog


@lru_cache(maxsize=1)
def agent_catalog() -> tuple[AgentDescriptor, ...]:
    """Aggregate colocated descriptors lazily and validate the immutable result once."""

    from fin_graph.agents.analysis.catalog import NATIVE_ANALYSIS_DESCRIPTORS
    from fin_graph.agents.analysis.persona_team import PERSONA_TEAM_DESCRIPTORS
    from fin_graph.agents.chat.supervisor import CHAT_AGENT_DESCRIPTORS
    from fin_graph.agents.optimizer.proposer import OPTIMIZER_AGENT_DESCRIPTORS
    from fin_graph.agents.solver.supervisor import SOLVER_AGENT_DESCRIPTORS

    return validate_agent_catalog(
        (
            *NATIVE_ANALYSIS_DESCRIPTORS,
            *PERSONA_TEAM_DESCRIPTORS,
            *CHAT_AGENT_DESCRIPTORS,
            *SOLVER_AGENT_DESCRIPTORS,
            *OPTIMIZER_AGENT_DESCRIPTORS,
        )
    )


def agent_by_id(agent_id: str) -> AgentDescriptor:
    """Return one descriptor or raise an actionable lookup error."""

    try:
        return next(item for item in agent_catalog() if item.id == agent_id)
    except StopIteration:
        raise KeyError(f"unknown agent id: {agent_id}") from None
