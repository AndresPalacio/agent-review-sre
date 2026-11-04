"""Restricted replacements for Deep Agents' additive tool middleware."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool


class _NoFilesystemMiddleware(AgentMiddleware[Any, Any, Any]):
    """Replace Deep Agents' filesystem and shell tool slot with no tools."""

    tools: Sequence[BaseTool] = ()

    @property
    def name(self) -> str:
        return "FilesystemMiddleware"


class _NoSubAgentMiddleware(AgentMiddleware[Any, Any, Any]):
    """Replace Deep Agents' synchronous ``task`` tool slot with no tools."""

    tools: Sequence[BaseTool] = ()

    @property
    def name(self) -> str:
        return "SubAgentMiddleware"


def restricted_deepagents_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    """Return replacements that expose no additive Deep Agents tools."""

    return [_NoFilesystemMiddleware(), _NoSubAgentMiddleware()]
