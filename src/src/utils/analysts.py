"""Reference analyst ordering backed by the canonical native catalog."""

from __future__ import annotations

from fin_graph.domain.analysts import analyst_catalog

ANALYST_ORDER = [(item.display_name, item.id.value) for item in analyst_catalog()]


def get_agents_list() -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in analyst_catalog()]


def get_analyst_nodes() -> dict[str, tuple[str, object]]:
    import src.agents as agents

    values: dict[str, tuple[str, object]] = {}
    module_aliases = {
        "technical_analyst": "technical_analyst_agent",
        "fundamentals_analyst": "fundamentals_analyst_agent",
        "growth_analyst": "growth_analyst_agent",
        "news_sentiment_analyst": "news_sentiment_agent",
        "sentiment_analyst": "sentiment_analyst_agent",
        "valuation_analyst": "valuation_analyst_agent",
    }
    for item in analyst_catalog():
        function_name = module_aliases.get(item.id.value, f"{item.id.value}_agent")
        values[item.id.value] = (function_name, getattr(agents, function_name))
    return values


__all__ = ["ANALYST_ORDER", "get_agents_list", "get_analyst_nodes"]
