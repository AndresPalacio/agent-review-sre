"""Registry-backed v2 prediction and training compatibility routes."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from fin_graph.compat.ai_hedge_fund.adapters import from_native_signal
from fin_graph.compat.ai_hedge_fund.schemas import (
    SignalResponse,
    TrainRequest,
    TrainResponse,
    TrainResult,
)
from fin_graph.quant.artifacts import ArtifactStore, ModelArtifact, TrainingRequest
from fin_graph.quant.data import MarketDataClient
from fin_graph.quant.signals import alpha_model_registry

router = APIRouter(prefix="/v2")
_TRAINABLE = frozenset({"arima", "lstm", "technical_ml", "technical_scan"})


def _date(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "date must be a valid YYYY-MM-DD date") from exc


def _data(request: Request) -> MarketDataClient:
    value = getattr(request.app.state, "compatibility_quant_data", None)
    if isinstance(value, MarketDataClient):
        return value
    factory = getattr(request.app.state, "compatibility_context_factory", None)
    if callable(factory):
        from fin_graph.integrations.analytics.engine import AnalyticsEngine
        from fin_graph.integrations.market_data import MarketDataClient as IntegrationMarketData

        context = factory()
        engine = context.client("analytics", AnalyticsEngine)
        value = context.clients.get(engine.data_integration)
        if isinstance(value, IntegrationMarketData):
            request.app.state.compatibility_quant_data = value
            return value
    raise HTTPException(503, "quantitative data service unavailable")


def _model(key: str, request: Request) -> Any:
    instances = getattr(request.app.state, "compatibility_signal_models", None)
    if not isinstance(instances, dict):
        instances = {}
        request.app.state.compatibility_signal_models = instances
    if key in instances:
        return instances[key]
    factory = alpha_model_registry().get(key)
    if factory is None:
        available = sorted(alpha_model_registry())
        raise HTTPException(404, f"Unknown v2 model '{key}'. Available: {available}")
    try:
        if key == "buffett":
            llm = getattr(request.app.state, "compatibility_quant_llm", None)
            if llm is None:
                raise HTTPException(503, "Language model for buffett is unavailable")
            instances[key] = factory(model=llm)
        else:
            instances[key] = factory()
    except HTTPException:
        raise
    except (ImportError, TypeError) as exc:
        raise HTTPException(503, f"Optional dependencies for {key} are unavailable") from exc
    return instances[key]


@router.get("/{model}/predict", response_model=SignalResponse)
async def predict(
    model: str,
    ticker: str,
    request: Request,
    date: str | None = None,
) -> SignalResponse:
    symbol = ticker.strip().upper()
    if not symbol:
        raise HTTPException(422, "ticker must not be blank")
    instance = _model(model, request)
    try:
        signal = await instance.predict(symbol, _date(date), _data(request))
    except HTTPException:
        raise
    except ImportError as exc:
        raise HTTPException(503, f"Optional dependencies for {model} are unavailable") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to predict {model}: {type(exc).__name__}") from exc
    return from_native_signal(signal)


@router.post("/{model}/train", response_model=TrainResponse)
async def train(model: str, body: TrainRequest, request: Request) -> TrainResponse:
    if model not in _TRAINABLE:
        raise HTTPException(
            404,
            f"Unknown or non-trainable v2 model '{model}'. Available: {sorted(_TRAINABLE)}",
        )
    instance = _model(model, request)
    training = getattr(instance, "train", None)
    if not callable(training):
        raise HTTPException(
            404,
            f"Unknown or non-trainable v2 model '{model}'. Available: {sorted(_TRAINABLE)}",
        )
    as_of = _date(body.date)
    configured_root = getattr(
        request.app.state, "compatibility_quant_artifact_dir", ".v2_cache/models"
    )
    root = await asyncio.to_thread(lambda: Path(configured_root).resolve())
    store = ArtifactStore(root)
    data = _data(request)
    results: list[TrainResult] = []
    for raw_ticker in body.tickers:
        ticker = raw_ticker.strip().upper()
        if not ticker:
            results.append(TrainResult(ticker=raw_ticker, success=False, error="blank ticker"))
            continue
        try:
            artifact = await training(
                TrainingRequest(
                    ticker=ticker,
                    start_date=as_of - timedelta(days=730),
                    end_date=as_of,
                ),
                data,
                store,
            )
            parameters = getattr(artifact, "parameters", {})
            record: dict[str, Any] = {}
            if isinstance(artifact, ModelArtifact):
                payload = ArtifactStore.load(artifact)
                if isinstance(payload, dict) and isinstance(payload.get("record"), dict):
                    record = payload["record"]
            results.append(
                TrainResult(
                    ticker=ticker,
                    success=True,
                    trained_through=str(getattr(artifact, "trained_through", as_of)),
                    accuracy=record.get("accuracy", parameters.get("accuracy")),
                    precision=record.get("precision", parameters.get("precision")),
                    rmse=record.get("rmse", parameters.get("rmse")),
                    n_train=record.get("n_train", parameters.get("n_train")),
                    n_test=record.get("n_test", parameters.get("n_test")),
                )
            )
        except ImportError as exc:
            raise HTTPException(503, f"Optional dependencies for {model} are unavailable") from exc
        except Exception as exc:
            results.append(
                TrainResult(ticker=ticker, success=False, error=f"{type(exc).__name__}: {exc}")
            )
    return TrainResponse(results=results)
