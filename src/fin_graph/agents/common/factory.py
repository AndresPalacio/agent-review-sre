"""The single construction seam for agents: a thin, explicit wrapper over ``create_agent``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer


def make_agent(
    *,
    name: str,
    model: BaseChatModel,
    tools: Sequence[BaseTool] = (),
    system_prompt: str | None = None,
    response_format: Any = None,
    middleware: Sequence[AgentMiddleware[Any, Any]] = (),
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a tool-calling agent graph.

    Give ``checkpointer`` and ``store`` only to the outermost graph: nested
    agents inherit them at run time, which is what lets ``interrupt()`` inside a
    nested agent pause the whole run.
    """
    return create_agent(
        model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=list(middleware),
        response_format=response_format,
        context_schema=context_schema,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
