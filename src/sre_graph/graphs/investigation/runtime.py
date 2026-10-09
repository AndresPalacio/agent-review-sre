"""Build the same investigation graph for Studio and the local CLI."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from sre_graph.config.settings import Settings
from sre_graph.config.tracing import disable_external_tracing
from sre_graph.delivery.service import DeliveryService
from sre_graph.graphs.investigation.graph import make_investigation_graph
from sre_graph.graphs.investigation.services import (
    InvestigationServices,
    LangChainServices,
    UnavailableServices,
)
from sre_graph.integrations.catalog import default_registry
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.mcp.runtime import configured_mcp_path, with_configured_mcp
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.llm.models import ModelRole, get_model
from sre_graph.safety.masking import MaskingPolicy


def make_runtime() -> tuple[CompiledStateGraph[Any, Any, Any, Any], RunContext]:
    """Create a graph and fresh clients; credentials stay in RunContext, not graph state."""
    disable_external_tracing()
    settings = Settings()
    registry = _sync_registry()
    services = _services(settings)
    graph = make_investigation_graph(services, registry, delivery=DeliveryService.from_env())
    return graph, registry.run_context()


def make_runtime_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Studio target, compiled without a checkpointer or prebuilt provider clients."""
    disable_external_tracing()
    settings = Settings()
    services = _services(settings)
    return make_investigation_graph(services, _sync_registry(), delivery=DeliveryService.from_env())


def _sync_registry() -> IntegrationRegistry:
    registry = default_registry()
    if configured_mcp_path(registry) is None:
        return registry
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(with_configured_mcp(registry))
    raise RuntimeError("Configured MCP requires await make_runtime_async() in an async caller")


async def make_runtime_async() -> tuple[CompiledStateGraph[Any, Any, Any, Any], RunContext]:
    """Async entry point for callers already running an event loop."""
    disable_external_tracing()
    registry = await with_configured_mcp(default_registry())
    graph = make_investigation_graph(
        _services(Settings()), registry, delivery=DeliveryService.from_env()
    )
    return graph, registry.run_context()


async def make_runtime_graph_async() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Studio discovers MCP asynchronously; the graph opens native clients per run."""
    disable_external_tracing()
    registry = await with_configured_mcp(default_registry())
    return make_investigation_graph(
        _services(Settings()), registry, delivery=DeliveryService.from_env()
    )


def _services(settings: Settings) -> InvestigationServices:
    try:
        models = {role: get_model(role, settings) for role in ModelRole}
    except Exception:
        return UnavailableServices()
    return LangChainServices(models, masking_policy=MaskingPolicy.from_settings(settings))
