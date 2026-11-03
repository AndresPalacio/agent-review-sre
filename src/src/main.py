"""Historical ``src.main`` API delegated to Fin Graph."""

from __future__ import annotations

from typing import Any

from fin_graph.adapters.reference_app.python_api import (
    create_workflow,
    parse_hedge_fund_response,
    run_hedge_fund_sync,
    start,
)


def run_hedge_fund(
    tickers: list[str],
    start_date: str,
    end_date: str,
    portfolio: dict[str, Any],
    show_reasoning: bool = False,
    selected_analysts: list[str] = [],  # noqa: B006 - exact historical signature
    model_name: str = "gpt-4.1",
    model_provider: str = "OpenAI",
) -> dict[str, Any]:
    return run_hedge_fund_sync(
        tickers,
        start_date,
        end_date,
        portfolio,
        show_reasoning,
        selected_analysts,
        model_name,
        model_provider,
    )


__all__ = [
    "create_workflow",
    "parse_hedge_fund_response",
    "run_hedge_fund",
    "start",
]
