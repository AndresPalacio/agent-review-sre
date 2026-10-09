"""Redact credentials from every tool result before the model or the checkpoint sees it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from sre_graph.safety.secrets import redact_text, redact_value

ToolCallResult = ToolMessage | Command[Any]


class GuardrailsMiddleware(AgentMiddleware[Any, Any]):
    """Replace secrets in tool results (content and artifact) with ``[REDACTED:<kind>]``.

    JSON content is redacted value by value and stays valid JSON.
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolCallResult],
    ) -> ToolCallResult:
        return _redacted(handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolCallResult]],
    ) -> ToolCallResult:
        return _redacted(await handler(request))


def _redacted(result: ToolCallResult) -> ToolCallResult:
    if not isinstance(result, ToolMessage):
        return result
    content = result.content
    return result.model_copy(
        update={
            "content": redact_text(content) if isinstance(content, str) else redact_value(content),
            "artifact": redact_value(result.artifact),
        }
    )
