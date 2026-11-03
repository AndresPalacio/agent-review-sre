"""Reference-application assembly for the nineteen-persona DeepAgents team."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from fin_graph.adapters.reference_app.portfolio import build_reference_portfolio_result
from fin_graph.agents.analysis.persona_team import (
    PersonaSnapshotServices,
    ToolSource,
    make_persona_analysis_supervisor,
)
from fin_graph.config.settings import Settings
from fin_graph.config.tracing import disable_external_tracing
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.mcp.runtime import with_configured_mcp
from fin_graph.llm.models import ModelRole, get_model
from fin_graph.runtime.analysis import _sync_registry
from fin_graph.workflows.analysis.snapshots import MarketSnapshotServices


def make_reference_analysis_supervisor(
    model: BaseChatModel,
    registry: ToolSource,
    context: RunContext,
    services: PersonaSnapshotServices,
    *,
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the persona team with the reference application's result mapping."""
    return make_persona_analysis_supervisor(
        model,
        registry,
        context,
        services,
        checkpointer=checkpointer,
        result_builder=build_reference_portfolio_result,
    )


def make_reference_analysis_runtime() -> tuple[
    CompiledStateGraph[Any, Any, Any, Any], RunContext
]:
    """Create the reference application's nineteen-persona analysis runtime."""
    disable_external_tracing()
    settings = Settings()
    registry = _sync_registry()
    context = registry.run_context()
    graph = make_reference_analysis_supervisor(
        get_model(ModelRole.REASONING, settings),
        registry,
        context,
        MarketSnapshotServices(),
    )
    return graph, context


async def make_reference_analysis_runtime_async() -> tuple[
    CompiledStateGraph[Any, Any, Any, Any], RunContext
]:
    """Create the reference runtime with asynchronous MCP discovery."""
    disable_external_tracing()
    settings = Settings()
    registry = await with_configured_mcp(default_registry())
    context = registry.run_context()
    graph = make_reference_analysis_supervisor(
        get_model(ModelRole.REASONING, settings),
        registry,
        context,
        MarketSnapshotServices(),
    )
    return graph, context
