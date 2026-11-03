"""Configured MCP discovery exposed through the same native registry boundary."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic_settings import BaseSettings, SettingsConfigDict

from fin_graph.domain.tools import attach_tool_meta, read_tool_meta
from fin_graph.integrations.base import Integration, IntegrationError, VerifyResult
from fin_graph.integrations.mcp.config import McpConfig, McpServerConfig, load_mcp_config
from fin_graph.integrations.mcp.loader import McpClientFactory, load_mcp_tools, verify_mcp
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.safety.secrets import redact_secrets, redact_value

_MAX_RESULT_BYTES = 2 * 1024 * 1024


class _Selection(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)
    mcp_config: Path | None = None


def configured_mcp_path(registry: IntegrationRegistry) -> Path | None:
    selected = _Selection(_env_file=registry.env_file).mcp_config
    if selected is not None:
        return selected
    default = Path("mcp-servers.json")
    return default if default.is_file() else None


def _credential_values(server: McpServerConfig) -> tuple[str, ...]:
    values = {
        os.environ[name]
        for name in [*server.env_vars, *server.headers_from_env.values()]
        if os.environ.get(name)
    }
    for value in tuple(values):
        scheme, separator, credential = value.partition(" ")
        if separator and scheme.lower() in {"bearer", "basic"} and credential:
            values.add(credential)
    return tuple(sorted(values, key=len, reverse=True))


def _bounded_tool(
    candidate: BaseTool, timeout: float, credentials: tuple[str, ...] = ()
) -> BaseTool:
    meta = read_tool_meta(candidate)
    if meta is None:
        raise ValueError("MCP tool metadata is missing")

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            for credential in credentials:
                value = value.replace(credential, "[REDACTED:credential]")
            return redact_secrets(value)
        if isinstance(value, dict):
            return {scrub(key): scrub(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [scrub(item) for item in value]
        return value

    if any(credential in candidate.name for credential in credentials):
        raise ValueError("MCP tool name contains credentials")

    async def invoke(config: RunnableConfig, **kwargs: Any) -> tuple[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                result = await candidate.ainvoke(
                    {
                        "name": candidate.name,
                        "args": kwargs,
                        "id": str(uuid4()),
                        "type": "tool_call",
                    },
                    config=config,
                )
            if isinstance(result, ToolMessage) and result.status == "error":
                raise IntegrationError("MCP tool returned an error")
            content = result.content if isinstance(result, ToolMessage) else result
            artifact = result.artifact if isinstance(result, ToolMessage) else None
            serialized = json.dumps([content, artifact], default=str, ensure_ascii=False)
            if len(serialized.encode("utf-8")) > _MAX_RESULT_BYTES:
                raise IntegrationError("MCP tool response exceeds 2 MiB")
            # Adapter artifacts may contain custom objects. Normalize before redaction
            # so later checkpoint/recording serialization cannot reveal their repr.
            content, artifact = json.loads(serialized)
            clean = scrub(redact_value(artifact if artifact is not None else content))
            rendered = content if isinstance(content, str) else json.dumps(content, default=str)
            return str(scrub(rendered))[:16000], clean
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise IntegrationError("MCP tool deadline exceeded") from exc
        except IntegrationError as exc:
            raise IntegrationError(str(scrub(str(exc)))) from exc
        except Exception as exc:
            # Remote exception strings can contain headers or server content.
            raise IntegrationError(f"MCP tool failed: {type(exc).__name__}") from exc

    schema = candidate.args_schema or candidate.get_input_schema()
    if not isinstance(schema, dict):
        schema = (
            schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema.schema()
        )
    clean_schema = scrub(schema)
    return attach_tool_meta(
        StructuredTool(
            name=candidate.name,
            description=str(scrub(candidate.description))[:8000],
            args_schema=clean_schema,
            coroutine=invoke,
            response_format="content_and_artifact",
            handle_tool_error=True,
        ),
        meta,
    )


@dataclass(frozen=True)
class _Client:
    server: McpServerConfig


class _ConfiguredIntegration(Integration[McpServerConfig, _Client]):
    id = "mcp"
    display_name = "MCP"

    def __init__(
        self,
        server: McpServerConfig,
        tools: list[BaseTool],
        failure: str | None,
        factory: McpClientFactory,
    ) -> None:
        self.server = server
        self.available_tools = tools
        self.failure = failure
        self.factory = factory

    def load_settings(self, env_file: str | None) -> McpServerConfig | None:
        if not self.server.enabled:
            return None
        if self.failure:
            raise IntegrationError(self.failure)
        return self.server

    def build_client(self, settings: McpServerConfig) -> _Client:
        return _Client(settings)

    def tools(self) -> list[BaseTool]:
        return self.available_tools

    async def verify(self, client: _Client) -> VerifyResult:
        checks = await verify_mcp(McpConfig(servers=[client.server]), client_factory=self.factory)
        check = checks[0]
        return VerifyResult(self.id, check.status, check.detail)


async def with_configured_mcp(
    registry: IntegrationRegistry,
    *,
    config_path: str | Path | None = None,
    client_factory: McpClientFactory = MultiServerMCPClient,
) -> IntegrationRegistry:
    """Discover explicitly enabled servers once; tool sessions open/close per call.

    Bad declarations fail startup instead of silently ignoring safety policy. A
    connection failure is isolated to its integration and shown as unavailable.
    Undeclared/mutating tools retain restrictive metadata and existing specialist
    filters exclude them from analysis and chat read-tool sets.
    """
    path = Path(config_path) if config_path is not None else configured_mcp_path(registry)
    if path is None:
        return registry
    config = load_mcp_config(path)
    loaded = await load_mcp_tools(config, client_factory=client_factory)
    integrations: list[Integration[Any, Any]] = []
    for server in config.servers:
        identity = f"mcp:{server.name}"
        selected = [
            tool
            for tool in loaded.tools
            if (meta := read_tool_meta(tool)) is not None and meta.integration == identity
        ]
        failure = loaded.failures.get(server.name)
        try:
            bounded = [
                _bounded_tool(tool, server.timeout_seconds, _credential_values(server))
                for tool in selected
            ]
        except Exception:
            bounded = []
            failure = "MCP tool definitions are invalid"
        # Class identity fulfills the native Integration ClassVar contract.
        integration_type = type(
            "ConfiguredMcpIntegration",
            (_ConfiguredIntegration,),
            {"id": identity, "display_name": f"MCP {server.name}"},
        )
        integrations.append(
            integration_type(
                server,
                bounded,
                failure,
                client_factory,
            )
        )
    return registry.extended(integrations)
