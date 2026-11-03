"""Canonical production assembly for the financial-analysis workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from fin_graph.config.settings import Settings
from fin_graph.config.tracing import disable_external_tracing
from fin_graph.delivery.service import DeliveryService
from fin_graph.domain.portfolio import Portfolio
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.mcp.runtime import configured_mcp_path, with_configured_mcp
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.llm.models import ModelRole, get_model, missing_credentials
from fin_graph.memory.theses import ThesisMemory
from fin_graph.runtime.observation import Observation
from fin_graph.runtime.profile import resolve_analysis_profile
from fin_graph.runtime.trace import ExecutionTrace, observe_analysis_services
from fin_graph.safety.masking import MaskingPolicy
from fin_graph.safety.trading_limits import TradingLimits
from fin_graph.workflows.analysis.graph import (
    AnalysisDelivery,
    AnalysisLimits,
    _compile_analysis_graph,
)
from fin_graph.workflows.analysis.graph import ToolSource as AnalysisToolSource
from fin_graph.workflows.analysis.services import (
    AnalysisServices,
    AnalystMode,
    LangChainAnalysisServices,
    UnavailableAnalysisServices,
)


def _sync_registry() -> IntegrationRegistry:
    registry = default_registry()
    if configured_mcp_path(registry) is None:
        return registry
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(with_configured_mcp(registry))
    raise RuntimeError("Configured MCP requires await make_runtime_async() in an async caller")


def analyst_mode(settings: Settings) -> AnalystMode:
    """``FIN_ANALYST_MODE``; ``auto``: the fixed tool plan on Ollama, tool calling elsewhere."""
    if settings.fin_analyst_mode == "auto":
        return "tool_plan" if settings.llm_provider == "ollama" else "react"
    return settings.fin_analyst_mode


def _analysis_services(settings: Settings) -> AnalysisServices:
    """Model-backed services, or the deterministic ones when the provider cannot be used."""
    if missing_credentials(settings.llm_provider):
        return UnavailableAnalysisServices()
    try:
        models = {role: get_model(role, settings) for role in ModelRole}
    except Exception:
        return UnavailableAnalysisServices()
    return LangChainAnalysisServices(
        models, mode=analyst_mode(settings), masking_policy=MaskingPolicy.from_settings(settings)
    )


def make_analysis_graph(
    services: AnalysisServices,
    registry: AnalysisToolSource,
    *,
    delivery: AnalysisDelivery | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    limits: AnalysisLimits | None = None,
    checkpointer: BaseCheckpointSaver[Any] | Literal[False] | None = None,
    memory: ThesisMemory | None = None,
    portfolio: Portfolio | None = None,
    trading_limits: TradingLimits | None = None,
    orders_today: Callable[[], Awaitable[int]] | None = None,
    trace: ExecutionTrace | None = None,
    settings: Settings | None = None,
    verify_variant_pin: bool = True,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the analysis graph with traced model steps and pinned runtime budgets."""
    active = settings or Settings()
    profile = resolve_analysis_profile(limits=limits, verify_variant_pin=verify_variant_pin)
    assert isinstance(profile.limits, AnalysisLimits)
    return _compile_analysis_graph(
        observe_analysis_services(services),
        registry,
        delivery=delivery,
        clock=clock,
        limits=profile.limits,
        checkpointer=checkpointer,
        memory=memory,
        portfolio=portfolio
        or Portfolio(
            cash=active.initial_cash,
            margin_requirement=active.margin_requirement,
            currency=active.base_currency,
        ),
        trading_limits=trading_limits or TradingLimits.from_settings(active),
        orders_today=orders_today,
        instrument=Observation(profile, trace).wrap,
    )


def make_analysis_runtime() -> tuple[CompiledStateGraph[Any, Any, Any, Any], RunContext]:
    """A local analysis graph and fresh clients; credentials stay in RunContext, not state."""
    disable_external_tracing()
    settings = Settings()
    registry = _sync_registry()
    graph = make_analysis_graph(
        _analysis_services(settings),
        registry,
        delivery=DeliveryService.from_env(),
        settings=settings,
    )
    return graph, registry.run_context()


async def make_analysis_runtime_async() -> tuple[
    CompiledStateGraph[Any, Any, Any, Any], RunContext
]:
    """Async entry point for callers already running an event loop."""
    disable_external_tracing()
    settings = Settings()
    registry = await with_configured_mcp(default_registry())
    graph = make_analysis_graph(
        _analysis_services(settings),
        registry,
        delivery=DeliveryService.from_env(),
        settings=settings,
    )
    return graph, registry.run_context()


async def make_analysis_graph_async() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Studio target ("analysis"): MCP discovered asynchronously, clients opened per run."""
    disable_external_tracing()
    settings = Settings()
    registry = await with_configured_mcp(default_registry())
    return make_analysis_graph(
        _analysis_services(settings),
        registry,
        delivery=DeliveryService.from_env(),
        settings=settings,
    )
