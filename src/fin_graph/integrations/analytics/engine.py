"""Fetch what each analyst needs and score it, once per run.

A fetch that fails becomes a data gap and the score uses whatever did arrive. Only when nothing
usable arrived and something failed does the engine raise, so a provider outage surfaces as a
failed tool call (evidence with an error) instead of a confident-looking empty score.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypeVar

from fin_graph.domain.market import Price
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.quant import buffett, fundamentals, graham, sentiment, technicals, valuation
from fin_graph.domain.quant.common import QuantScore
from fin_graph.domain.quant.risk import PositionLimit, position_limits
from fin_graph.domain.tools import Domain
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.market_data import ANALYST_LINE_ITEMS, MarketDataClient, RunMemo

#: ``MARKET_DATA_PROVIDER`` value → the integration id whose client serves it.
PROVIDER_INTEGRATIONS = {
    "yahoo": "yahoo",
    "financial_datasets": "financial_datasets",
    "fixtures": "market_fixtures",
}
DEFAULT_LOOKBACK_DAYS = 365
#: Insider filings and headlines older than this say little about today's sentiment.
SENTIMENT_WINDOW_DAYS = 365

T = TypeVar("T")


@dataclass
class _Fetch:
    """Collects data gaps while fetching; ``failed`` counts provider errors."""

    gaps: list[str]
    failed: int = 0

    async def __call__(self, label: str, call: Awaitable[T], empty: T) -> T:
        try:
            return await call
        except IntegrationError as exc:
            self.gaps.append(f"{label} unavailable: {exc}")
            self.failed += 1
            return empty


class AnalyticsEngine:
    """Scores per ``(analyst, ticker, as_of)``, cached for the run; reads one data provider."""

    def __init__(self, provider: str = "yahoo", *, risk_free_rate: float = 0.045) -> None:
        if provider not in PROVIDER_INTEGRATIONS:
            raise ValueError(f"unknown market data provider: {provider}")
        self.provider = provider
        self.risk_free_rate = risk_free_rate
        self._memo = RunMemo()

    @property
    def data_integration(self) -> str:
        """The integration id whose client this engine reads."""
        return PROVIDER_INTEGRATIONS[self.provider]

    async def score(
        self,
        analyst: Domain,
        ticker: str,
        as_of: date,
        data: MarketDataClient,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> QuantScore:
        """The analyst's deterministic score; raises only when the provider gave nothing at all."""
        key = (analyst, ticker, as_of, lookback_days if analyst is Domain.TECHNICALS else 0)
        return await self._memo.get(
            key, lambda: self._score(analyst, ticker, as_of, data, lookback_days)
        )

    async def _score(
        self, analyst: Domain, ticker: str, as_of: date, data: MarketDataClient, lookback_days: int
    ) -> QuantScore:
        fetch = _Fetch(gaps=[])
        if analyst is Domain.FUNDAMENTALS:
            metrics = await fetch("metrics", data.financial_metrics(ticker, as_of, limit=10), [])
            result = fundamentals.score_fundamentals(ticker, metrics)
        elif analyst is Domain.VALUATION:
            metrics = await fetch("metrics", data.financial_metrics(ticker, as_of, limit=8), [])
            items = await fetch(
                "line items",
                data.line_items(ticker, ANALYST_LINE_ITEMS, as_of, period="ttm", limit=8),
                [],
            )
            cap = await fetch("market cap", data.market_cap(ticker, as_of), None)
            result = valuation.score_valuation(
                ticker, metrics, items, cap, risk_free_rate=self.risk_free_rate
            )
        elif analyst is Domain.TECHNICALS:
            start = as_of - timedelta(days=lookback_days)
            bars = await fetch("prices", data.prices(ticker, start, as_of), [])
            result = technicals.score_technicals(ticker, bars)
        elif analyst is Domain.SENTIMENT:
            start = as_of - timedelta(days=SENTIMENT_WINDOW_DAYS)
            trades = await fetch(
                "insider trades", data.insider_trades(ticker, as_of, start=start), []
            )
            news = await fetch("news", data.company_news(ticker, as_of, start=start, limit=100), [])
            result = sentiment.score_sentiment(ticker, trades, news)
        elif analyst in (Domain.BUFFETT, Domain.GRAHAM):
            period = "ttm" if analyst is Domain.BUFFETT else "annual"
            metrics = await fetch(
                "metrics", data.financial_metrics(ticker, as_of, period=period, limit=10), []
            )
            items = await fetch(
                "line items",
                data.line_items(ticker, ANALYST_LINE_ITEMS, as_of, period="annual", limit=10),
                [],
            )
            cap = await fetch("market cap", data.market_cap(ticker, as_of), None)
            scorer = buffett.score_buffett if analyst is Domain.BUFFETT else graham.score_graham
            result = scorer(ticker, metrics, items, cap)
        else:
            raise IntegrationError(f"{analyst.value} is not a financial analyst")
        if fetch.failed and result.degraded:
            raise IntegrationError(f"{analyst.value} for {ticker}: " + "; ".join(fetch.gaps))
        if fetch.gaps:
            result = result.model_copy(update={"data_gaps": [*fetch.gaps, *result.data_gaps]})
        return result

    async def position_limits(
        self,
        tickers: Sequence[str],
        as_of: date,
        data: MarketDataClient,
        portfolio: Portfolio,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> dict[str, PositionLimit]:
        """Risk limits for ``tickers`` against ``portfolio``, from prices up to ``as_of``.

        Tickers already held are priced too, so their value and correlation count. A ticker
        whose prices fail gets the zero limit the risk model gives missing data.
        """
        start = as_of - timedelta(days=lookback_days)
        prices: dict[str, list[Price]] = {}
        for ticker in dict.fromkeys([*tickers, *portfolio.positions]):
            try:
                prices[ticker] = await data.prices(ticker, start, as_of)
            except IntegrationError:
                prices[ticker] = []
        return position_limits(list(tickers), prices, portfolio)


def summarize(score: QuantScore) -> Mapping[str, object]:
    """What the model reads from a score: verdict, parts and gaps, without the raw metrics."""
    return {
        "ticker": score.ticker,
        "analyst": score.analyst.value,
        "signal": score.signal.value,
        "confidence": score.confidence,
        "score": score.score,
        "max_score": score.max_score,
        "components": [
            {
                "name": item.name,
                "signal": item.signal.value,
                **({"score": item.score} if item.score is not None else {}),
                **({"confidence": item.confidence} if item.confidence is not None else {}),
                **(
                    {"details": item.details[:300]}
                    if item.details
                    else {"metrics": _rounded(item.metrics)}
                ),
            }
            for item in score.components
        ],
        "data_gaps": score.data_gaps[:8],
    }


def _rounded(metrics: Mapping[str, object]) -> dict[str, object]:
    """Numbers to four significant decimals so the model reads them without noise."""
    return {
        key: round(value, 4) if isinstance(value, float) else value
        for key, value in metrics.items()
    }
