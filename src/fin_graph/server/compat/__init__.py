"""Composable router for the frozen AI Hedge Fund public surface."""

from fastapi import APIRouter, Depends

from fin_graph.server.compat import (
    algorithms,
    api_keys,
    backtests,
    basic,
    flows,
    hedge_fund,
    models,
    ollama,
    skills,
    v2_risk,
    v2_signals,
)
from fin_graph.server.compat.security import compatibility_guard


def compatibility_router(*, include_root: bool = True, include_health: bool = True) -> APIRouter:
    router = APIRouter(dependencies=[Depends(compatibility_guard)])
    if include_root:
        router.include_router(basic.root_router)
    if include_health:
        router.include_router(basic.health_router)
    router.include_router(basic.router)
    router.include_router(models.router)
    router.include_router(api_keys.router)
    router.include_router(ollama.router)
    router.include_router(skills.router)
    router.include_router(flows.router)
    router.include_router(hedge_fund.router)
    router.include_router(v2_signals.router)
    router.include_router(v2_risk.router)
    router.include_router(algorithms.router)
    router.include_router(backtests.router)
    return router


__all__ = ["compatibility_router"]
