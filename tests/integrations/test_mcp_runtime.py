import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta, attach_tool_meta
from fin_graph.integrations.mcp.runtime import _bounded_tool, with_configured_mcp
from fin_graph.integrations.registry import IntegrationRegistry


class Adapter:
    def __init__(self, connections: dict[str, Any]):
        pass

    async def get_tools(self, *, server_name: str):
        if server_name == "broken":
            raise RuntimeError("password=private")

        @tool(response_format="content_and_artifact")
        async def filings(ticker: str) -> tuple[str, Any]:
            """Read company filings."""
            return "password=private", {"ticker": ticker, "token": "private", "items": ["10-K"]}

        @tool
        async def write() -> str:
            """An undeclared mutation."""
            raise AssertionError("must never be called")

        return [filings, write]


async def test_configured_mcp_routes_reads_and_sanitizes_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "financial",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "not-executed",
                        "tools": {
                            "filings": {
                                "domains": ["fundamentals"],
                                "evidence_type": "financials",
                                "side_effect_level": "read_only",
                                "idempotent": True,
                            }
                        },
                    },
                    {
                        "name": "broken",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "not-executed",
                    },
                    {"name": "disabled", "transport": "stdio", "command": "not-executed"},
                ]
            }
        )
    )
    registry = await with_configured_mcp(
        IntegrationRegistry([], env_file=None), config_path=path, client_factory=Adapter
    )
    context = registry.run_context()
    assert registry.ids == ("mcp:financial", "mcp:broken", "mcp:disabled")
    assert "mcp:broken" in context.unavailable and "private" not in str(context.unavailable)
    tools = registry.tools(context, domains={Domain.FUNDAMENTALS})
    assert [item.name for item in tools] == ["financial_filings"]
    result = await tools[0].ainvoke(
        {
            "name": "financial_filings",
            "id": "read1",
            "type": "tool_call",
            "args": {"ticker": "AAPL"},
        }
    )
    assert isinstance(result, ToolMessage)
    assert result.artifact["ticker"] == "AAPL"
    assert "private" not in str(result)
    checks = await registry.verify_all(context)
    assert [item.status.value for item in checks] == ["ok", "failed", "not_configured"]


async def test_no_config_leaves_native_registry_unchanged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    registry = IntegrationRegistry([], env_file=None)
    assert await with_configured_mcp(registry) is registry


@pytest.mark.parametrize("behavior", ["timeout", "oversize", "failure"])
async def test_mcp_bounds_are_reported_as_sanitized_tool_errors(behavior: str) -> None:
    @tool
    async def remote() -> str:
        """A bounded remote read."""
        if behavior == "timeout":
            await asyncio.Event().wait()
        if behavior == "failure":
            raise RuntimeError("password=private")
        return "x" * (2 * 1024 * 1024 + 1)

    candidate = attach_tool_meta(
        remote,
        ToolMeta(
            integration="mcp:test",
            domains=frozenset({Domain.FUNDAMENTALS}),
            evidence_type=EvidenceType.FINANCIALS,
            side_effect_level=SideEffectLevel.READ_ONLY,
            idempotent=True,
        ),
    )
    result = await _bounded_tool(candidate, 0.01).ainvoke(
        {"name": "remote", "id": "bounded", "type": "tool_call", "args": {}}
    )
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "private" not in str(result) and len(str(result)) < 1000


async def test_configured_opaque_credentials_never_leave_mcp_boundary(tmp_path, monkeypatch):
    secret = "opaque-unformatted-credential-zz882299"
    monkeypatch.setenv("MCP_PROBE_SECRET", "Bearer " + secret)

    class EchoAdapter:
        def __init__(self, connections):
            pass

        async def get_tools(self, *, server_name):
            @tool
            async def echo() -> str:
                """Read a diagnostic."""
                return os.environ["MCP_PROBE_SECRET"].split(" ", 1)[1]

            echo.description = "Diagnostics " + secret
            echo.args_schema = {"type": "object", "properties": {}, "description": secret}
            return [echo]

    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "echo",
                        "enabled": True,
                        "transport": "http",
                        "url": "https://mcp.test",
                        "headers_from_env": {"Authorization": "MCP_PROBE_SECRET"},
                        "tools": {
                            "echo": {
                                "domains": ["fundamentals"],
                                "side_effect_level": "read_only",
                            }
                        },
                    }
                ]
            }
        )
    )
    registry = await with_configured_mcp(
        IntegrationRegistry([], env_file=None), config_path=path, client_factory=EchoAdapter
    )
    selected = registry.tools(registry.run_context())[0]
    assert secret not in selected.description and secret not in str(selected.args_schema)
    result = await selected.ainvoke(
        {"name": selected.name, "id": "opaque", "type": "tool_call", "args": {}}
    )
    assert secret not in str(result)


async def test_mcp_normalizes_custom_artifacts_before_credential_redaction() -> None:
    secret = "opaque-artifact-credential-882299"

    @dataclass
    class Box:
        text: str

    @tool(response_format="content_and_artifact")
    async def custom() -> tuple[str, Any]:
        """Return a custom adapter artifact."""
        return "ok", {"nested": Box(secret)}

    attach_tool_meta(
        custom,
        ToolMeta(
            integration="mcp:test",
            domains=frozenset({Domain.FUNDAMENTALS}),
            evidence_type=EvidenceType.FINANCIALS,
            side_effect_level=SideEffectLevel.READ_ONLY,
            idempotent=True,
        ),
    )
    result = await _bounded_tool(custom, 1, (secret,)).ainvoke(
        {"name": "custom", "id": "custom", "type": "tool_call", "args": {}}
    )
    assert secret not in str(result)
    json.dumps(result.artifact)
