"""Validate local MCP declarations without accepting literal credentials."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel


class McpConfigError(ValueError):
    """A server declaration is invalid; its contents are never echoed."""


class McpToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: set[Domain] = Field(default_factory=set)
    evidence_type: EvidenceType = EvidenceType.EVENTS
    side_effect_level: SideEffectLevel = SideEffectLevel.EXTERNAL
    idempotent: bool = False


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    enabled: bool = False
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_vars: list[str] = Field(default_factory=list)
    headers_from_env: dict[str, str] = Field(default_factory=dict)
    tools: dict[str, McpToolPolicy] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)

    @model_validator(mode="after")
    def validate_connection(self) -> McpServerConfig:
        if self.transport == "stdio":
            if not self.command or self.url or self.headers_from_env:
                raise ValueError("invalid stdio connection")
        else:
            if not self.url or self.command or self.args or self.env_vars:
                raise ValueError("invalid HTTP connection")
            parts = urlsplit(self.url)
            local = parts.hostname in ("localhost", "127.0.0.1", "::1")
            if (parts.scheme != "https" and not (local and parts.scheme == "http")) or (
                not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("invalid HTTP URL")
        for variable in [*self.env_vars, *self.headers_from_env.values()]:
            if not variable.isidentifier() or not variable.isupper():
                raise ValueError("invalid environment variable name")
        for arg in self.args:
            flag = arg.split("=", 1)[0].lower()
            if arg == "-H" or (
                flag.startswith("-")
                and re.search(
                    r"(?:token|secret|password|passwd|api[-_]?key|credential|auth|header)",
                    flag,
                )
            ):
                raise ValueError("credential arguments are not supported; use env_vars")
        return self


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servers: list[McpServerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self) -> McpConfig:
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("duplicate MCP server name")
        return self


def load_mcp_config(path: str | Path) -> McpConfig:
    """Read at most 64 KiB of JSON; report errors without copying configuration input."""
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 64 * 1024:
            raise McpConfigError("MCP config is too large")
        return McpConfig.model_validate(json.loads(raw))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError) and any(
            item["msg"] == "Value error, duplicate MCP server name"
            for item in exc.errors(include_input=False, include_context=False)
        ):
            raise McpConfigError("duplicate MCP server name") from None
        raise McpConfigError("invalid MCP configuration") from None
