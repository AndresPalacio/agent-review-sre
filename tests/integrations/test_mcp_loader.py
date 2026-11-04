from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, read_tool_meta
from fin_graph.integrations.base import VerifyStatus
from fin_graph.integrations.mcp.config import McpConfigError, load_mcp_config
from fin_graph.integrations.mcp.loader import load_mcp_tools, verify_mcp


@tool
def lookup(query: str) -> str:
    """Look up an item."""
    return query


class FakeAdapter:
    def __init__(self, connections: dict[str, Any]) -> None:
        self.connections = connections

    async def get_tools(self, *, server_name: str) -> list[BaseTool]:
        if server_name == "broken":
            raise ConnectionError("private-server-secret")
        return [lookup.model_copy(deep=True)]


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_mcp_config_rejects_literal_secrets_and_duplicate_names(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mcp.json",
        {
            "servers": [
                {
                    "name": "local",
                    "transport": "stdio",
                    "command": "python",
                    "env": {"API_KEY": "literal-private-value"},
                }
            ]
        },
    )
    with pytest.raises(McpConfigError) as caught:
        load_mcp_config(path)
    assert "literal-private-value" not in str(caught.value)

    _write(
        path,
        {
            "servers": [
                {
                    "name": "local",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-H", "Authorization: Bearer literal-private-value"],
                }
            ]
        },
    )
    with pytest.raises(McpConfigError) as caught:
        load_mcp_config(path)
    assert "literal-private-value" not in str(caught.value)

    _write(
        path,
        {
            "servers": [
                {"name": "local", "transport": "stdio", "command": "python"},
                {"name": "local", "transport": "stdio", "command": "python"},
            ]
        },
    )
    with pytest.raises(McpConfigError, match="duplicate"):
        load_mcp_config(path)

    _write(
        path,
        {
            "servers": [
                {
                    "name": "local",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["--api-key", "literal-private-value"],
                }
            ]
        },
    )
    with pytest.raises(McpConfigError) as caught:
        load_mcp_config(path)
    assert "literal-private-value" not in str(caught.value)


async def test_mcp_loader_defaults_to_external_and_isolates_a_failed_server(tmp_path: Path) -> None:
    config = load_mcp_config(
        _write(
            tmp_path / "mcp.json",
            {
                "servers": [
                    {"name": "safe", "transport": "stdio", "command": "python", "enabled": True},
                    {"name": "broken", "transport": "stdio", "command": "python", "enabled": True},
                ]
            },
        )
    )

    result = await load_mcp_tools(config, client_factory=FakeAdapter)

    assert [tool.name for tool in result.tools] == ["safe_lookup"]
    meta = read_tool_meta(result.tools[0])
    assert meta.side_effect_level is SideEffectLevel.EXTERNAL
    assert meta.evidence_type is EvidenceType.FINANCIALS
    assert result.failures == {"broken": "ConnectionError: MCP server unavailable"}
    assert (await verify_mcp(config, client_factory=FakeAdapter))[0].status is VerifyStatus.OK


async def test_mcp_policy_sets_domains_and_read_only_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_FINANCE_AUTH", "Bearer synthetic-token")
    connections: list[dict[str, Any]] = []

    def factory(value: dict[str, Any]) -> FakeAdapter:
        connections.append(value)
        return FakeAdapter(value)

    config = load_mcp_config(
        _write(
            tmp_path / "mcp.json",
            {
                "servers": [
                    {
                        "name": "financial_research",
                        "enabled": True,
                        "transport": "http",
                        "url": "https://mcp.example/mcp",
                        "headers_from_env": {"Authorization": "MCP_FINANCE_AUTH"},
                        "tools": {
                            "lookup": {
                                "domains": ["fundamentals"],
                                "evidence_type": "financials",
                                "side_effect_level": "read_only",
                                "idempotent": True,
                            }
                        },
                    }
                ]
            },
        )
    )

    result = await load_mcp_tools(config, client_factory=factory)

    meta = read_tool_meta(result.tools[0])
    assert meta.integration == "mcp:financial_research"
    assert meta.domains == frozenset({Domain.FUNDAMENTALS})
    assert meta.side_effect_level is SideEffectLevel.READ_ONLY
    assert connections[0]["financial_research"] == {
        "transport": "streamable_http",
        "url": "https://mcp.example/mcp",
        "headers": {"Authorization": "Bearer synthetic-token"},
        "timeout": 15.0,
        "session_kwargs": {"read_timeout_seconds": timedelta(seconds=15)},
    }


async def test_mcp_missing_environment_is_isolated(tmp_path: Path) -> None:
    config = load_mcp_config(
        _write(
            tmp_path / "mcp.json",
            {
                "servers": [
                    {"name": "safe", "transport": "stdio", "command": "python", "enabled": True},
                    {
                        "name": "missing",
                        "transport": "stdio",
                        "command": "python",
                        "enabled": True,
                        "env_vars": ["MCP_DEFINITELY_ABSENT_123"],
                    },
                ]
            },
        )
    )

    result = await load_mcp_tools(config, client_factory=FakeAdapter)

    assert [tool.name for tool in result.tools] == ["safe_lookup"]
    assert result.failures == {"missing": "ValueError: MCP server unavailable"}


async def test_stdio_mcp_session_has_a_read_timeout(tmp_path: Path) -> None:
    connections: list[dict[str, Any]] = []

    def factory(value: dict[str, Any]) -> FakeAdapter:
        connections.append(value)
        return FakeAdapter(value)

    config = load_mcp_config(
        _write(
            tmp_path / "mcp.json",
            {
                "servers": [
                    {
                        "name": "local",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "python",
                        "timeout_seconds": 7,
                    }
                ]
            },
        )
    )
    await load_mcp_tools(config, client_factory=factory)
    assert connections[0]["local"]["session_kwargs"] == {
        "read_timeout_seconds": timedelta(seconds=7)
    }


def test_mcp_rejects_insecure_remote_url(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mcp.json",
        {
            "servers": [
                {
                    "name": "remote",
                    "enabled": True,
                    "transport": "http",
                    "url": "http://mcp.example/mcp",
                }
            ]
        },
    )
    with pytest.raises(McpConfigError, match="invalid"):
        load_mcp_config(path)


def test_mcp_rejects_malformed_url_without_echoing_it(tmp_path: Path) -> None:
    malformed = "https://[private-secret"
    path = _write(
        tmp_path / "mcp.json",
        {"servers": [{"name": "remote", "transport": "http", "url": malformed}]},
    )
    with pytest.raises(McpConfigError) as caught:
        load_mcp_config(path)
    assert malformed not in str(caught.value)


async def test_mcp_loader_discovers_a_real_local_stdio_tool(tmp_path: Path) -> None:
    script = tmp_path / "sample_mcp.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('sample')\n"
        "@server.tool()\n"
        "def ping(message: str) -> str:\n"
        "    return message\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    config = load_mcp_config(
        _write(
            tmp_path / "mcp.json",
            {
                "servers": [
                    {
                        "name": "sample",
                        "enabled": True,
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(script)],
                        "timeout_seconds": 20,
                    }
                ]
            },
        )
    )

    result = await load_mcp_tools(config)

    assert result.failures == {}
    assert [candidate.name for candidate in result.tools] == ["sample_ping"]
    assert read_tool_meta(result.tools[0]).side_effect_level is SideEffectLevel.EXTERNAL
    assert "hello" in str(await result.tools[0].ainvoke({"message": "hello"}))
