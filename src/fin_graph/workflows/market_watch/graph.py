"""Scheduled market watch: read recent prices, apply the rules, and say whether to escalate.

``check → summarize``. No model is involved: every result comes from
:func:`~fin_graph.domain.market_watch.assess` on the provider's daily closes, and a provider
error leaves that ticker ``unknown`` instead of failing the run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from fin_graph.domain.market import Price
from fin_graph.domain.market_watch import (
    WatchResult,
    WatchRule,
    assess,
    watch_digest,
    watch_question,
)
from fin_graph.integrations.base import IntegrationError

#: Calendar days of history: a trading year for the drawdown plus margin for holidays.
HISTORY_DAYS = 400


class MarketWatchState(TypedDict, total=False):
    rules: list[dict[str, Any]]
    recent_results: list[dict[str, Any]]
    results: list[dict[str, Any]]
    escalate: bool
    question: str
    digest: str


def make_market_watch_graph(
    prices: Callable[[str, date, date], Awaitable[list[Price]]],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the watch; ``prices`` owns the lifetime of each provider request."""

    async def check(state: MarketWatchState) -> dict[str, Any]:
        rules = [WatchRule.model_validate(item) for item in state.get("rules", [])]
        today = clock().date()
        results: list[WatchResult] = []
        for rule in rules:
            try:
                history = await prices(rule.ticker, today - timedelta(days=HISTORY_DAYS), today)
            except (IntegrationError, TypeError) as exc:
                results.append(
                    WatchResult(ticker=rule.ticker, status="unknown", reasons=[type(exc).__name__])
                )
                continue
            results.append(assess(rule, history))
        triggered = [item for item in results if item.status == "triggered"]
        return {
            "results": [item.model_dump(mode="json") for item in results],
            "escalate": bool(triggered),
            "question": watch_question(triggered) if triggered else "",
        }

    def summarize(state: MarketWatchState) -> dict[str, Any]:
        rows = [*state.get("recent_results", []), *state.get("results", [])]
        results = [WatchResult.model_validate(item) for item in rows]
        return {"digest": watch_digest(results, clock())}

    graph = StateGraph(MarketWatchState)
    graph.add_node("check", check)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "check")
    graph.add_edge("check", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()
