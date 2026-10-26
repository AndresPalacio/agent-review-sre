"""Financial Datasets as a :class:`MarketDataClient`.

Ported from ai-hedge-fund ``src/data/providers/financialdatasets_provider.py`` (MIT). The API is
point in time already (``report_period_lte``, ``filing_date_lte``), so ``as_of`` maps onto those
filters. Rate limits back off for a few seconds instead of the original's minute-long waits, then
fail with a readable error.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from fin_graph.integrations.base import HttpStatusError, IntegrationError
from fin_graph.integrations.http import HttpJsonClient
from fin_graph.integrations.market_data import MarketDataClient, RunMemo

BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0)


class FinancialDatasetsClient(MarketDataClient):
    provider = "financial_datasets"

    def __init__(
        self,
        api_key: str,
        *,
        url: str = "https://api.financialdatasets.ai",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff: Sequence[float] = BACKOFF_SECONDS,
    ) -> None:
        self.http = HttpJsonClient(url, {"X-API-KEY": api_key}, timeout_seconds, transport)
        self.backoff = tuple(backoff)
        self._memo = RunMemo()

    async def _call(
        self, method: str, path: str, params: Mapping[str, Any] | None = None, body: Any = None
    ) -> Any:
        for delay in (*self.backoff, None):
            try:
                return await self.http.request(method, path, params=params, json_body=body)
            except HttpStatusError as exc:
                if exc.status != 429 or delay is None:
                    if exc.status == 401:
                        raise IntegrationError("Financial Datasets rejected the API key") from exc
                    raise
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _rows[T: BaseModel](payload: Any, key: str, model: type[T], ticker: str) -> list[T]:
        rows = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        try:
            return [model.model_validate({"ticker": ticker, **row}) for row in rows]
        except (ValidationError, TypeError) as exc:
            raise IntegrationError(f"Financial Datasets returned malformed {key}") from exc

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        async def load() -> list[Price]:
            payload = await self._call(
                "GET",
                "/prices/",
                {
                    "ticker": ticker,
                    "interval": "day",
                    "interval_multiplier": 1,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            )
            bars = self._rows(payload, "prices", Price, ticker)
            return sorted(
                (bar for bar in bars if start <= bar.day <= end), key=lambda bar: bar.time
            )

        return await self._memo.get(("prices", ticker, start, end), load)

    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]:
        async def load() -> list[FinancialMetrics]:
            payload = await self._call(
                "GET",
                "/financial-metrics/",
                {
                    "ticker": ticker,
                    "report_period_lte": as_of.isoformat(),
                    "limit": limit,
                    "period": period,
                },
            )
            rows = self._rows(payload, "financial_metrics", FinancialMetrics, ticker)
            return [row for row in rows if row.report_period <= as_of][:limit]

        return await self._memo.get(("metrics", ticker, as_of, period, limit), load)

    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]:
        async def load() -> list[LineItem]:
            payload = await self._call(
                "POST",
                "/financials/search/line-items",
                body={
                    "tickers": [ticker],
                    "line_items": list(names),
                    "end_date": as_of.isoformat(),
                    "period": period,
                    "limit": limit,
                },
            )
            rows = self._rows(payload, "search_results", LineItem, ticker)
            return [row for row in rows if row.report_period <= as_of][:limit]

        return await self._memo.get(
            ("line_items", ticker, tuple(names), as_of, period, limit), load
        )

    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]:
        params: dict[str, Any] = {
            "ticker": ticker,
            "filing_date_lte": as_of.isoformat(),
            "limit": limit,
        }
        if start is not None:
            params["filing_date_gte"] = start.isoformat()
        payload = await self._memo.get(
            ("insiders", ticker, as_of, start, limit),
            lambda: self._call("GET", "/insider-trades/", params),
        )
        return self._rows(payload, "insider_trades", InsiderTrade, ticker)[:limit]

    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]:
        params: dict[str, Any] = {"ticker": ticker, "end_date": as_of.isoformat(), "limit": limit}
        if start is not None:
            params["start_date"] = start.isoformat()
        payload = await self._memo.get(
            ("news", ticker, as_of, start, limit), lambda: self._call("GET", "/news/", params)
        )
        rows = self._rows(payload, "news", CompanyNews, ticker)
        return [row for row in rows if row.date.date() <= as_of][:limit]

    async def market_cap(self, ticker: str, as_of: date) -> float | None:
        metrics = await self.financial_metrics(ticker, as_of)
        return metrics[0].market_cap if metrics and metrics[0].market_cap else None
