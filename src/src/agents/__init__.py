"""Legacy analyst module names backed by the full native compatibility workflow."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from fin_graph.domain.analysts import analyst_catalog

_MODULES = {
    "aswath_damodaran": "aswath_damodaran_agent",
    "ben_graham": "ben_graham_agent",
    "bill_ackman": "bill_ackman_agent",
    "cathie_wood": "cathie_wood_agent",
    "charlie_munger": "charlie_munger_agent",
    "michael_burry": "michael_burry_agent",
    "mohnish_pabrai": "mohnish_pabrai_agent",
    "nassim_taleb": "nassim_taleb_agent",
    "peter_lynch": "peter_lynch_agent",
    "phil_fisher": "phil_fisher_agent",
    "rakesh_jhunjhunwala": "rakesh_jhunjhunwala_agent",
    "stanley_druckenmiller": "stanley_druckenmiller_agent",
    "warren_buffett": "warren_buffett_agent",
    "technicals": "technical_analyst_agent",
    "fundamentals": "fundamentals_analyst_agent",
    "growth_agent": "growth_analyst_agent",
    "news_sentiment": "news_sentiment_agent",
    "sentiment": "sentiment_analyst_agent",
    "valuation": "valuation_analyst_agent",
    "risk_manager": "risk_management_agent",
    "portfolio_manager": "portfolio_management_agent",
}


def _delegate(state: Any, agent_id: str) -> Any:
    """Run only the selected persona through the installed workflow adapter."""

    from src.main import create_workflow

    value = agent_id.removesuffix("_agent")
    aliases = {
        "technical_analyst": "technical_analyst",
        "fundamentals_analyst": "fundamentals_analyst",
        "growth_analyst": "growth_analyst",
        "news_sentiment": "news_sentiment_analyst",
        "sentiment_analyst": "sentiment_analyst",
        "valuation_analyst": "valuation_analyst",
    }
    known = {item.id.value for item in analyst_catalog()}
    selected = aliases.get(value, value)
    if selected not in known or selected in {"risk_management", "portfolio_management"}:
        return state
    return create_workflow([selected]).compile().invoke(state)


def _install(module_name: str, function_name: str) -> None:
    qualified = f"{__name__}.{module_name}"
    module = ModuleType(qualified)

    def agent(state: Any, agent_id: str = function_name) -> Any:
        return _delegate(state, agent_id)

    agent.__name__ = function_name
    agent.__qualname__ = function_name
    setattr(module, function_name, agent)
    module.__all__ = [function_name]
    sys.modules[qualified] = module
    globals()[function_name] = agent


for _module_name, _function_name in _MODULES.items():
    _install(_module_name, _function_name)

__all__ = list(_MODULES.values())
