"""The per-run context that carries integration clients to tools."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from langchain.tools import ToolRuntime

from fin_graph.integrations.base import IntegrationNotConfiguredError


@runtime_checkable
class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class RunContext:
    """Clients for the integrations available in this run, and why the others are not.

    Pass it as the graph's runtime context. LangGraph never persists the
    context, so credentials stay out of checkpoints; build a fresh one for
    every invoke and every resume.
    """

    clients: Mapping[str, object] = field(default_factory=dict)
    unavailable: Mapping[str, str] = field(default_factory=dict)
    owned_clients: tuple[object, ...] = ()
    actor: str | None = None
    role: str | None = None
    _closed_client_ids: set[int] = field(default_factory=set, init=False, repr=False, compare=False)

    async def aclose(self) -> None:
        """Attempt every unique close and retry only clients whose close failed."""
        attempted: set[int] = set()
        errors: list[BaseException] = []
        for client in self.owned_clients:
            identity = id(client)
            if identity in self._closed_client_ids or identity in attempted:
                continue
            attempted.add(identity)
            if isinstance(client, AsyncCloseable):
                try:
                    await client.aclose()
                except BaseException as exc:
                    errors.append(exc)
                else:
                    self._closed_client_ids.add(identity)
        if errors:
            raise BaseExceptionGroup("run context close failed", errors)

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


@asynccontextmanager
async def close_run_context[ContextT](context: ContextT) -> AsyncIterator[ContextT]:
    """Close a run context after one graph/tool operation; pass through test contexts."""
    try:
        yield context
    finally:
        if isinstance(context, RunContext):
            await context.aclose()


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
