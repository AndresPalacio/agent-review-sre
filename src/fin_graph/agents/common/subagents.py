"""Subagents pattern: compiled agents delegated to through the native task tool."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from fin_graph.agents.common.factory import make_agent


@dataclass(frozen=True)
class SubagentSpec:
    """A compiled agent plus the routing text the supervisor's model reads."""

    name: str
    description: str
    agent: CompiledStateGraph[Any, Any, Any, Any]


def default_supervisor_prompt(subagents: Sequence[SubagentSpec]) -> str:
    """The coordinator prompt used when the caller supplies none."""
    names = ", ".join(spec.name for spec in subagents)
    return (
        "You coordinate a team of specialists. Delegate work to them via the task tool "
        f"({names}) and answer the user only once the delegated work is done."
    )


def make_supervisor(
    *,
    subagents: Sequence[SubagentSpec],
    model: BaseChatModel,
    name: str = "supervisor",
    system_prompt: str | None = None,
    tools: Sequence[BaseTool] = (),
    middleware: Sequence[AgentMiddleware[Any, Any]] = (),
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """A supervisor with native compiled subagents plus any direct ``tools``."""
    compiled_subagents: list[CompiledSubAgent] = [
        {
            "name": spec.name,
            "description": spec.description,
            "runnable": spec.agent,
            "mode": "isolated",
        }
        for spec in subagents
    ]
    return make_agent(
        name=name,
        model=model,
        tools=tools,
        system_prompt=system_prompt or default_supervisor_prompt(subagents),
        middleware=middleware,
        subagents=compiled_subagents,
        allow_synchronous_subagents=True,
        context_schema=context_schema,
        checkpointer=checkpointer,
        store=store,
    )
