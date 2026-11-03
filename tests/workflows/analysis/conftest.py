"""An offline analysis run: synthetic companies as market fixtures and scripted services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from tests.domain.quant.synthetic import (
    COMPANIES,
    bars,
    company_metrics,
    insider_trades,
    line_items,
    news,
)

from fin_graph.domain.analysis import AnalysisPlan
from fin_graph.domain.orders import OrderAction, PortfolioDecision
from fin_graph.domain.quant.risk import PositionLimit
from fin_graph.domain.thesis import InvestmentThesis
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import StaticMarketData
from fin_graph.workflows.analysis.services import UnavailableAnalysisServices

AS_OF = date(2024, 11, 15)
NOW = datetime(2024, 11, 16, 12, tzinfo=UTC)


class Tools:
    """The analytics tools, as the registry would offer them."""

    def tools(self, context: RunContext) -> list[BaseTool]:
        return list(ANALYTICS_TOOLS)


def market() -> StaticMarketData:
    data: dict[str, dict[str, Any]] = {
        "prices": {},
        "metrics": {},
        "line_items": {},
        "insider_trades": {},
        "news": {},
        "market_caps": {},
    }
    for seed, name in enumerate(("deep_value", "distressed"), start=3):
        ticker, _, market_cap = COMPANIES[name]
        # Prices end on AS_OF and, for DEEP, sit near the synthetic market cap per share (6.0).
        data["prices"][ticker] = [
            bar.model_copy(
                update={
                    "open": bar.open / 25,
                    "high": bar.high / 25,
                    "low": bar.low / 25,
                    "close": bar.close / 25,
                }
            )
            for bar in bars(n=320, seed=seed)
        ]
        data["metrics"][ticker] = company_metrics(name)
        data["line_items"][ticker] = line_items(name)
        data["insider_trades"][ticker] = insider_trades("TSLA")
        data["news"][ticker] = news("TSLA", rotate_labels=True)
        data["market_caps"][ticker] = market_cap
    return StaticMarketData(**data)


def run_context() -> RunContext:
    return RunContext(
        clients={"analytics": AnalyticsEngine("fixtures"), "market_fixtures": market()}
    )


class ScriptedServices(UnavailableAnalysisServices):
    """Deterministic analysts; the plan, thesis and decision can be scripted per test."""

    def __init__(
        self,
        *,
        plan: AnalysisPlan | None = None,
        theses: Mapping[str, InvestmentThesis] | None = None,
        decision: PortfolioDecision | None = None,
    ) -> None:
        self._plan, self._theses, self._decision = plan, dict(theses or {}), decision
        self.synthesized: list[tuple[str, list[str]]] = []
        self.decided: list[dict[OrderAction, int]] = []

    async def plan(self, *args: Any, **kwargs: Any) -> AnalysisPlan:
        if self._plan is None:
            return await super().plan(*args, **kwargs)
        return self._plan

    async def synthesize(
        self,
        ticker: str,
        request: Any,
        plan: Any,
        findings: Sequence[Any],
        evidence: Sequence[Any],
        context: Any,
    ) -> InvestmentThesis:
        self.synthesized.append((ticker, list(context.previous_problems)))
        if ticker not in self._theses:
            return await super().synthesize(ticker, request, plan, findings, evidence, context)
        return self._theses[ticker]

    async def decide(
        self,
        thesis: InvestmentThesis,
        verified: bool,
        allowed: Mapping[OrderAction, int],
        limit: PositionLimit,
    ) -> PortfolioDecision:
        self.decided.append(dict(allowed))
        if self._decision is None:
            return await super().decide(thesis, verified, allowed, limit)
        return self._decision


@pytest.fixture
def context() -> RunContext:
    return run_context()
