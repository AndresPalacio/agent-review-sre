from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fin_graph.domain.market import Price
from fin_graph.quant.cache import (
    CacheCorruptionError,
    CachedMarketData,
    JsonFileCache,
    canonical_cache_key,
)


class CountingData:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def prices(self, ticker: str, start: date, end: date):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return [
            Price(
                time=datetime(2024, 1, 1, tzinfo=UTC),
                open=1,
                high=2,
                low=1,
                close=2,
                volume=100,
            )
        ]

    async def financial_metrics(self, ticker: str, as_of: date, **kwargs):
        return []

    async def line_items(self, ticker: str, names, as_of: date, **kwargs):
        return []

    async def insider_trades(self, ticker: str, as_of: date, **kwargs):
        return []

    async def company_news(self, ticker: str, as_of: date, **kwargs):
        return []

    async def market_cap(self, ticker: str, as_of: date):
        return None


def test_cache_key_is_independent_of_keyword_order() -> None:
    assert canonical_cache_key("prices", {"ticker": "AAPL", "limit": 2}) == canonical_cache_key(
        "prices", {"limit": 2, "ticker": "AAPL"}
    )


async def test_warm_cache_rehydrates_models_and_skips_provider(tmp_path) -> None:
    inner = CountingData()
    cache = JsonFileCache(tmp_path)
    data = CachedMarketData(inner, cache, "unit")

    first = await data.prices("AAPL", date(2024, 1, 1), date(2024, 1, 2))
    second = await data.prices("AAPL", date(2024, 1, 1), date(2024, 1, 2))

    assert inner.calls == 1
    assert isinstance(second[0], Price)
    assert second == first
    assert not list(tmp_path.glob("*.tmp"))


async def test_corrupted_cache_fails_loud(tmp_path) -> None:
    cache = JsonFileCache(tmp_path)
    params = {"ticker": "AAPL", "start": "2024-01-01", "end": "2024-01-02"}
    key = canonical_cache_key("prices", params, namespace="unit")
    cache.path_for(key).parent.mkdir(parents=True, exist_ok=True)
    cache.path_for(key).write_text("{broken", encoding="utf-8")

    with pytest.raises(CacheCorruptionError):
        await CachedMarketData(CountingData(), cache, "unit").prices(
            "AAPL", date(2024, 1, 1), date(2024, 1, 2)
        )


async def test_infrastructure_failure_is_not_cached_as_empty(tmp_path) -> None:
    inner = CountingData(fail=True)
    data = CachedMarketData(inner, JsonFileCache(tmp_path), "unit")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await data.prices("AAPL", date(2024, 1, 1), date(2024, 1, 2))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await data.prices("AAPL", date(2024, 1, 1), date(2024, 1, 2))

    assert inner.calls == 2
    assert not list(tmp_path.glob("*.json"))
