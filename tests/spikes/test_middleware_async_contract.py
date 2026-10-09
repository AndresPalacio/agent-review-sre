"""Spike: custom middleware must implement both the sync and the async hook."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message

ToolCallResult = ToolMessage | Command[Any]


@tool
async def ping() -> str:
    """Answer pong."""
    return "pong"


class SyncOnlyMiddleware(AgentMiddleware[Any, Any]):
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolCallResult],
    ) -> ToolCallResult:
        return handler(request)


class SyncAndAsyncMiddleware(SyncOnlyMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolCallResult]],
    ) -> ToolCallResult:
        return await handler(request)


def _script() -> ScriptedChatModel:
    return ScriptedChatModel(responses=[tool_call_message("ping", {}, "p1"), AIMessage("done")])


async def test_sync_only_middleware_fails_under_ainvoke() -> None:
    agent = make_agent(name="a", model=_script(), tools=[ping], middleware=[SyncOnlyMiddleware()])

    with pytest.raises(NotImplementedError):
        await agent.ainvoke({"messages": [HumanMessage("go")]})


async def test_middleware_with_both_hooks_works_under_ainvoke() -> None:
    agent = make_agent(
        name="a", model=_script(), tools=[ping], middleware=[SyncAndAsyncMiddleware()]
    )

    result = await agent.ainvoke({"messages": [HumanMessage("go")]})

    assert result["messages"][-1].content == "done"
