"""Turn an async function into an integration tool with metadata and readable errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

from sre_graph.domain.tools import ToolMeta, attach_tool_meta

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
