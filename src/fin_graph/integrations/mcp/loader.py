"""Load MCP tools one server at a time and attach routing/safety metadata."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from fin_graph.domain.tools import ToolMeta, attach_tool_meta
from fin_graph.integrations.base import VerifyResult, VerifyStatus
from fin_graph.integrations.mcp.config import McpConfig, McpServerConfig, McpToolPolicy


class McpToolClient(Protocol):
    async def get_tools(self, *, server_name: str) -> list[BaseTool]: ...


type McpClientFactory = Callable[[dict[str, Any]], McpToolClient]


@dataclass
class McpLoadResult:
    tools: list[BaseTool] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


def _connection(server: McpServerConfig) -> dict[str, Any]:
    session_kwargs = {"read_timeout_seconds": timedelta(seconds=server.timeout_seconds)}
    if server.transport == "stdio":
        env = {name: os.environ[name] for name in server.env_vars if name in os.environ}
        for inherited in ("PATH", "SystemRoot"):
            if inherited in os.environ:
                env[inherited] = os.environ[inherited]
        missing = set(server.env_vars) - os.environ.keys()
        if missing:
            raise ValueError("MCP server environment is incomplete")
        return {
            "transport": "stdio",
            "command": server.command,
            "args": server.args,
            "env": env,
            "session_kwargs": session_kwargs,
        }
    headers = {}
    for name, variable in server.headers_from_env.items():
        if variable not in os.environ:
            raise ValueError("MCP server environment is incomplete")
        headers[name] = os.environ[variable]
    return {
        "transport": "streamable_http",
        "url": server.url,
        "headers": headers,
        "timeout": server.timeout_seconds,
        "session_kwargs": session_kwargs,
    }


async def load_mcp_tools(
    config: McpConfig,
    *,
    client_factory: McpClientFactory = MultiServerMCPClient,
) -> McpLoadResult:
    """Connect only enabled servers; a failing server cannot remove healthy tools."""
    result = McpLoadResult()
    names: set[str] = set()
    for server in config.servers:
        if not server.enabled:
            continue
        try:
            client = client_factory({server.name: _connection(server)})
            loaded = await asyncio.wait_for(
                client.get_tools(server_name=server.name), timeout=server.timeout_seconds
            )
            prepared: list[BaseTool] = []
            for candidate in loaded:
                original = candidate.name
                name = f"{server.name}_{original}"
                if name in names or any(tool.name == name for tool in prepared):
                    raise ValueError("duplicate MCP tool name")
                policy = server.tools.get(original, McpToolPolicy())
                candidate.name = name
                prepared.append(
                    attach_tool_meta(
                        candidate,
                        ToolMeta(
                            integration=f"mcp:{server.name}",
                            domains=frozenset(policy.domains),
                            evidence_type=policy.evidence_type,
                            side_effect_level=policy.side_effect_level,
                            idempotent=policy.idempotent,
                        ),
                    )
                )
            result.tools.extend(prepared)
            names.update(tool.name for tool in prepared)
        except Exception as exc:
            result.failures[server.name] = f"{type(exc).__name__}: MCP server unavailable"
    return result


async def verify_mcp(
    config: McpConfig,
    *,
    client_factory: McpClientFactory = MultiServerMCPClient,
) -> list[VerifyResult]:
    """Enumerate tools as a read-only connectivity check; never invoke them."""
    result = await load_mcp_tools(config, client_factory=client_factory)
    checks: list[VerifyResult] = []
    for server in config.servers:
        if not server.enabled:
            checks.append(VerifyResult(server.name, VerifyStatus.NOT_CONFIGURED, "not configured"))
        elif server.name in result.failures:
            checks.append(
                VerifyResult(server.name, VerifyStatus.FAILED, result.failures[server.name])
            )
        else:
            count = sum(tool.name.startswith(server.name + "_") for tool in result.tools)
            checks.append(VerifyResult(server.name, VerifyStatus.OK, f"{count} tools discovered"))
    return checks
