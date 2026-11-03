"""Schema-versioned atomic JSON cache for async market-data providers."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from fin_graph.quant.data import MarketDataClient

CACHE_SCHEMA_VERSION = 1
T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


class CacheCorruptionError(RuntimeError):
    """A cache entry exists but is not valid for the current schema."""


def _json_value(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def canonical_cache_key(
    method: str, params: Mapping[str, object], *, namespace: str = "default"
) -> str:
    """Content key independent of mapping insertion or keyword order."""

    canonical = json.dumps(_json_value(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{namespace}|{method}|{canonical}".encode()).hexdigest()


class JsonFileCache:
    """Small file cache whose entries appear atomically after complete validation."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def read(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheCorruptionError(f"corrupt cache entry {key}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise CacheCorruptionError(f"incompatible cache entry {key}")
        return value

    def write(self, key: str, payload: Mapping[str, object]) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        document = {"schema_version": CACHE_SCHEMA_VERSION, **payload}
        try:
            temporary.write_text(
                json.dumps(document, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class CachedMarketData:
    """Market-data decorator that caches only successfully returned values."""

    def __init__(self, client: MarketDataClient, cache: JsonFileCache, namespace: str) -> None:
        self.client = client
        self.cache = cache
        self.namespace = namespace

    async def _models(
        self,
        method: str,
        params: Mapping[str, object],
        model: type[M],
        load: Callable[[], Awaitable[list[M]]],
    ) -> list[M]:
        key = canonical_cache_key(method, params, namespace=self.namespace)
        hit = self.cache.read(key)
        if hit is not None:
            try:
                rows = hit["data"]
                if not isinstance(rows, list):
                    raise TypeError("data is not a list")
                return [model.model_validate(row) for row in rows]
            except (KeyError, TypeError, ValidationError) as exc:
                raise CacheCorruptionError(f"invalid cached {method} payload") from exc
        result = await load()
        self.cache.write(
            key,
            {"kind": method, "data": [item.model_dump(mode="json") for item in result]},
        )
        return result

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        params = {"ticker": ticker, "start": start.isoformat(), "end": end.isoformat()}
        return await self._models(
            "prices", params, Price, lambda: self.client.prices(ticker, start, end)
        )

    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]:
        params = {
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "period": period,
            "limit": limit,
        }
        return await self._models(
            "financial_metrics",
            params,
            FinancialMetrics,
            lambda: self.client.financial_metrics(ticker, as_of, period=period, limit=limit),
        )

    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]:
        params = {
            "ticker": ticker,
            "names": sorted(names),
            "as_of": as_of.isoformat(),
            "period": period,
            "limit": limit,
        }
        return await self._models(
            "line_items",
            params,
            LineItem,
            lambda: self.client.line_items(ticker, names, as_of, period=period, limit=limit),
        )

    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]:
        params = {
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "start": start.isoformat() if start else None,
            "limit": limit,
        }
        return await self._models(
            "insider_trades",
            params,
            InsiderTrade,
            lambda: self.client.insider_trades(ticker, as_of, start=start, limit=limit),
        )

    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]:
        params = {
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "start": start.isoformat() if start else None,
            "limit": limit,
        }
        return await self._models(
            "company_news",
            params,
            CompanyNews,
            lambda: self.client.company_news(ticker, as_of, start=start, limit=limit),
        )

    async def market_cap(self, ticker: str, as_of: date) -> float | None:
        params = {"ticker": ticker, "as_of": as_of.isoformat()}
        key = canonical_cache_key("market_cap", params, namespace=self.namespace)
        hit = self.cache.read(key)
        if hit is not None:
            value = hit.get("data")
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int | float)
            ):
                raise CacheCorruptionError("invalid cached market_cap payload")
            return float(value) if value is not None else None
        result = await self.client.market_cap(ticker, as_of)
        self.cache.write(key, {"kind": "market_cap", "data": result})
        return result
