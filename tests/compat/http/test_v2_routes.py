from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI

from fin_graph.adapters.reference_app.http import reference_app_router
from fin_graph.adapters.reference_app.settings import ReferenceAppProfile
from fin_graph.domain.market import Price
from fin_graph.quant.signals.base import Signal


class FakeData:
    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        begin = max(start, end - timedelta(days=10))
        return [
            Price(
                ticker=ticker,
                time=datetime.combine(begin + timedelta(days=index), datetime.min.time(), UTC),
                open=100 + index,
                high=101 + index,
                low=99 + index,
                close=100 + index,
                volume=1000,
            )
            for index in range((end - begin).days + 1)
        ]

    async def financial_metrics(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def line_items(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def insider_trades(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def company_news(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def market_cap(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeModel:
    name = "technical_ml"

    async def predict(self, ticker: str, as_of: date, data: object) -> Signal:
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=0.25,
            reasoning="fixture",
            metadata={"source": "test"},
        )

    async def train(self, request: object, data: object, store: object) -> object:
        return SimpleNamespace(
            trained_through=date(2025, 1, 31),
            parameters={},
            artifact_id="artifact-1",
        )


@asynccontextmanager
async def client_for(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.reference_app_profile = ReferenceAppProfile()
    app.state.reference_app_store = None
    app.state.reference_app_quant_data = FakeData()
    app.state.reference_app_signal_models = {"technical_ml": FakeModel()}
    app.state.reference_app_quant_artifact_dir = tmp_path / "models"
    app.include_router(reference_app_router())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_predict_train_model_availability_and_validation(tmp_path: Path) -> None:
    async with client_for(tmp_path) as client:
        predicted = await client.get(
            "/v2/technical_ml/predict", params={"ticker": "aapl", "date": "2025-01-31"}
        )
        trained = await client.post(
            "/v2/technical_ml/train", json={"tickers": ["aapl"], "date": "2025-01-31"}
        )
        unknown = await client.get("/v2/missing/predict", params={"ticker": "AAPL"})
        view_only = await client.post(
            "/v2/buffett/train", json={"tickers": ["AAPL"], "date": "2025-01-31"}
        )
        bad_date = await client.get(
            "/v2/technical_ml/predict", params={"ticker": "AAPL", "date": "nope"}
        )

    assert predicted.json() == {
        "ticker": "AAPL",
        "date": "2025-01-31",
        "value": 0.25,
        "reasoning": "fixture",
        "metadata": {"source": "test"},
    }
    assert trained.json()["results"][0]["success"] is True
    assert unknown.status_code == view_only.status_code == 404
    assert bad_date.status_code == 422


async def test_var_and_algorithm_catalog_routes(tmp_path: Path) -> None:
    async with client_for(tmp_path) as client:
        risk = await client.get(
            "/v2/risk/var",
            params={
                "tickers": "AAPL,MSFT",
                "shares": "10,5",
                "date": "2025-01-31",
                "n_sims": 1000,
            },
        )
        algorithms = await client.get("/v2/algorithms")
        spec = await client.get("/v2/algorithms/sma_crossover")
        missing = await client.get("/v2/algorithms/not-a-real-algorithm")
        unsafe = await client.get("/v2/algorithms/%2E%2E%2Fsecret")

    assert risk.status_code == 200
    assert risk.json()["holdings"] == {"AAPL": 10.0, "MSFT": 5.0}
    assert [item["key"] for item in algorithms.json()] == [
        "sma_crossover",
        "rsi_mean_reversion",
        "momentum",
    ]
    assert spec.json()["parameters"]["fast_window"]["default"] == 20
    assert missing.status_code == unsafe.status_code == 404


async def test_var_rejects_mismatched_or_non_positive_holdings(tmp_path: Path) -> None:
    async with client_for(tmp_path) as client:
        missing = await client.get("/v2/risk/var", params={"tickers": "AAPL"})
        mismatch = await client.get("/v2/risk/var", params={"tickers": "AAPL,MSFT", "shares": "10"})
        negative = await client.get("/v2/risk/var", params={"tickers": "AAPL", "shares": "-1"})

    assert missing.status_code == mismatch.status_code == negative.status_code == 422
