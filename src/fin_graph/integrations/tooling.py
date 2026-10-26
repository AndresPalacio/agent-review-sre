"""Turn an async function into an integration tool with metadata and readable errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool

from fin_graph.domain.tools import ToolMeta, attach_tool_meta

ToolFunction = Callable[..., Awaitable[tuple[str, Any]]]


def integration_tool(meta: ToolMeta) -> Callable[[ToolFunction], BaseTool]:
    """Decorate an async function into a tool tagged with ``meta``.

    The function returns ``(content, artifact)``: compact text for the model
    and the full (capped) data kept on the ``ToolMessage``. Its docstring needs
    a Google-style ``Args:`` section; those lines become the argument
    descriptions the model sees. Raising ``IntegrationError`` gives the model
    an error message instead of failing the run. The tool is async-only, so
    run agents with ``ainvoke`` or ``astream``.
    """

    def decorate(func: ToolFunction) -> BaseTool:
        built = tool(func, response_format="content_and_artifact", parse_docstring=True)
        built.handle_tool_error = True
        return attach_tool_meta(built, meta)

    return decorate


async def invoke_tool(
    built: BaseTool, args: dict[str, Any], context: object, *, call_id: str
) -> ToolMessage:
    """Run ``built`` outside an agent, injecting a runtime that carries ``context``.

    Used when the graph, not the model, decides which tools to call (the fixed tool plan).
    The result is the same ``ToolMessage`` an agent's tool node would produce, artifact
    included, and tool errors come back as an error message, never as an exception.
    """
    runtime: ToolRuntime[Any, Any] = ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id=call_id,
        store=None,
    )
    message = await built.ainvoke(
        {
            "type": "tool_call",
            "id": call_id,
            "name": built.name,
            "args": {**args, "runtime": runtime},
        }
    )
    if not isinstance(message, ToolMessage):
        raise TypeError(f"{built.name} returned {type(message).__name__}, not a ToolMessage")
    return message
