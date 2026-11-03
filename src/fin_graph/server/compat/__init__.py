"""Composable router for the frozen AI Hedge Fund public surface."""

from fastapi import APIRouter, Depends

from fin_graph.server.compat import api_keys, basic, flows, hedge_fund, models, ollama, skills
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
    return router


__all__ = ["compatibility_router"]
