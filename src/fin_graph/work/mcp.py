"""MCP tools for the local, single-process gateway and its fixed trusted actor."""

from collections.abc import Awaitable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fin_graph.work.contracts import (
    CancelWork,
    StartWork,
    UpdateWork,
    WorkConflict,
    WorkUnavailable,
    WorkView,
)
from fin_graph.work.service import WorkService


async def _tool_result(operation: Awaitable[WorkView]) -> dict[str, Any]:
    try:
        return (await operation).model_dump(mode="json")
    except WorkConflict as exc:
        raise ToolError(f"409 conflict: {exc}") from None
    except WorkUnavailable as exc:
        raise ToolError(f"503 unavailable: {exc}") from None
    except PermissionError as exc:
        raise ToolError(f"403 forbidden: {exc}") from None


def create_mcp_server(service: WorkService, *, actor: str = "local-demo") -> FastMCP[None]:
    """Mount this server in the HTTP gateway; do not run a second ledger writer.

    The host app owns the session-manager lifespan. Identity is supplied by the
    gateway operator, never by tool arguments or transport headers.
    """
    server: FastMCP[None] = FastMCP(
        "fin-graph Work local pilot",
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    @server.tool()
    async def work_start(command: StartWork) -> dict[str, Any]:
        """Start work; reuse the same command_id to recover an uncertain response."""
        return await _tool_result(service.start(command, actor=actor))

    @server.tool()
    async def work_get(work_id: str) -> dict[str, Any]:
        """Read current work status, revision, result, and child execution identifiers."""
        return await _tool_result(service.get(work_id, actor=actor))

    @server.tool()
    async def work_update(work_id: str, command: UpdateWork) -> dict[str, Any]:
        """Steer active work using its expected revision and a unique command_id."""
        return await _tool_result(service.update(work_id, command, actor=actor))

    @server.tool()
    async def work_cancel(work_id: str, command: CancelWork) -> dict[str, Any]:
        """Request cancellation of a work tree using its expected revision."""
        return await _tool_result(service.cancel(work_id, command, actor=actor))

    return server
