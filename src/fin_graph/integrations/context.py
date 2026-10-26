"""The per-run context that carries integration clients to tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import ToolRuntime

from fin_graph.integrations.base import IntegrationNotConfiguredError


@dataclass(frozen=True)
class RunContext:
    """Clients for the integrations available in this run, and why the others are not.

    Pass it as the graph's runtime context. LangGraph never persists the
    context, so credentials stay out of checkpoints; build a fresh one for
    every invoke and every resume.
    """

    clients: Mapping[str, object] = field(default_factory=dict)
    unavailable: Mapping[str, str] = field(default_factory=dict)
    actor: str | None = None
    role: str | None = None

    def client[ClientT](self, integration: str, expected_type: type[ClientT]) -> ClientT:
        """The client for ``integration``; an error the model can read when there is none."""
        found = self.clients.get(integration)
        if found is None:
            reason = self.unavailable.get(integration, "it is not configured")
            raise IntegrationNotConfiguredError(
                f"{integration} is unavailable in this run: {reason}"
            )
        if not isinstance(found, expected_type):
            raise TypeError(
                f"{integration} client is {type(found).__name__}, expected {expected_type.__name__}"
            )
        return found


def require_client[ClientT](
    runtime: ToolRuntime[Any, Any], integration: str, expected_type: type[ClientT]
) -> ClientT:
    """The ``integration`` client from a tool's runtime context."""
    context = runtime.context
    if not isinstance(context, RunContext):
        raise IntegrationNotConfiguredError(
            f"{integration} is unavailable: this run was started without integration clients"
        )
    return context.client(integration, expected_type)
