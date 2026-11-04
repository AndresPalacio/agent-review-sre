"""The single construction seam for restricted Deep Agents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from fin_graph.agents.common.middleware.deepagents import restricted_deepagents_middleware


def make_agent(
    *,
    name: str,
    model: BaseChatModel,
    tools: Sequence[BaseTool] = (),
    system_prompt: str | None = None,
    response_format: Any = None,
    middleware: Sequence[AgentMiddleware[Any, Any]] = (),
    subagents: Sequence[AsyncSubAgent] = (),
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a Deep Agent exposing only caller-approved tools by default.

    Give ``checkpointer`` and ``store`` only to the outermost graph: nested
    agents inherit them at run time, which is what lets ``interrupt()`` inside a
    nested agent pause the whole run.
    """
    custom_middleware_names = {item.name for item in middleware}
    restricted_middleware = [
        item
        for item in restricted_deepagents_middleware()
        if item.name not in custom_middleware_names
    ]
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=[*restricted_middleware, *middleware],
        subagents=list(subagents),
        response_format=response_format,
        context_schema=context_schema,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
