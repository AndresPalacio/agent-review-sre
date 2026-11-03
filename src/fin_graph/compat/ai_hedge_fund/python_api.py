"""Python-call compatibility for the original ``src.main`` entrypoints.

This module only translates the historical dictionaries into the native, typed
compatibility graph.  All analysis remains in :mod:`fin_graph`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fin_graph.compat.ai_hedge_fund.adapters import to_native_analysis_request
from fin_graph.compat.ai_hedge_fund.schemas import (
    GraphNode,
    HedgeFundRequest,
    ModelProvider,
    PortfolioPosition,
)


def parse_hedge_fund_response(response: object) -> dict[str, Any] | None:
    """Retain the reference parser's forgiving ``None`` failure result."""

    try:
        value = json.loads(response) if isinstance(response, str | bytes | bytearray) else response
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _provider(value: str) -> ModelProvider:
    folded = value.replace("_", "").replace("-", "").replace(" ", "").casefold()
    for provider in ModelProvider:
        candidates = (provider.name, provider.value)
        if any(
            item.replace("_", "").replace("-", "").replace(" ", "").casefold() == folded
            for item in candidates
        ):
            return provider
    return ModelProvider.OPENAI


def _positions(portfolio: Mapping[str, Any]) -> list[PortfolioPosition]:
    values: list[PortfolioPosition] = []
    raw_positions = portfolio.get("positions")
    if not isinstance(raw_positions, Mapping):
        return values
    for raw_ticker, raw_position in raw_positions.items():
        if not isinstance(raw_position, Mapping):
            continue
        ticker = str(raw_ticker).strip().upper()
        long_quantity = int(raw_position.get("long", 0) or 0)
        short_quantity = int(raw_position.get("short", 0) or 0)
        if long_quantity:
            basis = float(raw_position.get("long_cost_basis", 0) or 0)
            if basis > 0:
                values.append(
                    PortfolioPosition(ticker=ticker, quantity=long_quantity, trade_price=basis)
                )
        if short_quantity:
            basis = float(raw_position.get("short_cost_basis", 0) or 0)
            if basis > 0:
                values.append(
                    PortfolioPosition(ticker=ticker, quantity=-short_quantity, trade_price=basis)
                )
    return values


def make_request(
    tickers: Sequence[str],
    start_date: str,
    end_date: str,
    portfolio: Mapping[str, Any],
    selected_analysts: Sequence[str] | None,
    model_name: str,
    model_provider: str,
) -> HedgeFundRequest:
    """Translate one historical function call into the frozen HTTP contract."""

    return HedgeFundRequest(
        tickers=list(tickers),
        start_date=start_date,
        end_date=end_date,
        initial_cash=float(portfolio.get("cash", 100_000) or 0),
        margin_requirement=float(portfolio.get("margin_requirement", 0) or 0),
        portfolio_positions=_positions(portfolio),
        graph_nodes=[GraphNode(id=item) for item in (selected_analysts or ())],
        graph_edges=[],
        model_name=model_name,
        model_provider=_provider(model_provider),
    )


async def run_hedge_fund_async(
    tickers: Sequence[str],
    start_date: str,
    end_date: str,
    portfolio: Mapping[str, Any],
    show_reasoning: bool = False,
    selected_analysts: Sequence[str] | None = None,
    model_name: str = "gpt-4.1",
    model_provider: str = "OpenAI",
) -> dict[str, Any]:
    """Execute the native DeepAgents compatibility graph."""

    del show_reasoning
    from fin_graph.harness.runtime import make_compat_analysis_runtime_async

    request = make_request(
        tickers,
        start_date,
        end_date,
        portfolio,
        selected_analysts,
        model_name,
        model_provider,
    )
    native = to_native_analysis_request(request)
    graph, _context = await make_compat_analysis_runtime_async()
    state = await graph.ainvoke(
        {
            "request": native.model_dump(mode="json"),
            "portfolio": (
                native.portfolio.model_dump(mode="json") if native.portfolio is not None else None
            ),
        }
    )
    result = state.get("result") if isinstance(state, dict) else None
    if not isinstance(result, dict):
        raise RuntimeError("analysis did not produce a compatible result")
    return result


def run_hedge_fund_sync(
    tickers: Sequence[str],
    start_date: str,
    end_date: str,
    portfolio: Mapping[str, Any],
    show_reasoning: bool = False,
    selected_analysts: Sequence[str] | None = None,
    model_name: str = "gpt-4.1",
    model_provider: str = "OpenAI",
) -> dict[str, Any]:
    """Synchronous historical entrypoint; async callers use ``run_hedge_fund_async``."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            run_hedge_fund_async(
                tickers,
                start_date,
                end_date,
                portfolio,
                show_reasoning,
                selected_analysts,
                model_name,
                model_provider,
            )
        )
    raise RuntimeError("run_hedge_fund is synchronous; await run_hedge_fund_async instead")


@dataclass(frozen=True)
class LegacyWorkflow:
    """Small adapter preserving ``create_workflow(...).compile().invoke(...)``."""

    selected_analysts: tuple[str, ...] | None = None

    def compile(self) -> LegacyWorkflow:
        return self

    def invoke(self, state: Mapping[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        data = state.get("data")
        metadata = state.get("metadata")
        if not isinstance(data, Mapping):
            raise ValueError("legacy workflow state requires data")
        if not isinstance(metadata, Mapping):
            metadata = {}
        result = run_hedge_fund_sync(
            list(data.get("tickers", ())),
            str(data.get("start_date", "")),
            str(data.get("end_date", "")),
            data.get("portfolio", {}) if isinstance(data.get("portfolio"), Mapping) else {},
            bool(metadata.get("show_reasoning", False)),
            self.selected_analysts,
            str(metadata.get("model_name", "gpt-4.1")),
            str(metadata.get("model_provider", "OpenAI")),
        )
        return {
            "messages": [json.dumps(result.get("decisions", {}))],
            "data": {**dict(data), "analyst_signals": result.get("analyst_signals", {})},
            "metadata": dict(metadata),
        }


def create_workflow(selected_analysts: Sequence[str] | None = None) -> LegacyWorkflow:
    return LegacyWorkflow(tuple(selected_analysts) if selected_analysts else None)


def start(state: Any) -> Any:
    return state
